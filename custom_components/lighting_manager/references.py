"""Find, and optionally re-point, everything that refers to a light's entity ID.

Used when a light's entity ID changes (Replace with "use the new light's
own ID", or "Change entity ID"). Home Assistant itself never rewrites
automations, scripts, scenes or dashboards when an entity is renamed; they
simply stop working. This module looks in the places it can safely reach:

- YAML files in the Home Assistant config folder. This includes
  automations.yaml / scripts.yaml / scenes.yaml, which is where the UI
  editors keep what you create there. Matches are whole entity IDs, so
  light.lamp is not confused with light.lamp_2. Every file is copied to
  lighting_manager_backups/<time>/ before it is changed.
- Dashboards you edit in the UI (storage mode), through Home Assistant's
  own dashboard object, not by editing its files behind its back.

It also LISTS, but never changes, things it can find yet cannot safely
rewrite: Scheduler schedules, and UI helpers that store a plain entity ID.
UI helpers that were linked through the entity picker follow a rename by
themselves and need nothing.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

BACKUP_DIR_NAME = "lighting_manager_backups"
_SKIP_DIRS = {
    ".storage", ".git", ".cloud", "custom_components", "deps", "__pycache__",
    "node_modules", "backups", "tts", "www", BACKUP_DIR_NAME, ".venv", "venv",
}
_SKIP_FILES = {"secrets.yaml"}
_MAX_BYTES = 5 * 1024 * 1024

# Reload service for the YAML files the UI editors write to.
_RELOAD_FOR_FILE = {
    "automations.yaml": ("automation", "reload"),
    "scripts.yaml": ("script", "reload"),
    "scenes.yaml": ("scene", "reload"),
}


def entity_pattern(entity_id: str) -> re.Pattern[str]:
    """Match the whole entity ID, never part of a longer one."""
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(entity_id) + r"(?![A-Za-z0-9_])")


# -- YAML files --------------------------------------------------------------


def _yaml_files(config_dir: str) -> list[str]:
    found: list[str] = []
    for root, dirs, files in os.walk(config_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name in _SKIP_FILES or not name.lower().endswith((".yaml", ".yml")):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) <= _MAX_BYTES:
                    found.append(path)
            except OSError:
                continue
    return sorted(found)


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def _scan_yaml(config_dir: str, entity_id: str) -> list[dict[str, Any]]:
    pattern = entity_pattern(entity_id)
    rows: list[dict[str, Any]] = []
    for path in _yaml_files(config_dir):
        text = _read(path)
        if text is None:
            continue
        count = len(pattern.findall(text))
        if not count:
            continue
        rows.append(
            {
                "kind": "yaml",
                "label": os.path.relpath(path, config_dir),
                "count": count,
                "auto": os.access(path, os.W_OK),
                "note": "" if os.access(path, os.W_OK) else "This file is read-only, so it must be edited by hand.",
            }
        )
    return rows


def _rewrite_yaml(config_dir: str, old: str, new: str, backup_dir: str) -> tuple[list[str], list[str]]:
    pattern = entity_pattern(old)
    changed: list[str] = []
    failed: list[str] = []
    for path in _yaml_files(config_dir):
        text = _read(path)
        if text is None or not pattern.search(text):
            continue
        rel = os.path.relpath(path, config_dir)
        try:
            target = os.path.join(backup_dir, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(path, target)
            tmp = path + ".lm-tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as handle:
                handle.write(pattern.sub(new, text))
            shutil.copymode(path, tmp)
            os.replace(tmp, path)
            changed.append(rel)
        except OSError as err:
            _LOGGER.warning("Could not update %s: %s", path, err)
            failed.append(rel)
    return changed, failed


# -- Dashboards edited in the UI ---------------------------------------------


def _lovelace_dashboards(hass: HomeAssistant) -> dict[Any, Any]:
    """The loaded dashboards, whichever shape this Home Assistant keeps them in."""
    data = hass.data.get("lovelace")
    if data is None:
        return {}
    dashboards = data.get("dashboards") if isinstance(data, dict) else getattr(data, "dashboards", None)
    return dict(dashboards or {})


async def _dashboard_config(dashboard) -> dict | None:
    try:
        return await dashboard.async_load(False)
    except Exception:  # not configured (auto-generated) or not readable
        return None


def _dashboard_label(url_path, config: dict) -> str:
    return config.get("title") or (url_path if url_path else "Overview")


async def _scan_dashboards(hass: HomeAssistant, entity_id: str) -> list[dict[str, Any]]:
    pattern = entity_pattern(entity_id)
    rows: list[dict[str, Any]] = []
    for url_path, dashboard in _lovelace_dashboards(hass).items():
        if getattr(dashboard, "mode", None) != "storage":
            continue
        config = await _dashboard_config(dashboard)
        if not config:
            continue
        count = len(pattern.findall(json.dumps(config, ensure_ascii=False)))
        if count:
            rows.append(
                {
                    "kind": "dashboard",
                    "label": f"Dashboard: {_dashboard_label(url_path, config)}",
                    "count": count,
                    "auto": True,
                    "note": "",
                }
            )
    return rows


async def _rewrite_dashboards(
    hass: HomeAssistant, old: str, new: str, backup_dir: str
) -> tuple[list[str], list[str]]:
    pattern = entity_pattern(old)
    changed: list[str] = []
    failed: list[str] = []
    for url_path, dashboard in _lovelace_dashboards(hass).items():
        if getattr(dashboard, "mode", None) != "storage":
            continue
        config = await _dashboard_config(dashboard)
        if not config:
            continue
        text = json.dumps(config, ensure_ascii=False)
        if not pattern.search(text):
            continue
        label = f"Dashboard: {_dashboard_label(url_path, config)}"
        try:
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(url_path or "overview"))
            target = os.path.join(backup_dir, "dashboards", f"{safe}.json")
            await hass.async_add_executor_job(_write_backup, target, text)
            await dashboard.async_save(json.loads(pattern.sub(new, text)))
            changed.append(label)
        except Exception as err:
            _LOGGER.warning("Could not update %s: %s", label, err)
            failed.append(label)
    return changed, failed


def _write_backup(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


# -- Things we can list but not change ---------------------------------------


def _contains_entity(value: Any, entity_id: str) -> bool:
    if isinstance(value, str):
        return value == entity_id
    if isinstance(value, Mapping):
        return any(_contains_entity(v, entity_id) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_entity(v, entity_id) for v in value)
    return False


def _scan_helpers(hass: HomeAssistant, entity_id: str) -> list[dict[str, Any]]:
    rows = []
    for entry in hass.config_entries.async_entries():
        if entry.domain in ("lighting_manager", "switch_as_x"):
            continue
        if _contains_entity(entry.options, entity_id) or _contains_entity(entry.data, entity_id):
            rows.append(
                {
                    "kind": "helper",
                    "label": f"Helper or integration: {entry.title} ({entry.domain})",
                    "count": 1,
                    "auto": False,
                    "note": "Stores this entity ID as plain text. Open it in Settings and pick the new light.",
                }
            )
    return rows


def _scan_schedules(coordinator, entity_id: str) -> list[dict[str, Any]]:
    scheduler = getattr(coordinator, "scheduler", None)
    if scheduler is None:
        return []
    rows = []
    for schedule in scheduler.async_schedules_for_entity(entity_id):
        state = coordinator.hass.states.get(schedule.schedule_id)
        name = (state.name if state else None) or schedule.schedule_id
        rows.append(
            {
                "kind": "schedule",
                "label": f"Scheduler schedule: {name}",
                "count": 1,
                "auto": False,
                "note": (
                    "Scheduler schedules can't be re-pointed automatically. Open this schedule in the "
                    "Scheduler card and choose the new light."
                ),
            }
        )
    return rows


# -- Public entry points ------------------------------------------------------


async def async_find_references(hass: HomeAssistant, coordinator, entity_id: str) -> list[dict[str, Any]]:
    """Everything that mentions entity_id. Each row says whether it can be updated automatically."""
    rows: list[dict[str, Any]] = []
    rows += await hass.async_add_executor_job(_scan_yaml, hass.config.config_dir, entity_id)
    rows += await _scan_dashboards(hass, entity_id)
    rows += _scan_schedules(coordinator, entity_id)
    rows += _scan_helpers(hass, entity_id)
    return rows


async def async_rewrite_references(hass: HomeAssistant, old: str, new: str) -> dict[str, Any]:
    """Replace the old entity ID with the new one in YAML files and UI dashboards."""
    backup_dir = os.path.join(
        hass.config.config_dir, BACKUP_DIR_NAME, datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    yaml_changed, yaml_failed = await hass.async_add_executor_job(
        _rewrite_yaml, hass.config.config_dir, old, new, backup_dir
    )
    dash_changed, dash_failed = await _rewrite_dashboards(hass, old, new, backup_dir)

    reloaded: list[str] = []
    needs_reload: list[str] = []
    seen: set[tuple[str, str]] = set()
    for rel in yaml_changed:
        reload = _RELOAD_FOR_FILE.get(os.path.basename(rel)) if os.path.dirname(rel) == "" else None
        if reload is None:
            needs_reload.append(rel)
            continue
        if reload in seen:
            continue
        seen.add(reload)
        if hass.services.has_service(*reload):
            try:
                await hass.services.async_call(*reload, blocking=True)
                reloaded.append(reload[0])
                continue
            except Exception as err:
                _LOGGER.warning("Could not reload %s: %s", reload[0], err)
        needs_reload.append(rel)

    return {
        "changed": yaml_changed + dash_changed,
        "failed": yaml_failed + dash_failed,
        "reloaded": reloaded,
        "needs_reload": needs_reload,
        "backup_dir": os.path.relpath(backup_dir, hass.config.config_dir) if (yaml_changed or dash_changed) else None,
    }
