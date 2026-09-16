"""Websocket API smoke tests (PRD §29, admin-gating).

These exercise the same commands Hans ran by hand from the browser
console during live testing (list_lights, adopt_light, start_countdown,
...), through hass_ws_client instead - the automated equivalent of that
manual pass, plus a permission check manual testing didn't cover.
"""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.lighting_manager.api import async_register_websocket_commands
from custom_components.lighting_manager.const import DOMAIN
from custom_components.lighting_manager.coordinator import LightingManagerCoordinator


@pytest.fixture
async def api_hass(hass: HomeAssistant):
    """A hass with a live coordinator wired up and websocket commands registered.

    Deliberately skips async_setup_entry's panel/static-path registration
    (which needs the frontend/http machinery fully stood up) so this
    stays a focused test of the websocket command layer itself, matching
    what was actually exercised by hand against Hans's instance.
    """
    coordinator = LightingManagerCoordinator(hass)
    await coordinator.async_setup()
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["coordinator"] = coordinator
    async_register_websocket_commands(hass)
    yield hass
    await coordinator.async_unload()


async def test_list_lights_returns_discovered_light(
    api_hass: HomeAssistant, hass_ws_client
) -> None:
    api_hass.states.async_set("light.kitchen", "on")
    client = await hass_ws_client(api_hass)

    await client.send_json_auto_id({"type": f"{DOMAIN}/list_lights"})
    response = await client.receive_json()

    assert response["success"] is True
    entity_ids = {light["entity_id"] for light in response["result"]["lights"]}
    assert "light.kitchen" in entity_ids


async def test_adopt_light_via_websocket(
    api_hass: HomeAssistant, hass_ws_client
) -> None:
    # Registered via the entity registry (not just a bare state) since
    # adopt_light's rename step requires it - see the "known limitation"
    # note on registry-less entities in test_coordinator.py.
    entry = er.async_get(api_hass).async_get_or_create(
        "light", "test_platform", "office-spots-ws"
    )
    api_hass.states.async_set(entry.entity_id, "off")
    client = await hass_ws_client(api_hass)

    await client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/adopt_light",
            "entity_id": entry.entity_id,
            "name": "Test Light",
            "light_type": "spotlights",
        }
    )
    response = await client.receive_json()
    assert response["success"] is True

    await client.send_json_auto_id({"type": f"{DOMAIN}/list_lights"})
    listing = await client.receive_json()
    light = next(
        light
        for light in listing["result"]["lights"]
        if light["entity_id"] == entry.entity_id
    )
    assert light["adopted"] is True
    assert light["light_type"] == "spotlights"


async def test_start_countdown_via_websocket_turns_light_on(
    api_hass: HomeAssistant, hass_ws_client
) -> None:
    """Regression coverage, at the websocket layer, for the turn_on_first bug."""

    async def _turn_on(call):
        api_hass.states.async_set(call.data["entity_id"], "on")

    async def _turn_off(call):
        api_hass.states.async_set(call.data["entity_id"], "off")

    api_hass.services.async_register("light", "turn_on", _turn_on)
    api_hass.services.async_register("light", "turn_off", _turn_off)
    api_hass.states.async_set("light.office_spots", "off")

    client = await hass_ws_client(api_hass)
    await client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/start_countdown",
            "entity_id": "light.office_spots",
            "minutes": 1,
        }
    )
    response = await client.receive_json()

    assert response["success"] is True
    assert api_hass.states.get("light.office_spots").state == "on"

    # Clean up the still-pending timer so the test harness doesn't flag
    # a lingering job at teardown.
    coordinator: LightingManagerCoordinator = api_hass.data[DOMAIN]["coordinator"]
    await coordinator.countdown.async_cancel("light.office_spots")


async def test_list_lights_includes_countdown_expires_at(
    api_hass: HomeAssistant, hass_ws_client
) -> None:
    async def _turn_on(call):
        api_hass.states.async_set(call.data["entity_id"], "on")

    async def _turn_off(call):
        api_hass.states.async_set(call.data["entity_id"], "off")

    api_hass.services.async_register("light", "turn_on", _turn_on)
    api_hass.services.async_register("light", "turn_off", _turn_off)
    api_hass.states.async_set("light.office_spots", "off")

    client = await hass_ws_client(api_hass)
    await client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/start_countdown",
            "entity_id": "light.office_spots",
            "minutes": 5,
        }
    )
    assert (await client.receive_json())["success"] is True

    await client.send_json_auto_id({"type": f"{DOMAIN}/list_lights"})
    listing = await client.receive_json()
    light = next(
        light
        for light in listing["result"]["lights"]
        if light["entity_id"] == "light.office_spots"
    )
    assert light["countdown_expires_at"] is not None

    coordinator: LightingManagerCoordinator = api_hass.data[DOMAIN]["coordinator"]
    await coordinator.countdown.async_cancel("light.office_spots")


