"""Scheduler schedules follow a light that is replaced or renamed (schedules.py)."""
from __future__ import annotations

import json
import os

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import entity_registry as er

from custom_components.lighting_manager import schedules
from custom_components.lighting_manager.coordinator import LightingManagerCoordinator

from .test_coordinator import _adopted_old_light, _register_light, coordinator  # noqa: F401


@pytest.fixture(autouse=True)
def _clean_scheduler_storage(hass: HomeAssistant):
    path = os.path.join(hass.config.config_dir, ".storage", "scheduler.storage")
    if os.path.exists(path):
        os.remove(path)
    yield


def _slot(start, entity_id, service="light.turn_on", extra_actions=(), conditions=None):
    return {
        "start": start,
        "stop": None,
        "conditions": conditions or [],
        "condition_type": None,
        "track_conditions": False,
        "actions": [{"service": service, "entity_id": entity_id, "service_data": {}}, *extra_actions],
    }


def _schedule(schedule_id, slots, *, enabled=True, weekdays=("mon", "wed", "sat")):
    return {
        "schedule_id": schedule_id,
        "timeslots": slots,
        "weekdays": list(weekdays),
        "start_date": None,
        "end_date": None,
        "repeat_type": "repeat",
        "name": None,
        "enabled": enabled,
    }


def _install(hass: HomeAssistant, stored: list[dict], *, live_override: dict | None = None) -> list[dict]:
    """Write scheduler.storage and expose matching switch.schedule_* entities."""
    folder = os.path.join(hass.config.config_dir, ".storage")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "scheduler.storage"), "w") as handle:
        json.dump({"version": 3, "key": "scheduler.storage", "data": {"schedules": stored}}, handle)
    for sched in stored:
        entities = sorted(schedules._action_entities(sched))
        attrs = {
            "entities": entities,
            "weekdays": sched["weekdays"],
            "timeslots": [s["start"] for s in sched["timeslots"]],
            "actions": [{"service": "light.turn_on"}],
        }
        attrs.update((live_override or {}).get(sched["schedule_id"], {}))
        hass.states.async_set(f"switch.schedule_{sched['schedule_id']}", "on" if sched["enabled"] else "off", attrs)
    return stored


def _fake_scheduler_edit(hass: HomeAssistant, *, apply: bool = True, fail: bool = False) -> list[dict]:
    calls: list[dict] = []

    async def _edit(call: ServiceCall) -> None:
        calls.append(dict(call.data))
        if fail:
            raise ValueError("length of value must be at least 1")
        if not apply:
            return
        switch_id = call.data["entity_id"]
        state = hass.states.get(switch_id)
        entities = sorted(
            {
                e
                for slot in call.data["timeslots"]
                for a in slot["actions"]
                for e in ([a["entity_id"]] if isinstance(a["entity_id"], str) else a["entity_id"])
            }
        )
        hass.states.async_set(switch_id, state.state, {**state.attributes, "entities": entities})

    hass.services.async_register("scheduler", "edit", _edit)
    return calls


def _follow_renames(hass: HomeAssistant) -> None:
    """Simulate a Scheduler build that rewrites schedules when an entity ID is renamed."""

    @callback
    def _on_rename(event) -> None:
        old, new = event.data.get("old_entity_id"), event.data.get("entity_id")
        if event.data.get("action") != "update" or not old or old == new:
            return
        for state in list(hass.states.async_all("switch")):
            ents = state.attributes.get("entities") or []
            if old in ents:
                hass.states.async_set(
                    state.entity_id, state.state, {**state.attributes, "entities": [new if e == old else e for e in ents]}
                )

    hass.bus.async_listen("entity_registry_updated", _on_rename)


async def test_replace_with_new_id_moves_schedules_and_leaves_others(hass, coordinator):  # noqa: F811
    old, _ = await _adopted_old_light(hass, coordinator, state="on")
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")
    _install(
        hass,
        [
            _schedule("aaa111", [_slot("10:11:00", old)], enabled=False),
            _schedule("bbb222", [_slot("18:00:00", "light.somewhere_else")]),
        ],
    )
    calls = _fake_scheduler_edit(hass)

    result = await coordinator.async_replace_light(old, new, use_new_id=True)

    final = result["entity_id"]
    assert final == new
    assert len(calls) == 1
    payload = calls[0]
    assert payload["entity_id"] == "switch.schedule_aaa111"
    slot = payload["timeslots"][0]
    assert slot["start"] == "10:11:00"
    assert slot["actions"][0]["entity_id"] == new
    # Empty / null fields must be left out or Scheduler rejects the call.
    assert "conditions" not in slot and "stop" not in slot and "condition_type" not in slot
    report = result["schedules"]
    assert report["moved"] == 1 and report["manual"] == []
    assert hass.states.get("switch.schedule_aaa111").state == "off"  # on/off untouched
    assert hass.states.get("switch.schedule_bbb222").attributes["entities"] == ["light.somewhere_else"]
    backup = os.path.join(hass.config.config_dir, report["backup_dir"], "schedules.json")
    assert json.load(open(backup))[0]["schedule_id"] == "aaa111"


