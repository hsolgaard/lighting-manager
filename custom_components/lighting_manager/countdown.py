"""Native countdown engine (PRD §20, §21).

Deliberately not a provider - see the Feasibility Review's "Decision:
countdown timers are native, not a provider" for why this replaced the
original Simple Timer adapter. Key properties, each tied to a PRD bullet:

- "Persist the absolute expiry time so active countdowns survive Home
  Assistant restart or integration reload" -> CountdownRecord.expires_at
  is an absolute UTC timestamp, not a relative duration, so restart
  doesn't need to reconstruct elapsed time.
- "On startup, restore future countdowns; if a countdown expired while
  Home Assistant was offline and the light is still on, apply the
  pending turn-off immediately" -> async_restore_all().
- External-action-cancels-countdown decision (2026-09, recorded in the
  Feasibility Review): any state change on the target entity that this
  manager didn't itself cause cancels the pending countdown, via
  async_track_state_change_event in async_start().
- "Do not create one Home Assistant timer helper per light" -> this
  class uses hass's own event-loop scheduling (async_call_later)
  directly; there is no per-light helper entity at all.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.util import dt as dt_util

from .storage import CountdownRecord, LightingManagerStore

_LOGGER = logging.getLogger(__name__)


@dataclass
class _ActiveCountdown:
    expires_at: datetime
    unsub_timer: callback
    unsub_state_listener: callback


class CountdownManager:
    """Owns every active native countdown across all managed lights."""

    def __init__(self, hass: HomeAssistant, store: LightingManagerStore) -> None:
        self._hass = hass
        self._store = store
        self._active: dict[str, _ActiveCountdown] = {}
        # Set while this manager's own expiry/cancel logic is changing a
        # light's state, so the state-change listener below doesn't
        # mistake our own turn_off for an "external" action and try to
        # cancel a countdown that has already finished.
        self._self_caused: set[str] = set()

    async def async_restore_all(self) -> None:
        """Restore persisted countdowns after startup (PRD §20)."""
        now = dt_util.utcnow()
        for entity_id, record in self._store.all_countdowns().items():
            expires_at = dt_util.parse_datetime(record.expires_at)
            if expires_at is None:
                _LOGGER.warning(
                    "Discarding countdown for %s: unparseable expiry %r",
                    entity_id,
                    record.expires_at,
                )
                await self._store.async_clear_countdown(entity_id)
                continue

            if expires_at <= now:
                state = self._hass.states.get(entity_id)
                if state is not None and state.state == "on":
                    _LOGGER.info(
                        "Countdown for %s expired while Home Assistant was "
                        "offline; applying pending turn-off now",
                        entity_id,
                    )
                    await self._async_apply_expiry(entity_id)
                else:
                    await self._store.async_clear_countdown(entity_id)
                continue

            self._schedule(entity_id, expires_at)

    def remaining_seconds(self, entity_id: str) -> int | None:
        active = self._active.get(entity_id)
        if active is None:
            return None
        remaining = (active.expires_at - dt_util.utcnow()).total_seconds()
        return max(0, int(remaining))

    def is_active(self, entity_id: str) -> bool:
        return entity_id in self._active

    async def async_start(self, entity_id: str, minutes: float) -> None:
        """Start (or replace) a countdown: turn the light on now, off at expiry.

        PRD §20 also describes a "Turn off in..." variant for a light
        that's already on - same engine, the only difference is whether
        we issue light.turn_on first. Both are exposed via the
        `turn_on_first` argument on the websocket command layer (api.py),
        which is the natural place for that UI-facing choice to live.
        """
        await self._async_cancel_internal(entity_id, persist=False)

        expires_at = dt_util.utcnow() + timedelta(minutes=minutes)
        await self._store.async_set_countdown(
            entity_id,
            CountdownRecord(expires_at=expires_at.isoformat(), action="turn_off"),
        )
        self._schedule(entity_id, expires_at)

    async def async_cancel(self, entity_id: str) -> None:
        """User-initiated cancel (PRD §20 "Allow cancel and restart")."""
        await self._async_cancel_internal(entity_id, persist=True)

    async def _async_cancel_internal(self, entity_id: str, *, persist: bool) -> None:
        active = self._active.pop(entity_id, None)
        if active is not None:
            active.unsub_timer()
            active.unsub_state_listener()
        if persist:
            await self._store.async_clear_countdown(entity_id)

    def _schedule(self, entity_id: str, expires_at: datetime) -> None:
        delay = max(0.0, (expires_at - dt_util.utcnow()).total_seconds())

        @callback
        def _on_expire(_now: datetime) -> None:
            self._hass.async_create_task(self._async_apply_expiry(entity_id))

        unsub_timer = async_call_later(self._hass, delay, _on_expire)

        @callback
        def _on_external_state_change(event: Event) -> None:
            if entity_id in self._self_caused:
                return
            _LOGGER.debug(
                "External state change on %s cancelled its active countdown",
                entity_id,
            )
            self._hass.async_create_task(self._async_cancel_internal(entity_id, persist=True))

        unsub_state_listener = async_track_state_change_event(
            self._hass, [entity_id], _on_external_state_change
        )

        self._active[entity_id] = _ActiveCountdown(
            expires_at=expires_at,
            unsub_timer=unsub_timer,
            unsub_state_listener=unsub_state_listener,
        )

    async def _async_apply_expiry(self, entity_id: str) -> None:
        active = self._active.pop(entity_id, None)
        if active is not None:
            # Both callbacks are still live at this point (the timer just
            # fired itself, so unsub_timer() is a harmless no-op; the state
            # listener must be explicitly removed or it leaks and, worse,
            # accumulates a fresh duplicate every time a later countdown is
            # started on the same entity_id).
            active.unsub_timer()
            active.unsub_state_listener()
        await self._store.async_clear_countdown(entity_id)

        self._self_caused.add(entity_id)
        try:
            await self._hass.services.async_call(
                "light",
                "turn_off",
                {"entity_id": entity_id},
                blocking=True,
            )
        finally:
            self._self_caused.discard(entity_id)
