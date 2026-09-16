"""Lighting Manager - a Home Assistant lighting administration layer.

See the PRD (Home_Assistant_Lighting_Manager_PRD_v0.3.docx) and its
companion Feasibility Review for the product rationale. This module is
the integration entry point: PRD §29's backend responsibilities are
split across registry.py (discovery/registry access), storage.py
(persistence), countdown.py (native countdown engine), and
providers/scheduler/ (the optional Niels Faber Scheduler adapter);
this file just wires them together per config entry.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components import frontend, panel_custom

from .api import async_register_websocket_commands
from .const import DOMAIN, PLATFORMS
from .coordinator import LightingManagerCoordinator
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PANEL_URL = "lighting-manager-panel"
PANEL_JS_MODULE = f"/lighting_manager_static/{PANEL_URL}.js"
# Served the same way as the panel itself (PRD Revision - Lighting-Aware
# Dashboard Automation §4.3): ships as part of this integration, so
# deploying a new build of Lighting Manager also updates the countdown
# card - no separate file to keep in sync in Hans's www/ folder the way
# light-scheduler-list-card.js (which Hans owns via his own Gist) needs.
COUNTDOWN_CARD_URL = "lighting-manager-countdown-card"
COUNTDOWN_CARD_JS_MODULE = f"/lighting_manager_static/{COUNTDOWN_CARD_URL}.js"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = LightingManagerCoordinator(hass)
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["coordinator"] = coordinator

    # Websocket commands are registered once per HA run, not per entry -
    # manifest.json's single_config_entry guarantees there's only ever
    # one entry anyway, but async_register_command would raise if called
    # twice, so guard it explicitly.
    if not hass.data[DOMAIN].get("_ws_registered"):
        async_register_websocket_commands(hass)
        hass.data[DOMAIN]["_ws_registered"] = True

    # Same once-per-HA-run guard as websocket commands above, and for the
    # same reason: single_config_entry guarantees one entry, but
    # hass.services.async_register would still raise if this ever ran
    # twice (e.g. a future change that adds a reload path).
    if not hass.data[DOMAIN].get("_services_registered"):
        async_register_services(hass)
        hass.data[DOMAIN]["_services_registered"] = True

    await _async_register_panel(hass)

    if PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: LightingManagerCoordinator = hass.data[DOMAIN]["coordinator"]
    await coordinator.async_unload()

    if PLATFORMS:
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    frontend.async_remove_panel(hass, PANEL_URL)
    async_unregister_services(hass)
    hass.data.pop(DOMAIN, None)
    return True


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Register the frontend panel (PRD §28 frontend/lighting-manager-panel.js).

    This scaffold registers a placeholder panel (see frontend/README.md)
    so the wiring - static path, panel_custom registration, sidebar
    entry - is proven end-to-end. The actual Rooms/Table/Inbox UI is the
    next major chunk of work, not part of this initial build.
    """
    static_path = hass.config.path(
        "custom_components/lighting_manager/frontend/lighting-manager-panel.js"
    )
    countdown_card_static_path = hass.config.path(
        "custom_components/lighting_manager/frontend/lighting-manager-countdown-card.js"
    )
    # HA deprecated the synchronous hass.http.register_static_path() in
    # favour of an async, StaticPathConfig-based API at some point after
    # the 2024.3 release this scaffold was verified against (see the
    # Feasibility Review for why the sandbox's package index is frozen
    # there). Try the modern API first and fall back for older cores,
    # rather than guessing which one Hans's actual HA version wants.
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(PANEL_JS_MODULE, static_path, cache_headers=False),
                StaticPathConfig(
                    COUNTDOWN_CARD_JS_MODULE, countdown_card_static_path, cache_headers=False
                ),
            ]
        )
    except ImportError:
        hass.http.register_static_path(PANEL_JS_MODULE, static_path, cache_headers=False)
        hass.http.register_static_path(
            COUNTDOWN_CARD_JS_MODULE, countdown_card_static_path, cache_headers=False
        )

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="lighting-manager-panel",
        frontend_url_path=PANEL_URL,
        module_url=PANEL_JS_MODULE,
        sidebar_title="Lighting",
        sidebar_icon="mdi:lightbulb-group",
        require_admin=False,
        embed_iframe=False,
    )
