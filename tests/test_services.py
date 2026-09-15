"""Tests for services.py: the turn_off_area service (PRD Revision §4.1).

Covers the promoted-switch safety requirement directly at the service
layer: the forwarded light.turn_off call must carry the Area target
unchanged (native HA area resolution is not this integration's job to
re-test), and the follow-up switch.turn_off call must reach only
adopted, promoted switches in the requested Area(s) - confirmed via a
fake light/switch platform that records exactly what it was called
with, the same pattern test_countdown.py uses.
"""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant, ServiceCall

from custom_components.lighting_manager.const import DOMAIN, SERVICE_TURN_OFF_AREA
from custom_components.lighting_manager.coordinator import LightingManagerCoordinator
from custom_components.lighting_manager.services import (
    async_register_services,
    async_unregister_services,
)

from .test_coordinator import _register_light


@pytest.fixture
async def fake_domain_services(hass: HomeAssistant):
    """Fake light.turn_off/switch.turn_off that just record what they got.

    Real Area resolution (area_id -> concrete entity_id) is HA core's own
    job, already confirmed live against Hans's Play Room button - this
    fixture only needs to prove Lighting Manager forwards the Area target
    unchanged and scopes the switch call correctly, not re-verify HA's
    own target-resolution mechanism.
    """
    calls: list[tuple[str, str, dict]] = []

    async def _record(domain: str, service: str, call: ServiceCall) -> None:
        calls.append((domain, service, dict(call.data)))

    async def _light_turn_off(call: ServiceCall) -> None:
        await _record("light", "turn_off", call)

    async def _switch_turn_off(call: ServiceCall) -> None:
        await _record("switch", "turn_off", call)

    hass.services.async_register("light", "turn_off", _light_turn_off)
    hass.services.async_register("switch", "turn_off", _switch_turn_off)
    return calls


@pytest.fixture
async def coordinator(hass: HomeAssistant):
    coord = LightingManagerCoordinator(hass)
    await coord.async_setup()
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["coordinator"] = coord
    async_register_services(hass)
    yield coord
    async_unregister_services(hass)
    await coord.async_unload()


async def test_turn_off_area_forwards_native_light_call(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator, fake_domain_services
) -> None:
    await hass.services.async_call(
        DOMAIN, SERVICE_TURN_OFF_AREA, {"area_id": ["big_lounge"]}, blocking=True
    )

    light_calls = [c for c in fake_domain_services if c[:2] == ("light", "turn_off")]
    assert len(light_calls) == 1
    # target merging puts area_id straight into call.data for a bare
    # (non entity-component) service - confirms the Area target was
    # forwarded unchanged, not narrowed or dropped.
    assert light_calls[0][2].get("area_id") == ["big_lounge"]


async def test_turn_off_area_only_reaches_adopted_promoted_switches_in_area(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator, fake_domain_services
) -> None:
    from homeassistant.helpers import area_registry as ar

    kitchen = ar.async_get(hass).async_create("Kitchen")

    lamp_switch = _register_light(hass, "switch.counter_lamp", "counter-lamp")
    await coordinator.async_promote_switch(lamp_switch)
    await coordinator.async_adopt_light(lamp_switch, area_id=kitchen.id)

    unrelated_switch = _register_light(hass, "switch.hob_power", "hob-power")
    from custom_components.lighting_manager import registry

    await registry.async_set_entity_area(hass, unrelated_switch, kitchen.id)

    await hass.services.async_call(
        DOMAIN, SERVICE_TURN_OFF_AREA, {"area_id": [kitchen.id]}, blocking=True
    )

    switch_calls = [c for c in fake_domain_services if c[:2] == ("switch", "turn_off")]
    assert len(switch_calls) == 1
    assert switch_calls[0][2]["entity_id"] == [lamp_switch]


async def test_turn_off_area_skips_switch_call_when_no_promoted_switches(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator, fake_domain_services
) -> None:
    """Avoids an empty no-op switch.turn_off call when a room has no lamp switches."""
    await hass.services.async_call(
        DOMAIN, SERVICE_TURN_OFF_AREA, {"area_id": ["big_lounge"]}, blocking=True
    )

    switch_calls = [c for c in fake_domain_services if c[:2] == ("switch", "turn_off")]
    assert switch_calls == []
