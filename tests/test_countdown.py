"""Tests for countdown.py: the native countdown engine (PRD §20-21).

Covers the second real bug found during manual testing prep (see the
Feasibility Review's "Second real bug" section): async_start() not
actually calling light.turn_on before scheduling the turn-off.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.lighting_manager.countdown import CountdownManager
from custom_components.lighting_manager.storage import CountdownRecord, LightingManagerStore

ENTITY_ID = "light.test_light"


@pytest.fixture
async def light_services(hass: HomeAssistant):
    """A minimal fake light.turn_on/turn_off that actually flips state.

    Real light integrations do this; without it, calling the service
    wouldn't change hass.states and the tests couldn't tell the
    difference between "turn_on was called" and "turn_on actually
    happened" - which is exactly the distinction the real bug hinged on
    (the docstring claimed it happened; the code never called it).
    """
    calls: list[str] = []

    async def _turn_on(call):
        calls.append(f"turn_on:{call.data.get('entity_id')}")
        hass.states.async_set(call.data["entity_id"], "on")

    async def _turn_off(call):
        calls.append(f"turn_off:{call.data.get('entity_id')}")
        hass.states.async_set(call.data["entity_id"], "off")

    hass.services.async_register("light", "turn_on", _turn_on)
    hass.services.async_register("light", "turn_off", _turn_off)
    hass.states.async_set(ENTITY_ID, "off")
    return calls


@pytest.fixture
async def manager(hass: HomeAssistant):
    store = LightingManagerStore(hass)
    await store.async_load()
    mgr = CountdownManager(hass, store)
    yield mgr
    # HA's test harness fails a test that leaves a scheduled timer
    # behind (a real safeguard against the exact listener-leak class of
    # bug caught during the initial build - see the Feasibility
    # Review's "Real bug, caught by re-reading the countdown code").
    for entity_id in list(mgr._active):
        await mgr._async_cancel_internal(entity_id, persist=False)


async def test_start_turns_light_on_immediately(
    hass: HomeAssistant, manager: CountdownManager, light_services: list[str]
) -> None:
    await manager.async_start(ENTITY_ID, minutes=1)

    assert hass.states.get(ENTITY_ID).state == "on"
    assert f"turn_on:{ENTITY_ID}" in light_services
    assert manager.is_active(ENTITY_ID)


async def test_start_with_turn_on_first_false_skips_turn_on(
    hass: HomeAssistant, manager: CountdownManager, light_services: list[str]
) -> None:
    hass.states.async_set(ENTITY_ID, "on")

    await manager.async_start(ENTITY_ID, minutes=1, turn_on_first=False)

    assert f"turn_on:{ENTITY_ID}" not in light_services
    assert manager.is_active(ENTITY_ID)


async def test_countdown_turns_light_off_at_expiry(
    hass: HomeAssistant,
    manager: CountdownManager,
    light_services: list[str],
    freezer,
) -> None:
    await manager.async_start(ENTITY_ID, minutes=1)
    assert hass.states.get(ENTITY_ID).state == "on"

    freezer.tick(timedelta(minutes=1, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == "off"
    assert f"turn_off:{ENTITY_ID}" in light_services
    assert not manager.is_active(ENTITY_ID)
    assert manager.remaining_seconds(ENTITY_ID) is None


async def test_manual_cancel_prevents_turn_off(
    hass: HomeAssistant,
    manager: CountdownManager,
    light_services: list[str],
    freezer,
) -> None:
    await manager.async_start(ENTITY_ID, minutes=1)

    await manager.async_cancel(ENTITY_ID)

    freezer.tick(timedelta(minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == "on"
    assert f"turn_off:{ENTITY_ID}" not in light_services
    assert not manager.is_active(ENTITY_ID)


async def test_external_state_change_cancels_countdown(
    hass: HomeAssistant,
    manager: CountdownManager,
    light_services: list[str],
    freezer,
) -> None:
    """PRD decision: external actions override countdowns."""
    await manager.async_start(ENTITY_ID, minutes=5)
    assert manager.is_active(ENTITY_ID)

    # Simulate an external toggle - not going through the countdown
    # manager's own service calls, so it isn't in self._self_caused.
    hass.states.async_set(ENTITY_ID, "off")
    await hass.async_block_till_done()

    assert not manager.is_active(ENTITY_ID)

    light_services.clear()
    freezer.tick(timedelta(minutes=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # The cancelled countdown must not fire later.
    assert f"turn_off:{ENTITY_ID}" not in light_services


async def test_restarting_a_countdown_cancels_the_previous_one(
    hass: HomeAssistant,
    manager: CountdownManager,
    light_services: list[str],
    freezer,
) -> None:
    await manager.async_start(ENTITY_ID, minutes=10)
    await manager.async_start(ENTITY_ID, minutes=1)

    freezer.tick(timedelta(minutes=1, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == "off"
    # Only one turn_off should have fired, not two lingering timers.
    assert light_services.count(f"turn_off:{ENTITY_ID}") == 1


async def test_restore_all_reschedules_future_countdown(
    hass: HomeAssistant,
    manager: CountdownManager,
    light_services: list[str],
    freezer,
) -> None:
    expires_at = dt_util.utcnow() + timedelta(minutes=5)
    await manager._store.async_set_countdown(
        ENTITY_ID, CountdownRecord(expires_at=expires_at.isoformat(), action="turn_off")
    )
    hass.states.async_set(ENTITY_ID, "on")

    await manager.async_restore_all()

    assert manager.is_active(ENTITY_ID)
    assert f"turn_off:{ENTITY_ID}" not in light_services

    freezer.move_to(expires_at + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == "off"


async def test_restore_all_applies_expired_countdown_immediately(
    hass: HomeAssistant,
    manager: CountdownManager,
    light_services: list[str],
) -> None:
    """PRD §20: a countdown that expired while HA was offline, on a light
    still 'on', should apply the pending turn-off right away on startup."""
    expired_at = dt_util.utcnow() - timedelta(minutes=5)
    await manager._store.async_set_countdown(
        ENTITY_ID, CountdownRecord(expires_at=expired_at.isoformat(), action="turn_off")
    )
    hass.states.async_set(ENTITY_ID, "on")

    await manager.async_restore_all()

    assert hass.states.get(ENTITY_ID).state == "off"
    assert manager._store.get_countdown(ENTITY_ID) is None


async def test_restore_all_discards_expired_countdown_for_off_light(
    hass: HomeAssistant,
    manager: CountdownManager,
    light_services: list[str],
) -> None:
    expired_at = dt_util.utcnow() - timedelta(minutes=5)
    await manager._store.async_set_countdown(
        ENTITY_ID, CountdownRecord(expires_at=expired_at.isoformat(), action="turn_off")
    )
    hass.states.async_set(ENTITY_ID, "off")

    await manager.async_restore_all()

    assert f"turn_off:{ENTITY_ID}" not in light_services
    assert manager._store.get_countdown(ENTITY_ID) is None


async def test_remaining_seconds_none_when_not_active(
    hass: HomeAssistant, manager: CountdownManager
) -> None:
    assert manager.remaining_seconds(ENTITY_ID) is None
    assert manager.is_active(ENTITY_ID) is False