async def test_keep_id_does_nothing_when_scheduler_stays_put(hass, coordinator):  # noqa: F811
    old, _ = await _adopted_old_light(hass, coordinator, state="on")
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")
    _install(hass, [_schedule("aaa111", [_slot("10:11:00", old)])])
    calls = _fake_scheduler_edit(hass)

    result = await coordinator.async_replace_light(old, new)

    assert calls == []
    assert result["schedules"]["unchanged"] == 1


async def test_keep_id_moves_schedule_back_when_scheduler_follows_the_aside_rename(hass, coordinator):  # noqa: F811
    old, _ = await _adopted_old_light(hass, coordinator, state="on")
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")
    _install(hass, [_schedule("aaa111", [_slot("10:11:00", old)])])
    _follow_renames(hass)
    calls = _fake_scheduler_edit(hass)

    result = await coordinator.async_replace_light(old, new)

    assert len(calls) == 1
    assert calls[0]["timeslots"][0]["actions"][0]["entity_id"] == old
    assert hass.states.get("switch.schedule_aaa111").attributes["entities"] == [old]
    assert result["schedules"]["moved"] == 1


async def test_only_matching_action_and_condition_are_changed(hass, coordinator):  # noqa: F811
    old, _ = await _adopted_old_light(hass, coordinator, state="on")
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")
    other_action = {"service": "input_boolean.turn_on", "entity_id": "input_boolean.guest", "service_data": {}}
    cond = [{"entity_id": old, "attribute": "state", "match_type": "is", "value": "on", "value_type": "string"}]
    slot = _slot("07:00:00", old, extra_actions=[other_action], conditions=cond)
    slot["condition_type"] = "and"
    _install(hass, [_schedule("ccc333", [slot])])
    calls = _fake_scheduler_edit(hass)

    await coordinator.async_replace_light(old, new, use_new_id=True)

    built = calls[0]["timeslots"][0]
    assert built["actions"][0]["entity_id"] == new
    assert built["actions"][1]["entity_id"] == "input_boolean.guest"
    assert built["conditions"][0]["entity_id"] == new
    assert built["condition_type"] == "and"


async def test_schedule_the_file_disagrees_with_is_left_alone_and_reported(hass, coordinator):  # noqa: F811
    old, _ = await _adopted_old_light(hass, coordinator, state="on")
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")
    # The live entity already points somewhere else: the saved file is out of date.
    _install(
        hass,
        [_schedule("aaa111", [_slot("10:11:00", old)])],
        live_override={"aaa111": {"entities": ["light.other"]}},
    )
    calls = _fake_scheduler_edit(hass)

    result = await coordinator.async_replace_light(old, new, use_new_id=True)

    assert calls == []
    assert result["entity_id"] == new  # the replace itself still succeeds
    manual = result["schedules"]["manual"]
    assert [m["schedule"] for m in manual] == ["switch.schedule_aaa111"]
    assert "Scheduler hasn't saved" in manual[0]["detail"]


async def test_scheduler_refusing_the_edit_is_reported_not_raised(hass, coordinator):  # noqa: F811
    old, _ = await _adopted_old_light(hass, coordinator, state="on")
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")
    _install(hass, [_schedule("aaa111", [_slot("10:11:00", old)])])
    _fake_scheduler_edit(hass, fail=True)

    result = await coordinator.async_replace_light(old, new, use_new_id=True)

    assert result["entity_id"] == new
    assert result["schedules"]["moved"] == 0
    assert result["schedules"]["manual"][0]["status"] == "failed"


async def test_change_without_effect_in_scheduler_is_flagged(hass, coordinator):  # noqa: F811
    old, _ = await _adopted_old_light(hass, coordinator, state="on")
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")
    _install(hass, [_schedule("aaa111", [_slot("10:11:00", old)])])
    _fake_scheduler_edit(hass, apply=False)
    schedules_mod_sleep = schedules.asyncio.sleep

    async def _fast(_):
        return None

    schedules.asyncio.sleep = _fast
    try:
        result = await coordinator.async_replace_light(old, new, use_new_id=True)
    finally:
        schedules.asyncio.sleep = schedules_mod_sleep

    assert result["schedules"]["manual"][0]["status"] == "failed"


async def test_change_entity_id_moves_schedules(hass, coordinator):  # noqa: F811
    old, _ = await _adopted_old_light(hass, coordinator, state="on")
    _install(hass, [_schedule("aaa111", [_slot("10:11:00", old)])])
    calls = _fake_scheduler_edit(hass)

    result = await coordinator.async_change_entity_id(old, "light.zb_kitchen_lamp", False)

    assert result["schedules"]["moved"] == 1
    assert calls[0]["timeslots"][0]["actions"][0]["entity_id"] == "light.zb_kitchen_lamp"


async def test_find_references_lists_schedules_as_automatic(hass, coordinator):  # noqa: F811
    old, _ = await _adopted_old_light(hass, coordinator, state="on")
    _install(hass, [_schedule("aaa111", [_slot("10:11:00", old)])])

    rows = await coordinator.async_find_references(old)

    sched = [r for r in rows if r["kind"] == "schedule"]
    assert len(sched) == 1 and sched[0]["auto"] is True and sched[0]["always"] is True


async def test_no_scheduler_storage_is_not_an_error(hass, coordinator):  # noqa: F811
    old, _ = await _adopted_old_light(hass, coordinator, state="on")
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")

    result = await coordinator.async_replace_light(old, new, use_new_id=True)

    assert result["schedules"]["schedules"] == []
