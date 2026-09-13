"""Niels Faber Scheduler Component adapter (PRD §18).

Data-model notes (from the Feasibility Review's live spike against a
real switch.schedule_* entity on 2026-09-13):

- Scheduler Component has no query API ("which schedules target entity
  X") - see PRD §18: "Scheduler Component does not provide a reverse
  lookup from light to schedules. Lighting Manager must therefore
  maintain its own entity-to-schedules index." That's what this module
  does, kept fresh via a state-change listener rather than polling.
- Each `switch.schedule_*` entity carries `entities` (flat list of
  target entity_ids) and `actions` (flat list of {"service": ...}, with
  no per-action entity_id - the same action set applies to every entity
  in `entities`) as top-level state attributes. This makes the reverse
  index and shared-target count trivial: read `attributes.entities`
  directly, `len(entities)` is the shared-target count.
- CONFIRMED ONLY for a single-timeslot, single-action schedule. Not yet
  confirmed: whether a schedule with multiple distinct triggers (e.g.
  the PRD §19 Grey Lamp example: 18:30 ON, sunset+15m ON, 23:15 OFF)
  lives as parallel `timeslots`/`weekdays`/`actions` arrays on *one*
  entity, or as multiple sibling `switch.schedule_*` entities that this
  adapter's `async_schedules_for_entity` would then need to group per
  light in the UI layer rather than here. Flagged in the Feasibility
  Review as a five-minute check against a real multi-trigger schedule;
  `_schedule_from_state` below assumes the parallel-arrays shape for
  now and should be revisited once that's confirmed.
"""
from __future__ import annotations

import logging
import re

from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event

from .base import NormalisedSchedule, SchedulerProvider

_LOGGER = logging.getLogger(__name__)

_SCHEDULE_ENTITY_RE = re.compile(r"^switch\.schedule_[0-9a-f]+$")

PROVIDER_ID = "niels_faber_scheduler"


def _schedule_from_state(state: State) -> NormalisedSchedule:
    attrs = state.attributes
    return NormalisedSchedule(
        schedule_id=state.entity_id,
        provider=PROVIDER_ID,
        enabled=state.state == "on",
        target_entity_ids=list(attrs.get("entities", [])),
        weekdays=list(attrs.get("weekdays", [])),
        start_times=list(attrs.get("timeslots", [])),
        actions=[a.get("service", "") for a in attrs.get("actions", [])],
    )


class NielsFaberSchedulerProvider(SchedulerProvider):
    """Adapter that indexes every switch.schedule_* entity by its targets."""

    provider_id = PROVIDER_ID

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass)
        self._by_schedule_id: dict[str, NormalisedSchedule] = {}
        self._by_target: dict[str, list[str]] = {}
        self._unsub: callback | None = None

    def is_available(self) -> bool:
        return any(
            _SCHEDULE_ENTITY_RE.match(state.entity_id)
            for state in self.hass.states.async_all("switch")
        )

    async def async_setup(self) -> None:
        """Build the initial index and start listening for changes."""
        self._async_rebuild_index()

        @callback
        def _on_any_switch_change(event: Event) -> None:
            entity_id = event.data.get("entity_id", "")
            if _SCHEDULE_ENTITY_RE.match(entity_id):
                self._async_rebuild_index()

        # Scheduler Component fires ordinary state_changed events on
        # edit/add/remove (confirmed in the live spike), so a plain
        # switch-domain listener is sufficient - no custom event needed.
        self._unsub = async_track_state_change_event(
            self.hass, self._all_schedule_entity_ids(), _on_any_switch_change
        )

    def async_teardown(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _all_schedule_entity_ids(self) -> list[str]:
        return [
            state.entity_id
            for state in self.hass.states.async_all("switch")
            if _SCHEDULE_ENTITY_RE.match(state.entity_id)
        ]

    @callback
    def _async_rebuild_index(self) -> None:
        by_schedule_id: dict[str, NormalisedSchedule] = {}
        by_target: dict[str, list[str]] = {}

        for state in self.hass.states.async_all("switch"):
            if not _SCHEDULE_ENTITY_RE.match(state.entity_id):
                continue
            schedule = _schedule_from_state(state)
            by_schedule_id[schedule.schedule_id] = schedule
            for target in schedule.target_entity_ids:
                by_target.setdefault(target, []).append(schedule.schedule_id)

        self._by_schedule_id = by_schedule_id
        self._by_target = by_target

        # Re-subscribe in case schedule entities were added/removed since
        # the listener was first set up (the listener above only knows
        # about entities that existed at async_setup time otherwise).
        if self._unsub is not None:
            self._unsub()

            @callback
            def _on_any_switch_change(event: Event) -> None:
                entity_id = event.data.get("entity_id", "")
                if _SCHEDULE_ENTITY_RE.match(entity_id):
                    self._async_rebuild_index()

            self._unsub = async_track_state_change_event(
                self.hass, self._all_schedule_entity_ids(), _on_any_switch_change
            )

    def async_schedules_for_entity(self, entity_id: str) -> list[NormalisedSchedule]:
        return [
            self._by_schedule_id[schedule_id]
            for schedule_id in self._by_target.get(entity_id, [])
            if schedule_id in self._by_schedule_id
        ]

    def async_all_schedules(self) -> list[NormalisedSchedule]:
        return list(self._by_schedule_id.values())

    async def async_set_enabled(self, schedule_id: str, enabled: bool) -> None:
        await self.hass.services.async_call(
            "switch",
            "turn_on" if enabled else "turn_off",
            {"entity_id": schedule_id},
            blocking=True,
        )

    def deep_link_for_schedule(self, schedule_id: str) -> str | None:
        # Scheduler Card doesn't expose a stable per-schedule deep link
        # today (open item under PRD §38 investigation #3's remaining
        # "editing/deep-link mechanisms" half) - returning None tells the
        # frontend to fall back to "open the scheduler-card dashboard"
        # rather than a specific schedule.
        return None
