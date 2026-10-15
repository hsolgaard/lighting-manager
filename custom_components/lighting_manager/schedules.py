"""Keep Scheduler (Niels Faber) schedules attached to a light that is replaced or renamed.

Scheduler stores the entity ID as plain text inside each schedule's actions
(and conditions) and does not follow entity ID changes. So when a light is
replaced, or its ID changes, its schedules would silently keep pointing at
the old light.

How this works, and why it is built this way:

- Scheduler has no public "read a schedule" call, but it keeps every schedule
  in .storage/scheduler.storage. We only READ that file (never write it).
  Writing is done through Scheduler's own public service, scheduler.edit, so
  Scheduler stays in charge of its data and re-arms its timers itself.
- The file is saved with a short delay. Before trusting a schedule we compare
  it with what the live switch.schedule_* entity reports (targets, number of
  time slots, on/off). If they disagree the schedule is left alone and
  reported, never guessed at.
- Before anything is edited, the affected schedules are copied to
  lighting_manager_backups/<time>/schedules.json.
- After the edit we check the live entity again, and report schedule by
  schedule what happened. Schedules are never deleted.
- The same check runs when the light KEEPS its ID: some Scheduler versions
  follow an entity rename, which would drag a schedule onto the old light's
  "_replaced" ID. We move it back.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant

from .references import BACKUP_DIR_NAME

_LOGGER = logging.getLogger(__name__)

STORAGE_PATH = ".storage/scheduler.storage"
SWITCH_PREFIX = "switch.schedule_"

# The fields scheduler.edit accepts for a time slot. Empty / null ones must be
# left out entirely: Scheduler rejects `conditions: []` and `stop: null`.
_SLOT_KEYS = ("start", "stop", "conditions", "condition_type", "track_conditions", "actions")


# -- reading Scheduler's data -------------------------------------------------


def _read_storage(path: str) -> list[dict[str, Any]] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return None
    schedules = (raw.get("data") or {}).get("schedules")
    return schedules if isinstance(schedules, list) else None


def _ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _action_entities(schedule: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for slot in schedule.get("timeslots") or []:
        for action in slot.get("actions") or []:
            found.update(_ids(action.get("entity_id")))
    return found


def _mentions(schedule: dict[str, Any], entity_id: str) -> bool:
    for slot in schedule.get("timeslots") or []:
        for action in slot.get("actions") or []:
            if entity_id in _ids(action.get("entity_id")):
                return True
        for cond in slot.get("conditions") or []:
            if cond.get("entity_id") == entity_id:
                return True
    return False


def _switch_id(schedule: dict[str, Any]) -> str:
    return f"{SWITCH_PREFIX}{schedule.get('schedule_id')}"


def _disagreement(schedule: dict[str, Any], state) -> str | None:
    """Why the saved file can't be trusted for this schedule right now (None = it matches)."""
    if state is None:
        return "its Scheduler entity isn't loaded"
    live = {e for e in (state.attributes.get("entities") or []) if isinstance(e, str)}
    if live != _action_entities(schedule):
        return "it was changed a moment ago and Scheduler hasn't saved it yet"
    live_slots = state.attributes.get("timeslots") or []
    if len(live_slots) != len(schedule.get("timeslots") or []):
        return "it was changed a moment ago and Scheduler hasn't saved it yet"
    if state.state not in ("on", "off") or (state.state == "on") != bool(schedule.get("enabled", True)):
        return "it was changed a moment ago and Scheduler hasn't saved it yet"
    return None


def _label(schedule: dict[str, Any] | None, state, switch_id: str) -> str:
    name = (schedule or {}).get("name") or (state.name if state else None)
    return name or switch_id


# -- rewriting ------------------------------------------------------------------


