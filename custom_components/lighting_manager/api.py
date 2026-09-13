"""Websocket API for the Lighting Manager frontend panel.

This is the "supported HA frontend/backend API" referenced by open
technical investigation #1 (PRD §38): the frontend panel talks to the
backend exclusively through these commands, never by calling
entity/area registry websocket commands directly, so all the PRD's
side effects (updating storage, re-resolving effective Area, etc.) stay
in one place.

Investigation #6 ("confirm security/permissions expectations for
registry-modifying frontend actions"): every command that mutates HA
state or Lighting Manager's own storage below is decorated with
`@websocket_api.require_admin`, matching the convention HA's own
Settings pages already use for the same class of action (rename, area
assignment). Read-only commands (list_lights, list_inbox) are not
admin-gated, so a non-admin dashboard viewer can still see the panel.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .coordinator import LightingManagerCoordinator


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_list_lights)
    websocket_api.async_register_command(hass, ws_list_inbox)
    websocket_api.async_register_command(hass, ws_list_promotable_switches)
    websocket_api.async_register_command(hass, ws_adopt_light)
    websocket_api.async_register_command(hass, ws_ignore_light)
    websocket_api.async_register_command(hass, ws_move_light)
    websocket_api.async_register_command(hass, ws_rename_light)
    websocket_api.async_register_command(hass, ws_set_light_type)
    websocket_api.async_register_command(hass, ws_set_dashboard_included)
    websocket_api.async_register_command(hass, ws_promote_switch)
    websocket_api.async_register_command(hass, ws_start_countdown)
    websocket_api.async_register_command(hass, ws_cancel_countdown)


def _coordinator(hass: HomeAssistant) -> LightingManagerCoordinator:
    return hass.data[DOMAIN]["coordinator"]


# -- read-only ----------------------------------------------------------


@callback
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list_lights"})
def ws_list_lights(hass: HomeAssistant, connection, msg) -> None:
    lights = _coordinator(hass).async_list_lights()
    connection.send_result(msg["id"], {"lights": [vars(light) for light in lights]})


@callback
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list_inbox"})
def ws_list_inbox(hass: HomeAssistant, connection, msg) -> None:
    items = _coordinator(hass).async_build_inbox()
    connection.send_result(msg["id"], {"items": [vars(item) for item in items]})


@callback
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list_promotable_switches"})
def ws_list_promotable_switches(hass: HomeAssistant, connection, msg) -> None:
    from . import registry

    connection.send_result(
        msg["id"], {"entity_ids": registry.async_list_promotable_switches(hass)}
    )


# -- mutating (admin-gated; see module docstring) ------------------------


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/adopt_light",
        vol.Required("entity_id"): str,
        vol.Optional("name"): str,
        vol.Optional("area_id"): str,
        vol.Optional("light_type"): str,
    }
)
@websocket_api.async_response
async def ws_adopt_light(hass: HomeAssistant, connection, msg) -> None:
    await _coordinator(hass).async_adopt_light(
        msg["entity_id"],
        name=msg.get("name"),
        area_id=msg.get("area_id"),
        light_type=msg.get("light_type"),
    )
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/ignore_light", vol.Required("entity_id"): str}
)
@websocket_api.async_response
async def ws_ignore_light(hass: HomeAssistant, connection, msg) -> None:
    await _coordinator(hass).async_ignore_light(msg["entity_id"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/move_light",
        vol.Required("entity_id"): str,
        vol.Required("area_id"): str,
    }
)
@websocket_api.async_response
async def ws_move_light(hass: HomeAssistant, connection, msg) -> None:
    await _coordinator(hass).async_move_light(msg["entity_id"], msg["area_id"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/rename_light",
        vol.Required("entity_id"): str,
        vol.Required("name"): str,
    }
)
@websocket_api.async_response
async def ws_rename_light(hass: HomeAssistant, connection, msg) -> None:
    await _coordinator(hass).async_rename_light(msg["entity_id"], msg["name"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_light_type",
        vol.Required("entity_id"): str,
        vol.Optional("light_type"): vol.Any(str, None),
    }
)
@websocket_api.async_response
async def ws_set_light_type(hass: HomeAssistant, connection, msg) -> None:
    await _coordinator(hass).async_set_light_type(msg["entity_id"], msg.get("light_type"))
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_dashboard_included",
        vol.Required("entity_id"): str,
        vol.Required("included"): bool,
    }
)
@websocket_api.async_response
async def ws_set_dashboard_included(hass: HomeAssistant, connection, msg) -> None:
    await _coordinator(hass).async_set_dashboard_included(msg["entity_id"], msg["included"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/promote_switch", vol.Required("entity_id"): str}
)
@websocket_api.async_response
async def ws_promote_switch(hass: HomeAssistant, connection, msg) -> None:
    await _coordinator(hass).async_promote_switch(msg["entity_id"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/start_countdown",
        vol.Required("entity_id"): str,
        vol.Required("minutes"): vol.Coerce(float),
    }
)
@websocket_api.async_response
async def ws_start_countdown(hass: HomeAssistant, connection, msg) -> None:
    await _coordinator(hass).countdown.async_start(msg["entity_id"], msg["minutes"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/cancel_countdown", vol.Required("entity_id"): str}
)
@websocket_api.async_response
async def ws_cancel_countdown(hass: HomeAssistant, connection, msg) -> None:
    await _coordinator(hass).countdown.async_cancel(msg["entity_id"])
    connection.send_result(msg["id"])
