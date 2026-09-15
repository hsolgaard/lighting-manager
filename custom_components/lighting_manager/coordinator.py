"""Core orchestration logic (PRD §29 backend responsibilities).

This is where "the aggregation itself is the product" (Feasibility
Review) actually happens: combining live HA registry/state data with
Lighting Manager's own minimal persisted metadata, the countdown
engine, and the scheduler provider's reverse index into one coherent
per-light view and an Inbox of things needing attention.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from . import registry
from .const import (
    INBOX_AREA_MISMATCH,
    INBOX_MISSING,
    INBOX_NEW,
    INBOX_UNASSIGNED,
    MISSING_AFTER_SECONDS,
    SWITCH_DOMAIN,
)
from .countdown import CountdownManager
from .providers.scheduler.base import SchedulerProvider
from .providers.scheduler.niels_faber import NielsFaberSchedulerProvider
from .storage import LightingManagerStore, ManagedLightRecord

_LOGGER = logging.getLogger(__name__)


@dataclass
class LightView:
    """One row of the Table/Rooms view (PRD §7, §8) - the shape sent to the frontend."""

    entity_id: str
    key: str
    name: str
    state: str
    area_id: str | None
    adopted: bool
    ignored: bool
    light_type: str | None
    dashboard_included: bool
    schedule_count: int
    countdown_remaining_seconds: int | None
    source_integration: str | None
    area_mismatch: tuple[str, str] | None
    status: str


@dataclass
class InboxItem:
    condition: str
    entity_id: str
    detail: dict = field(default_factory=dict)


class LightingManagerCoordinator:
    """Owns discovery, persisted metadata, countdowns and the scheduler provider."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = LightingManagerStore(hass)
        self.countdown = CountdownManager(hass, self.store)
        self.scheduler: SchedulerProvider | None = None
        self._first_seen_missing: dict[str, datetime] = {}
        self._unsub_state: callback | None = None
        self._unsub_interval: callback | None = None
        self._unsub_scheduler_start: callback | None = None

    async def async_setup(self) -> None:
        await self.store.async_load()
        await registry.async_ensure_type_labels_exist(self.hass)
        await self.countdown.async_restore_all()

        # Real bug found 2026-09-14: checking niels_faber.is_available()
        # inline, right here, meant that on a cold boot this ran before
        # the Scheduler Component (a separate custom integration) had
        # necessarily created its switch.schedule_* entities yet -
        # integration setup order across custom_components isn't
        # guaranteed. Since this was only ever checked once, a false
        # negative on that one check disabled scheduling for the rest of
        # the HA run: every light showed 0 schedules regardless of what
        # was actually configured, confirmed live against Hans's real
        # switch.schedule_* entities (all correctly hex-formatted, so the
        # reverse-index regex wasn't the problem - the detection timing
        # was). async_at_started() defers this check to when HA has
        # actually finished starting (or runs it immediately if it
        # already has, e.g. on a config entry reload rather than a cold
        # boot), so the scheduler is detected reliably regardless of
        # load order.
        self._unsub_scheduler_start = async_at_started(self.hass, self._async_setup_scheduler)

        self._unsub_state = async_track_state_change_event(
            self.hass, self._async_all_managed_entity_ids(), self._async_on_light_state_change
        )
        # Periodic sweep to catch lights that go quietly unavailable
        # without a fresh state_changed event to trigger re-evaluation.
        self._unsub_interval = async_track_time_interval(
            self.hass, self._async_periodic_reconcile, timedelta(minutes=15)
        )

    async def _async_setup_scheduler(self, hass: HomeAssistant) -> None:
        niels_faber = NielsFaberSchedulerProvider(self.hass)
        if niels_faber.is_available():
            await niels_faber.async_setup()
            self.scheduler = niels_faber
            _LOGGER.info("Niels Faber Scheduler detected; schedule features enabled")
        else:
            _LOGGER.info("No supported scheduler provider installed; scheduling disabled (PRD §4.5)")

    async def async_unload(self) -> None:
        if self._unsub_scheduler_start is not None:
            # Cancels the pending async_at_started callback if HA hasn't
            # finished starting yet and this entry is unloaded/reloaded
            # first - avoids a late scheduler setup racing the teardown
            # below.
            self._unsub_scheduler_start()
        if self._unsub_state is not None:
            self._unsub_state()
        if self._unsub_interval is not None:
            self._unsub_interval()
        if self.scheduler is not None:
            self.scheduler.async_teardown()

    @callback
    def _async_on_light_state_change(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if entity_id is None:
            return
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            self._first_seen_missing.setdefault(entity_id, dt_util.utcnow())
        else:
            self._first_seen_missing.pop(entity_id, None)

    @callback
    def _async_periodic_reconcile(self, _now: datetime) -> None:
        for entity_id in self._async_all_managed_entity_ids():
            state = self.hass.states.get(entity_id)
            if state is not None and state.state not in ("unavailable", "unknown"):
                self._first_seen_missing.pop(entity_id, None)

    def _async_all_managed_entity_ids(self) -> list[str]:
        """light.* entities plus any switch.* entities promoted as lighting (PRD §11.1).

        A promoted switch never appears in registry.async_list_eligible_lights
        (it deliberately only scans the light domain, since PRD §11.1 says
        Lighting Manager "must never assume that every switch is a light").
        Once a user has explicitly promoted one via async_promote_switch,
        though, it needs to show up everywhere a light does - Rooms, Table,
        Inbox, state tracking - so this combines both sources.
        """
        entity_ids = list(registry.async_list_eligible_lights(self.hass))
        seen = set(entity_ids)
        for record_dict in self.store.data.lights.values():
            if not record_dict.get("promoted_switch"):
                continue
            candidate = record_dict.get("last_known_entity_id")
            if candidate and candidate not in seen and self.hass.states.get(candidate) is not None:
                entity_ids.append(candidate)
                seen.add(candidate)
        return entity_ids

    # -- per-light view -----------------------------------------------------

    def _build_light_view(self, entity_id: str) -> LightView:
        key = registry.stable_key_for_entity(self.hass, entity_id)
        record: ManagedLightRecord = self.store.get_light(key)
        state = self.hass.states.get(entity_id)
        area_id = registry.async_get_effective_area(self.hass, entity_id)
        area_mismatch = registry.async_get_area_mismatch(self.hass, entity_id)
        schedule_count = 0
        if self.scheduler is not None:
            schedule_count = len(self.scheduler.async_schedules_for_entity(entity_id))

        status = "ok"
        if not record.adopted and not record.ignored:
            status = "needs_setup"
        elif area_id is None:
            status = "unassigned"
        elif area_mismatch is not None:
            status = "area_mismatch"
        elif state is None or state.state in ("unavailable", "unknown"):
            status = "unavailable"

        return LightView(
            entity_id=entity_id,
            key=key,
            name=state.name if state else entity_id,
            state=state.state if state else "unknown",
            area_id=area_id,
            adopted=record.adopted,
            ignored=record.ignored,
            light_type=registry.get_light_type(self.hass, entity_id),
            dashboard_included=record.dashboard_included,
            schedule_count=schedule_count,
            countdown_remaining_seconds=self.countdown.remaining_seconds(entity_id),
            source_integration=registry.async_get_source_integration(self.hass, entity_id),
            area_mismatch=area_mismatch,
            status=status,
        )

    def async_list_lights(self) -> list[LightView]:
        return [
            self._build_light_view(entity_id)
            for entity_id in self._async_all_managed_entity_ids()
        ]

    def async_build_inbox(self) -> list[InboxItem]:
        """PRD §16 Inbox conditions, steady-state (post first-run-adoption)."""
        items: list[InboxItem] = []
        now = dt_util.utcnow()

        for entity_id in self._async_all_managed_entity_ids():
            key = registry.stable_key_for_entity(self.hass, entity_id)
            record = self.store.get_light(key)

            if not record.adopted and not record.ignored:
                items.append(InboxItem(INBOX_NEW, entity_id))
                continue
            if not record.adopted:
                continue  # ignored, steady state, nothing to surface

            area_id = registry.async_get_effective_area(self.hass, entity_id)
            if area_id is None:
                items.append(InboxItem(INBOX_UNASSIGNED, entity_id))

            mismatch = registry.async_get_area_mismatch(self.hass, entity_id)
            if mismatch is not None:
                items.append(
                    InboxItem(
                        INBOX_AREA_MISMATCH,
                        entity_id,
                        {"device_area": mismatch[0], "entity_area": mismatch[1]},
                    )
                )

            first_seen = self._first_seen_missing.get(entity_id)
            if first_seen is not None and (now - first_seen).total_seconds() >= MISSING_AFTER_SECONDS:
                items.append(InboxItem(INBOX_MISSING, entity_id, {"since": first_seen.isoformat()}))

        return items

    # -- mutating actions used by the websocket API (PRD §29 "expose
    #    normalised services/actions to the frontend") -------------------

    async def async_adopt_light(
        self,
        entity_id: str,
        *,
        name: str | None = None,
        area_id: str | None = None,
        light_type: str | None = None,
    ) -> None:
        if name:
            await registry.async_rename_entity(self.hass, entity_id, name)
        if area_id:
            await registry.async_set_entity_area(self.hass, entity_id, area_id)
        if light_type:
            await registry.async_set_light_type(self.hass, entity_id, light_type)

        key = registry.stable_key_for_entity(self.hass, entity_id)
        record = self.store.get_light(key)
        record.adopted = True
        record.ignored = False
        record.last_known_entity_id = entity_id
        state = self.hass.states.get(entity_id)
        record.last_known_name = state.name if state else name
        record.last_known_area_id = registry.async_get_effective_area(self.hass, entity_id)
        await self.store.async_set_light(key, record)

    async def async_ignore_light(self, entity_id: str) -> None:
        key = registry.stable_key_for_entity(self.hass, entity_id)
        record = self.store.get_light(key)
        record.ignored = True
        await self.store.async_set_light(key, record)

    async def async_unignore_light(self, entity_id: str) -> None:
        """Counterpart to async_ignore_light.

        Not in the original PRD/backend scope - added while building the
        real Inbox UI, because an Ignore button with no way back would be
        a one-way trap for whoever clicks it by mistake. Mirrors
        async_ignore_light exactly; the light returns to the Inbox as
        INBOX_NEW on the next async_build_inbox call, same as any other
        never-adopted light.
        """
        key = registry.stable_key_for_entity(self.hass, entity_id)
        record = self.store.get_light(key)
        record.ignored = False
        await self.store.async_set_light(key, record)

    async def async_move_light(self, entity_id: str, area_id: str) -> None:
        await registry.async_set_entity_area(self.hass, entity_id, area_id)
        key = registry.stable_key_for_entity(self.hass, entity_id)
        record = self.store.get_light(key)
        record.last_known_area_id = area_id
        await self.store.async_set_light(key, record)

    async def async_rename_light(self, entity_id: str, name: str) -> None:
        await registry.async_rename_entity(self.hass, entity_id, name)

    async def async_set_light_type(self, entity_id: str, light_type: str | None) -> None:
        await registry.async_set_light_type(self.hass, entity_id, light_type)

    async def async_set_dashboard_included(self, entity_id: str, included: bool) -> None:
        key = registry.stable_key_for_entity(self.hass, entity_id)
        record = self.store.get_light(key)
        record.dashboard_included = included
        await self.store.async_set_light(key, record)

    def async_list_promoted_switch_entity_ids(self, area_ids: list[str]) -> list[str]:
        """Adopted, promoted lamp switches whose effective Area is in area_ids.

        Backs the turn_off_area service (PRD Revision §4.1): native
        Area-targeted light.turn_off never reaches a promoted switch.*
        entity, since service domain and area targeting compose rather
        than merge - and naively also area-targeting switch.turn_off is
        unsafe, since a room can hold unrelated switches Lighting Manager
        has no business touching (Hans's real dashboard has
        switch.hob_power, switch.hob_child_lock and switch.dk_vpn sharing
        an Area with an adopted lamp switch). Only entities that are both
        adopted (not merely promoted - PRD §11.1 requires the explicit
        adoption step, same as any other light) and not ignored are
        returned.
        """
        area_id_set = set(area_ids)
        result: list[str] = []
        for entity_id in self._async_all_managed_entity_ids():
            if not entity_id.startswith(f"{SWITCH_DOMAIN}."):
                continue
            key = registry.stable_key_for_entity(self.hass, entity_id)
            record = self.store.get_light(key)
            if not record.promoted_switch or not record.adopted or record.ignored:
                continue
            area_id = registry.async_get_effective_area(self.hass, entity_id)
            if area_id in area_id_set:
                result.append(entity_id)
        return result

    async def async_promote_switch(self, entity_id: str) -> None:
        """PRD §11.1: explicit, per-entity opt-in - never inferred automatically."""
        key = registry.stable_key_for_entity(self.hass, entity_id)
        record = self.store.get_light(key)
        record.promoted_switch = True
        # Required so _async_all_managed_entity_ids() can find this entity
        # again later - it isn't in the light.* domain so nothing else
        # would surface it.
        record.last_known_entity_id = entity_id
        await self.store.async_set_light(key, record)
