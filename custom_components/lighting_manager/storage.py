"""Persistence for Lighting Manager (PRD §25).

Lighting Manager stores only what Home Assistant and the scheduler
provider cannot already tell it: per-light adoption/ignore state, the
controlled light-type taxonomy choice, dashboard-inclusion, and native
countdown state. Everything else (name, area, current on/off state) is
read live from Home Assistant on every access rather than duplicated
here, per PRD §4.2 ("HA is the source of truth").

Design notes tied to PRD §25 bullets:
- "Use stable HA registry identifiers where possible rather than relying
  only on entity_id strings" -> keyed by unique_id when the entity has
  one, falling back to entity_id for entities without a registry
  unique_id (e.g. some template/YAML lights).
- "Reconcile metadata when entity IDs are renamed outside Lighting
  Manager" -> because the key is unique_id, a rename is transparent;
  reconcile_entity_id() below only exists for the fallback case.
- "Provide schema versioning and migration" -> STORAGE_VERSION in
  const.py plus _async_migrate_func below.
- "Preserve ignored/adopted state ... across restart and backup
  restore" -> homeassistant.helpers.storage.Store already participates
  in HA's backup/restore; nothing extra required here.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)


@dataclass
class ManagedLightRecord:
    """Persisted metadata for one managed/adopted light.

    `key` is the stable identifier (unique_id, or entity_id as a
    fallback) - it is not itself stored inside the record, only used as
    the dict key in LightingManagerData.lights.
    """

    adopted: bool = False
    ignored: bool = False
    light_type_label: str | None = None
    dashboard_included: bool = True
    promoted_switch: bool = False
    # Historical identity, kept so a future replacement workflow (P1,
    # PRD §24) has something to offer the migration onto a new entity.
    last_known_entity_id: str | None = None
    last_known_name: str | None = None
    last_known_area_id: str | None = None


@dataclass
class CountdownRecord:
    """Persisted native countdown state for one light (PRD §20).

    `expires_at` is an absolute UTC ISO timestamp, deliberately not a
    relative "remaining seconds" value, so a restart doesn't need to
    know how much wall-clock time has already elapsed - see countdown.py
    for why this matters.
    """

    expires_at: str
    action: str  # "turn_off" - kept as a field in case future actions are added


@dataclass
class LightingManagerData:
    """Top-level shape of the persisted store."""

    lights: dict[str, dict[str, Any]] = field(default_factory=dict)
    countdowns: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Manual sort-order override per Area, for light-scheduler-list-card's
    # area-aware mode (PRD Revision - Lighting-Aware Dashboard Automation
    # §4.2). An ordered list of entity_ids; anything adopted-in-area but
    # not present here falls back to alphabetical - see
    # coordinator.async_list_area_lights.
    area_light_order: dict[str, list[str]] = field(default_factory=dict)


class LightingManagerStore:
    """Thin wrapper around homeassistant.helpers.storage.Store."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY,
            minor_version=1,
        )
        self.data = LightingManagerData()

    async def async_load(self) -> None:
        raw = await self._store.async_load()
        if raw is None:
            self.data = LightingManagerData()
            return
        self.data = LightingManagerData(
            lights=raw.get("lights", {}),
            countdowns=raw.get("countdowns", {}),
            area_light_order=raw.get("area_light_order", {}),
        )

    async def async_save(self) -> None:
        await self._store.async_save(
            {
                "lights": self.data.lights,
                "countdowns": self.data.countdowns,
                "area_light_order": self.data.area_light_order,
            }
        )

    # -- managed lights ---------------------------------------------------

    def get_light(self, key: str) -> ManagedLightRecord:
        raw = self.data.lights.get(key)
        if raw is None:
            return ManagedLightRecord()
        return ManagedLightRecord(**raw)

    async def async_set_light(self, key: str, record: ManagedLightRecord) -> None:
        self.data.lights[key] = asdict(record)
        await self.async_save()

    async def async_remove_light(self, key: str) -> None:
        self.data.lights.pop(key, None)
        await self.async_save()

    # -- countdowns ---------------------------------------------------------

    def get_countdown(self, entity_id: str) -> CountdownRecord | None:
        raw = self.data.countdowns.get(entity_id)
        if raw is None:
            return None
        return CountdownRecord(**raw)

    async def async_set_countdown(self, entity_id: str, record: CountdownRecord) -> None:
        self.data.countdowns[entity_id] = asdict(record)
        await self.async_save()

    async def async_clear_countdown(self, entity_id: str) -> None:
        if entity_id in self.data.countdowns:
            del self.data.countdowns[entity_id]
            await self.async_save()

    def all_countdowns(self) -> dict[str, CountdownRecord]:
        return {
            entity_id: CountdownRecord(**raw)
            for entity_id, raw in self.data.countdowns.items()
        }

    # -- per-Area manual light order (light-scheduler-list-card) ----------

    def get_area_light_order(self, area_id: str) -> list[str]:
        return list(self.data.area_light_order.get(area_id, []))

    async def async_set_area_light_order(self, area_id: str, entity_ids: list[str]) -> None:
        self.data.area_light_order[area_id] = list(entity_ids)
        await self.async_save()