async def test_unignore_light_via_websocket(
    api_hass: HomeAssistant, hass_ws_client
) -> None:
    api_hass.states.async_set("light.attic", "off")
    client = await hass_ws_client(api_hass)

    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/ignore_light", "entity_id": "light.attic"}
    )
    assert (await client.receive_json())["success"] is True

    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/unignore_light", "entity_id": "light.attic"}
    )
    response = await client.receive_json()
    assert response["success"] is True

    await client.send_json_auto_id({"type": f"{DOMAIN}/list_inbox"})
    inbox = await client.receive_json()
    conditions = {
        item["condition"]
        for item in inbox["result"]["items"]
        if item["entity_id"] == "light.attic"
    }
    assert conditions == {"new_light"}


async def test_mutating_command_rejected_for_non_admin(
    api_hass: HomeAssistant, hass_ws_client, hass_read_only_access_token
) -> None:
    api_hass.states.async_set("light.office_spots", "off")
    client = await hass_ws_client(api_hass, hass_read_only_access_token)

    await client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/adopt_light",
            "entity_id": "light.office_spots",
            "name": "Should Not Work",
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "unauthorized"


async def test_list_area_lights_returns_only_adopted_in_area(
    api_hass: HomeAssistant, hass_ws_client
) -> None:
    from homeassistant.helpers import area_registry as ar

    coordinator: LightingManagerCoordinator = api_hass.data[DOMAIN]["coordinator"]
    lounge = ar.async_get(api_hass).async_create("Big Lounge")

    entry = er.async_get(api_hass).async_get_or_create(
        "light", "test_platform", "lounge-ceiling-ws"
    )
    api_hass.states.async_set(entry.entity_id, "off")
    await coordinator.async_adopt_light(entry.entity_id, area_id=lounge.id)

    client = await hass_ws_client(api_hass)
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/list_area_lights", "area_id": lounge.id}
    )
    response = await client.receive_json()

    assert response["success"] is True
    entity_ids = {light["entity_id"] for light in response["result"]["lights"]}
    assert entity_ids == {entry.entity_id}


async def test_set_area_light_order_via_websocket_then_reflected_in_list(
    api_hass: HomeAssistant, hass_ws_client
) -> None:
    from homeassistant.helpers import area_registry as ar

    coordinator: LightingManagerCoordinator = api_hass.data[DOMAIN]["coordinator"]
    lounge = ar.async_get(api_hass).async_create("Big Lounge")

    first = er.async_get(api_hass).async_get_or_create("light", "test_platform", "first")
    second = er.async_get(api_hass).async_get_or_create("light", "test_platform", "second")
    for entry in (first, second):
        api_hass.states.async_set(entry.entity_id, "off")
        await coordinator.async_adopt_light(entry.entity_id, area_id=lounge.id)

    client = await hass_ws_client(api_hass)
    await client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/set_area_light_order",
            "area_id": lounge.id,
            "entity_ids": [second.entity_id, first.entity_id],
        }
    )
    assert (await client.receive_json())["success"] is True

    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/list_area_lights", "area_id": lounge.id}
    )
    listing = await client.receive_json()
    ordered_ids = [light["entity_id"] for light in listing["result"]["lights"]]
    assert ordered_ids == [second.entity_id, first.entity_id]


async def test_set_area_light_order_rejected_for_non_admin(
    api_hass: HomeAssistant, hass_ws_client, hass_read_only_access_token
) -> None:
    client = await hass_ws_client(api_hass, hass_read_only_access_token)

    await client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/set_area_light_order",
            "area_id": "big_lounge",
            "entity_ids": [],
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "unauthorized"


async def test_read_only_command_allowed_for_non_admin(
    api_hass: HomeAssistant, hass_ws_client, hass_read_only_access_token
) -> None:
    """list_lights is deliberately not admin-gated (module docstring)."""
    api_hass.states.async_set("light.kitchen", "on")
    client = await hass_ws_client(api_hass, hass_read_only_access_token)

    await client.send_json_auto_id({"type": f"{DOMAIN}/list_lights"})
    response = await client.receive_json()

    assert response["success"] is True
