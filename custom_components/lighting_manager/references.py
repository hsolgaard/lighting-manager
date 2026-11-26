"""Find everything that refers to a light's entity ID. Read-only.

Used when a light's entity ID changes (Replace with "use the new light's
own ID", or "Change entity ID"). Home Assistant itself never rewrites
automations, scripts, scenes or dashboards when an entity is renamed; they
simply stop working. Lighting Manager LISTS what still uses the old ID so
you can update it yourself. It never changes your files or dashboards
(1.1.0: the automatic rewrite was removed after the HACS review).

It looks in:
- YAML files in the Home Assistant config folder, including
  automations.yaml / scripts.yaml / scenes.yaml, where the UI editors keep
  what you create there. Matches are whole entity IDs, so light.lamp is not
  confused with light.lamp_2.
- Dashboards you edit in the UI (storage mode), read through Home
  Assistant's own dashboard object.
- UI helpers that store a plain entity ID.
(Scheduler schedules are handled separately, by schedules.py.)
UI helpers that were linked through the entity picker follow a rename by
themselves and need nothing.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
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
                "auto": False,
                "note": "Edit it and change the entity ID by hand, then reload it (or restart).",
            }
        )
    return rows


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
                    "auto": False,
                    "note": "Open the dashboard, edit the cards that use it and pick the new light.",
                }
            )
    return rows


# -- UI helpers -----------------------------------------------------------------


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


# -- Public entry points ------------------------------------------------------


async def async_find_references(hass: HomeAssistant, coordinator, entity_id: str) -> list[dict[str, Any]]:
    """Everything that mentions entity_id, for you to update by hand. Read-only."""
    rows: list[dict[str, Any]] = []
    rows += await hass.async_add_executor_job(_scan_yaml, hass.config.config_dir, entity_id)
    rows += await _scan_dashboards(hass, entity_id)
    rows += _scan_helpers(hass, entity_id)
    return rows
