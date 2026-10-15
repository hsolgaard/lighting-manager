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
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from . import references, registry
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
from .storage import LightingManagerStore, ManagedLightRecord, ReplacedLightRecord

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
    tile_width: str
    brightness_slider: bool
    scheduling_included: bool
    schedule_count: int
    countdown_remaining_seconds: int | None
    countdown_expires_at: str | None
    source_integration: str | None
    device_id: str | None
    icon: str | None
    area_order: int | None
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

        area_order: int | None = None
        if area_id is not None:
            saved_order = self.store.get_area_light_order(area_id)
            if entity_id in saved_order:
                area_order = saved_order.index(entity_id)

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
            tile_width=record.tile_width,
            brightness_slider=record.brightness_slider,
            scheduling_included=record.scheduling_included,
            schedule_count=schedule_count,
            countdown_remaining_seconds=self.countdown.remaining_seconds(entity_id),
            countdown_expires_at=self.countdown.expires_at_iso(entity_id),
            source_integration=registry.async_get_source_integration(self.hass, entity_id),
            device_id=registry.async_get_device_id(self.hass, entity_id),
            icon=registry.async_get_icon_override(self.hass, entity_id),
            area_order=area_order,
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

    async def async_set_icon(self, entity_id: str, icon: str | None) -> None:
        await registry.async_set_entity_icon(self.hass, entity_id, icon)

    async def async_set_light_type(self, entity_id: str, light_type: str | None) -> None:
        await registry.async_set_light_type(self.hass, entity_id, light_type)

    async def async_set_dashboard_included(self, entity_id: str, included: bool) -> None:
        key = registry.stable_key_for_entity(self.hass, entity_id)
        record = self.store.get_light(key)
        record.dashboard_included = included
        await self.store.async_set_light(key, record)

    async def async_set_scheduling_included(self, entity_id: str, included: bool) -> None:
        key = registry.stable_key_for_entity(self.hass, entity_id)
        record = self.store.get_light(key)
        record.scheduling_included = included
        await self.store.async_set_light(key, record)

    async def async_set_brightness_slider(self, entity_id: str, enabled: bool) -> None:
        key = registry.stable_key_for_entity(self.hass, entity_id)
        record = self.store.get_light(key)
        record.brightness_slider = enabled
        await self.store.async_set_light(key, record)

    async def async_set_tile_width(self, entity_id: str, width: str) -> None:
        key = registry.stable_key_for_entity(self.hass, entity_id)
        record = self.store.get_light(key)
        record.tile_width = width
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

    def async_list_area_lights(self, area_id: str) -> list[LightView]:
        """Adopted, non-ignored lights in this Area, in the user's chosen order.

        Backs light-scheduler-list-card's area-aware mode (PRD Revision -
        Lighting-Aware Dashboard Automation §4.2). Only adopted lights are
        included - an ignored/unadopted light in the Area is deliberately
        left out ("otherwise what's the point of leaving some lights
        ignored", Hans, round 2 - same reasoning as promoted-switch
        adoption in async_list_promoted_switch_entity_ids).

        Order follows any persisted manual override
        (async_set_area_light_order); anything not in that override - a
        newly-adopted light, or before any override has ever been saved -
        is appended alphabetically by name, so a stale or partial override
        never hides a light, it just falls back to alphabetical for that
        one.
        """
        views = [
            v
            for v in self.async_list_lights()
            if v.adopted and not v.ignored and v.area_id == area_id
        ]
        order = self.store.get_area_light_order(area_id)
        order_index = {entity_id: i for i, entity_id in enumerate(order)}
        ordered = sorted(
            (v for v in views if v.entity_id in order_index),
            key=lambda v: order_index[v.entity_id],
        )
        unordered = sorted(
            (v for v in views if v.entity_id not in order_index),
            key=lambda v: v.name.lower(),
        )
        return ordered + unordered

    async def async_set_area_light_order(self, area_id: str, entity_ids: list[str]) -> None:
        await self.store.async_set_area_light_order(area_id, entity_ids)

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

    async def async_replace_light(
        self,
        old_entity_id: str,
        new_entity_id: str,
        convert_switch: bool = False,
        use_new_id: bool = False,
        update_references: bool = False,
    ) -> dict:
        """Replace one light with another, carrying its configuration forward (PRD §24).

        The new light takes over the OLD entity ID, so schedules,
        automations, scenes, scripts and dashboards that name the old
        light keep working untouched. Carried over: name, room, icon,
        type and other labels, and Lighting Manager's own per-light
        settings (dashboard / scheduling inclusion, tile width, brightness
        slider). The old entry is then removed if it is dead, or renamed
        aside and disabled if it still works (e.g. a Tuya WiFi bulb being
        upgraded to Zigbee) - removing a live entry would just let its
        integration re-create it as a "new" light after the next restart.

        With convert_switch=True the replacement is a switch (a smart
        plug, say) and the old one is a light: the old light is renamed
        out of the way first, a light is created from the switch with Home
        Assistant's "Switch as X" helper, and that new light takes over
        the old ID (and keeps the switch's own name). Without convert_switch
        a light and a switch can never replace each other (an entity can't
        change type).

        With use_new_id=True the new light keeps its OWN entity ID instead
        of taking the old one. Everything that referred to the old ID then
        needs re-pointing: with update_references=True the YAML files and
        UI dashboards that mention it are rewritten (see references.py).
        """
        if old_entity_id == new_entity_id:
            raise ValueError("Choose two different lights")
        ent_reg = er.async_get(self.hass)
        old_entry = ent_reg.async_get(old_entity_id)
        new_entry = ent_reg.async_get(new_entity_id)
        if old_entry is None:
            raise ValueError(f"{old_entity_id} has no entity registry entry")
        if new_entry is None:
            raise ValueError(f"{new_entity_id} has no entity registry entry")

        old_domain = old_entity_id.split(".")[0]
        new_domain = new_entity_id.split(".")[0]
        converting = False
        if old_domain != new_domain:
            if not (convert_switch and old_domain == "light" and new_domain == "switch"):
                raise ValueError(
                    "A light can only be replaced by an entity of the same type (light/switch)"
                )
            reason = registry.async_can_convert_switch(self.hass, new_entity_id)
            if reason:
                raise ValueError(reason)
            converting = True

        old_key = registry.stable_key_for_entity(self.hass, old_entity_id)
        new_key = registry.stable_key_for_entity(self.hass, new_entity_id)
        new_record_before = self.store.get_light(new_key)
        if new_record_before.adopted:
            raise ValueError(
                f"{new_entity_id} is already set up in Lighting Manager - "
                "only a new light can take over another light's place"
            )

        old_record = self.store.get_light(old_key)
        old_state = self.hass.states.get(old_entity_id)
        old_name = old_entry.name or (old_state.name if old_state else None) or old_record.last_known_name
        # The new light keeps the name it already has (e.g. a "ZB-Room-Plug-Lamp2"
        # naming standard); only the entity ID is carried over from the old light.
        new_state = self.hass.states.get(new_entity_id)
        new_name = new_entry.name or new_entry.original_name or (new_state.name if new_state else None)
        old_area = registry.async_get_effective_area(self.hass, old_entity_id)
        new_area = registry.async_get_effective_area(self.hass, new_entity_id)
        known_type_labels = set(registry._label_id_cache(self.hass).values())
        old_has_type = bool(set(old_entry.labels) & known_type_labels)
        old_platform = old_entry.platform
        old_device_id = old_entry.device_id

        # A countdown on the old light means nothing for the new one.
        await self.countdown.async_cancel(old_entity_id)
        await self.countdown.async_cancel(new_entity_id)

        result = await registry.async_take_over_entity_id(
            self.hass,
            old_entity_id,
            new_entity_id,
            old_name=old_name,
            icon=old_entry.icon,
            # A freshly created light has no room of its own yet, so give it the old one's.
            area_id=old_area if old_area and (converting or old_area != new_area) else None,
            extra_labels=set(old_entry.labels),
            drop_type_labels=old_has_type,
            convert_switch=converting,
            keep_new_id=use_new_id,
        )
        final_id = result["new_entity_id_now"]
        if converting:
            # The new light is a different entity from the switch it wraps, and
            # the switch (now hidden) must not stay in the Inbox as well.
            self.store.data.lights.pop(new_key, None)
            self._first_seen_missing.pop(new_entity_id, None)
            new_key = registry.stable_key_for_entity(self.hass, final_id)

        record = ManagedLightRecord(**{**old_record.__dict__})
        record.adopted = True
        record.ignored = False
        record.last_known_entity_id = final_id
        record.last_known_name = new_name or old_name
        record.last_known_area_id = old_area
        self.store.data.lights.pop(old_key, None)
        await self.store.async_set_light(new_key, record)
        if final_id != old_entity_id:
            await self.store.async_replace_entity_id(old_entity_id, final_id)

        self._first_seen_missing.pop(old_entity_id, None)
        self._first_seen_missing.pop(new_entity_id, None)

        # Remember the old light so it can be reviewed and removed for good later.
        if result["outcome"] == "disabled" or old_device_id:
            await self.store.async_set_replaced(
                old_key,
                ReplacedLightRecord(
                    old_domain=old_domain,
                    old_platform=old_platform,
                    old_unique_id=old_entry.unique_id,
                    old_original_entity_id=old_entity_id,
                    old_name=old_name,
                    old_device_id=old_device_id,
                    replaced_by_entity_id=final_id,
                    replaced_by_name=new_name or old_name,
                    replaced_at=dt_util.utcnow().isoformat(),
                    outcome=result["outcome"],
                ),
            )

        references_report = None
        if use_new_id and update_references and final_id != old_entity_id:
            references_report = await references.async_rewrite_references(
                self.hass, old_entity_id, final_id
            )

        return {
            "entity_id": final_id,
            "kept_old_id": final_id == old_entity_id,
            "previous_entity_id": old_entity_id,
            "outcome": result["outcome"],
            "old_entity_id_now": result["old_entity_id_now"],
            "old_platform": old_platform,
            "old_device_id": old_device_id,
            "converted": converting,
            "created_from": result.get("created_from"),
            "references": references_report,
        }

    # -- entity ID changes ---------------------------------------------------

    async def async_find_references(self, entity_id: str) -> list[dict]:
        """Everything outside Lighting Manager that mentions this entity ID."""
        return await references.async_find_references(self.hass, self, entity_id)

    async def async_change_entity_id(
        self, entity_id: str, new_entity_id: str, update_references: bool
    ) -> dict:
        """Rename a light's entity ID, optionally re-pointing what referred to it."""
        registry.async_validate_new_entity_id(self.hass, entity_id, new_entity_id)
        await self.countdown.async_cancel(entity_id)
        ent_reg = er.async_get(self.hass)
        try:
            ent_reg.async_update_entity(entity_id, new_entity_id=new_entity_id)
        except ValueError as err:
            raise ValueError(str(err)) from err
        await self.store.async_replace_entity_id(entity_id, new_entity_id)
        self._first_seen_missing.pop(entity_id, None)
        report = None
        if update_references:
            report = await references.async_rewrite_references(self.hass, entity_id, new_entity_id)
        return {"entity_id": new_entity_id, "previous_entity_id": entity_id, "references": report}

    # -- replaced lights (Replaced tab) --------------------------------------

    def _replaced_row(self, key: str, record: ReplacedLightRecord) -> dict | None:
        """Live status of one replaced light, or None when there is nothing left to review."""
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        entity_id = None
        if record.old_platform and record.old_unique_id:
            entity_id = ent_reg.async_get_entity_id(
                record.old_domain, record.old_platform, record.old_unique_id
            )
        device = dev_reg.async_get(record.old_device_id) if record.old_device_id else None
        if entity_id is None and device is None:
            return None
        shared: list[str] = []
        if device is not None:
            shared = sorted(
                e.entity_id
                for e in er.async_entries_for_device(ent_reg, device.id, include_disabled_entities=True)
                if e.entity_id != entity_id
            )
        replaced_by = self.hass.states.get(record.replaced_by_entity_id or "")
        return {
            "id": key,
            "old_name": record.old_name,
            "entity_id_now": entity_id,
            "original_entity_id": record.old_original_entity_id,
            "platform": record.old_platform,
            "replaced_by_entity_id": record.replaced_by_entity_id,
            "replaced_by_name": (replaced_by.name if replaced_by else None) or record.replaced_by_name,
            "replaced_at": record.replaced_at,
            "outcome": record.outcome,
            "entity_present": entity_id is not None,
            "device_present": device is not None,
            "device_name": (device.name_by_user or device.name) if device is not None else None,
            "device_shared_with": shared,
        }

    async def async_list_replaced(self) -> list[dict]:
        rows: list[dict] = []
        for key, record in list(self.store.all_replaced().items()):
            row = self._replaced_row(key, record)
            if row is None:
                await self.store.async_remove_replaced(key)  # nothing left to review
                continue
            rows.append(row)
        rows.sort(key=lambda r: r["replaced_at"] or "", reverse=True)
        return rows

    async def async_remove_replaced(self, key: str) -> dict:
        """Delete a replaced light from Home Assistant: its entity and, if unused, its device."""
        record = self.store.all_replaced().get(key)
        if record is None:
            raise ValueError("That light is not on the Replaced list")
        result = await registry.async_remove_replaced_entity_and_device(
            self.hass,
            domain=record.old_domain,
            platform=record.old_platform,
            unique_id=record.old_unique_id,
            device_id=record.old_device_id,
        )
        # Forget the row once nothing of it is left; keep it if the device is stuck.
        if self._replaced_row(key, record) is None:
            await self.store.async_remove_replaced(key)
        return result

    async def async_dismiss_replaced(self, key: str) -> None:
        """Stop listing a replaced light without deleting anything."""
        await self.store.async_remove_replaced(key)