def _swap(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return new if value == old else value
    if isinstance(value, list):
        out = []
        for item in value:
            item = new if item == old else item
            if item not in out:
                out.append(item)
        return out
    return value


def _build_timeslots(schedule: dict[str, Any], old: str, new: str) -> list[dict[str, Any]]:
    """The schedule's time slots with old -> new, in the shape scheduler.edit accepts."""
    slots: list[dict[str, Any]] = []
    for slot in schedule.get("timeslots") or []:
        built: dict[str, Any] = {}
        for key in _SLOT_KEYS:
            value = copy.deepcopy(slot.get(key))
            if value is None or value == [] or value == "":
                continue
            if key == "actions":
                for action in value:
                    if "entity_id" in action:
                        action["entity_id"] = _swap(action["entity_id"], old, new)
            elif key == "conditions":
                for cond in value:
                    if "entity_id" in cond:
                        cond["entity_id"] = _swap(cond["entity_id"], old, new)
            built[key] = value
        slots.append(built)
    return slots


# -- read-only listing for the panel ---------------------------------------------


def _entity_actions(slot: dict[str, Any], entity_id: str) -> list[dict[str, Any]]:
    """The actions of one time slot that act on entity_id."""
    out = []
    for action in slot.get("actions") or []:
        ids = _ids(action.get("entity_id"))
        if not ids or entity_id in ids:
            out.append({"service": action.get("service", ""), "service_data": action.get("service_data") or {}})
    return out


def _rows_from_stored(schedule: dict[str, Any], switch_id: str, state) -> dict[str, dict[str, Any]]:
    """One row per light this schedule acts on, from the full saved data."""
    targets = {e for e in _action_entities(schedule) if e.split(".")[0] in ("light", "switch")}
    rows = {}
    for entity_id in targets:
        slots = []
        for slot in schedule.get("timeslots") or []:
            actions = _entity_actions(slot, entity_id)
            if not actions:
                continue
            slots.append(
                {
                    "start": slot.get("start"),
                    "stop": slot.get("stop"),
                    "actions": actions,
                    "has_conditions": bool(slot.get("conditions")),
                }
            )
        rows[entity_id] = {
            "schedule": switch_id,
            "name": schedule.get("name"),
            "enabled": state.state == "on",
            "weekdays": schedule.get("weekdays") or [],
            "repeat_type": schedule.get("repeat_type"),
            "slots": slots,
            "shared_with": sorted(targets - {entity_id}),
            "approximate": False,
        }
    return rows


def _rows_from_live(state) -> dict[str, dict[str, Any]]:
    """Best effort from the live entity alone (used when the saved file isn't trustworthy yet)."""
    attrs = state.attributes
    targets = {e for e in (attrs.get("entities") or []) if isinstance(e, str) and e.split(".")[0] in ("light", "switch")}
    services = [a.get("service", "") for a in (attrs.get("actions") or []) if isinstance(a, dict)]
    rows = {}
    for entity_id in targets:
        rows[entity_id] = {
            "schedule": state.entity_id,
            "name": None,
            "enabled": state.state == "on",
            "weekdays": list(attrs.get("weekdays") or []),
            "repeat_type": None,
            "slots": [
                {"start": start, "stop": None, "actions": [{"service": sv, "service_data": {}} for sv in services], "has_conditions": False}
                for start in (attrs.get("timeslots") or [])
            ],
            "shared_with": sorted(targets - {entity_id}),
            "approximate": True,
        }
    return rows


async def async_list_by_light(hass: HomeAssistant) -> dict[str, list[dict[str, Any]]]:
    """Every Scheduler schedule, grouped under the lights it acts on. Read-only."""
    stored = await hass.async_add_executor_job(_read_storage, hass.config.path(STORAGE_PATH))
    by_id = {s.get("schedule_id"): s for s in stored or []}
    result: dict[str, list[dict[str, Any]]] = {}
    for state in hass.states.async_all("switch"):
        if not state.entity_id.startswith(SWITCH_PREFIX):
            continue
        schedule = by_id.get(state.entity_id[len(SWITCH_PREFIX):])
        if schedule is not None and _disagreement(schedule, state) is None:
            rows = _rows_from_stored(schedule, state.entity_id, state)
        else:
            rows = _rows_from_live(state)
        for entity_id, row in rows.items():
            result.setdefault(entity_id, []).append(row)
    return result


# -- public entry points ----------------------------------------------------------


def _scan(hass: HomeAssistant, coordinator, entity_id: str, stored: list[dict] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for schedule in stored or []:
        if not _mentions(schedule, entity_id):
            continue
        switch_id = _switch_id(schedule)
        seen.add(switch_id)
        state = hass.states.get(switch_id)
        reason = _disagreement(schedule, state)
        rows.append(
            {
                "switch_id": switch_id,
                "name": _label(schedule, state, switch_id),
                "schedule": schedule if reason is None else None,
                "problem": reason,
            }
        )
    scheduler = getattr(coordinator, "scheduler", None)
    if scheduler is not None:
        for sched in scheduler.async_schedules_for_entity(entity_id):
            if sched.schedule_id in seen:
                continue
            state = hass.states.get(sched.schedule_id)
            rows.append(
                {
                    "switch_id": sched.schedule_id,
                    "name": _label(None, state, sched.schedule_id),
                    "schedule": None,
                    "problem": "Lighting Manager couldn't read its settings from Scheduler",
                }
            )
    return rows


async def async_snapshot(hass: HomeAssistant, coordinator, entity_id: str) -> list[dict[str, Any]]:
    """Schedules that use entity_id, captured BEFORE the light is changed."""
    stored = await hass.async_add_executor_job(_read_storage, hass.config.path(STORAGE_PATH))
    return _scan(hass, coordinator, entity_id, stored)


async def async_scan_rows(hass: HomeAssistant, coordinator, entity_id: str) -> list[dict[str, Any]]:
    """Rows for the 'what uses this ID' list in the dialogs."""
    rows = []
    for snap in await async_snapshot(hass, coordinator, entity_id):
        if snap["schedule"] is not None:
            note = "Moved to the new light automatically. The schedule keeps its days, times and on/off state."
        else:
            note = f"Can't be moved automatically ({snap['problem']}). Re-point it in the Scheduler card."
        rows.append(
            {
                "kind": "schedule",
                "label": f"Scheduler schedule: {snap['name']}",
                "count": 1,
                "auto": snap["schedule"] is not None,
                "always": True,
                "note": note,
            }
        )
    return rows


def _write_backup(config_dir: str, snapshots: list[dict[str, Any]]) -> str:
    folder = os.path.join(config_dir, BACKUP_DIR_NAME, datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "schedules.json"), "w", encoding="utf-8") as handle:
        json.dump([s["schedule"] for s in snapshots], handle, indent=2)
    return os.path.relpath(folder, config_dir)


def _live_entities(hass: HomeAssistant, switch_id: str) -> set[str] | None:
    state = hass.states.get(switch_id)
    if state is None:
        return None
    return {e for e in (state.attributes.get("entities") or []) if isinstance(e, str)}


async def _wait_for(hass: HomeAssistant, switch_id: str, wanted: set[str]) -> bool:
    for _ in range(10):
        if _live_entities(hass, switch_id) == wanted:
            return True
        await asyncio.sleep(0.5)
    return _live_entities(hass, switch_id) == wanted


async def async_restore(
    hass: HomeAssistant, snapshots: list[dict[str, Any]], old_id: str, final_id: str
) -> dict[str, Any]:
    """Make every snapshotted schedule point at final_id. Safe to run when nothing needs doing."""
    results: list[dict[str, Any]] = []
    todo: list[dict[str, Any]] = []

    for snap in snapshots:
        row = {"schedule": snap["switch_id"], "name": snap["name"]}
        schedule = snap["schedule"]
        if schedule is None:
            results.append({**row, "status": "manual", "detail": snap["problem"]})
            continue
        wanted = {final_id if e == old_id else e for e in _action_entities(schedule)}
        if _live_entities(hass, snap["switch_id"]) == wanted:
            results.append({**row, "status": "unchanged", "detail": "Already points at the right light."})
            continue
        todo.append({**snap, "row": row, "wanted": wanted})

    backup_dir = None
    if todo:
        try:
            backup_dir = await hass.async_add_executor_job(_write_backup, hass.config.config_dir, todo)
        except OSError as err:
            # Without a safety copy we don't touch anything.
            for item in todo:
                results.append(
                    {**item["row"], "status": "failed", "detail": f"Couldn't save a backup first ({err}), so it was left alone."}
                )
            todo = []

    for item in todo:
        row = item["row"]
        if not hass.services.has_service("scheduler", "edit"):
            results.append({**row, "status": "failed", "detail": "Scheduler's edit service isn't available."})
            continue
        payload = {
            "entity_id": item["switch_id"],
            "timeslots": _build_timeslots(item["schedule"], old_id, final_id),
        }
        try:
            await hass.services.async_call("scheduler", "edit", payload, blocking=True)
        except Exception as err:  # noqa: BLE001 - any failure is reported, never raised
            _LOGGER.warning("Could not re-point %s: %s", item["switch_id"], err)
            results.append({**row, "status": "failed", "detail": f"Scheduler refused the change: {err}"})
            continue
        if await _wait_for(hass, item["switch_id"], item["wanted"]):
            results.append({**row, "status": "moved", "detail": f"Now uses {final_id}."})
        else:
            results.append(
                {**row, "status": "failed", "detail": "The change was sent but Scheduler doesn't show it. Check it in the Scheduler card."}
            )

    return {
        "schedules": results,
        "moved": sum(1 for r in results if r["status"] == "moved"),
        "unchanged": sum(1 for r in results if r["status"] == "unchanged"),
        "manual": [r for r in results if r["status"] in ("manual", "failed")],
        "backup_dir": backup_dir,
    }
