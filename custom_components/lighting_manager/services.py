"""Home Assistant services exposed by Lighting Manager.

See the PRD Revision Proposal ("Lighting-Aware Dashboard Automation")
§4.1 for the product rationale, and §1 for the "prefer eliminating
configuration over automating its maintenance" principle this follows.

A plain `light.turn_off` with `target: {area_id: [...]}` already reaches
every light.* entity in a room with zero Lighting Manager involvement -
Hans's own Play Room "Toggle all lights" button already does exactly
this, live, today. That native mechanism needs no service of ours and
this module does not wrap it for its own sake.

The one gap native Area targeting leaves is a promoted switch.* entity
(PRD §11.1): service domain and area targeting compose, they don't merge
domains, so an area-targeted light.turn_off will never reach a switch.
The naive fix - also area-targeting switch.turn_off - is not just
incomplete, it's actively unsafe: a room can hold unrelated switches
Lighting Manager has no business touching (Hans's real dashboard has
switch.hob_power, switch.hob_child_lock and switch.dk_vpn sharing Areas
with adopted lamp switches). turn_off_area is deliberately narrow:
forward the native, zero-maintenance light.turn_off call, then
separately turn off exactly the switch.* entities Lighting Manager has
adopted as promoted lamps in that Area - never every switch in it.
"""
from __future__ import annotations

import logging

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, LIGHT_DOMAIN, SERVICE_TURN_OFF_AREA, SWITCH_DOMAIN
from .coordinator import LightingManagerCoordinator

_LOGGER = logging.getLogger(__name__)

# extra=vol.ALLOW_EXTRA tolerates other keys HA's generic `target:` merging
# may add to service_data (entity_id/device_id/floor_id/label_id) even
# though this service only acts on area_id - rejecting them outright would
# make a `target:` call that also happens to carry an empty device_id list
# fail validation for no real reason.
_TURN_OFF_AREA_SCHEMA = vol.Schema(
    {
        vol.Required("area_id"): vol.All(cv.ensure_list, [cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)


def async_register_services(hass: HomeAssistant) -> None:
    """Register Lighting Manager's services. Call once per HA run."""

    async def _async_turn_off_area(call: ServiceCall) -> None:
        area_ids: list[str] = call.data["area_id"]
        coordinator: LightingManagerCoordinator = hass.data[DOMAIN]["coordinator"]

        # Tier 1 (PRD Revision §1): native, zero-maintenance Area
        # targeting reaches every light.* entity in these Areas, adopted
        # by Lighting Manager or not - this call needs no help from this
        # integration and would work identically without it.
        await hass.services.async_call(
            LIGHT_DOMAIN,
            "turn_off",
            {},
            target={"area_id": area_ids},
            blocking=True,
        )

        # The one gap native targeting leaves: promoted lamp switches,
        # resolved narrowly to adopted+promoted switches in these Areas
        # only - never every switch.* entity HA happens to place there.
        promoted_entity_ids = coordinator.async_list_promoted_switch_entity_ids(area_ids)
        if promoted_entity_ids:
            await hass.services.async_call(
                SWITCH_DOMAIN,
                "turn_off",
                {"entity_id": promoted_entity_ids},
                blocking=True,
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_TURN_OFF_AREA,
        _async_turn_off_area,
        schema=_TURN_OFF_AREA_SCHEMA,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    hass.services.async_remove(DOMAIN, SERVICE_TURN_OFF_AREA)
