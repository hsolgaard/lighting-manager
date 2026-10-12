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
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components import frontend, panel_custom

from .api import async_register_websocket_commands
from .const import DOMAIN, PLATFORMS
from .coordinator import LightingManagerCoordinator
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PANEL_URL = "lighting-manager-panel"
# Dashboard cards that ship inside this integration (PRD Revision -
# Lighting-Aware Dashboard Automation §4.2/§4.3). They are served from the
# same static prefix as the panel and registered globally with
# frontend.add_extra_js_url, so installing/updating Lighting Manager is the
# only step: no Lovelace "Resources" entry and no file copied into www/.
CARD_FILES = (
    "lighting-manager-countdown-card.js",
    "light-scheduler-list-card.js",
    "lighting-manager-scheduler-card.js",
    "lighting-manager-room-card.js",
)
STATIC_URL_PREFIX = "/lighting_manager_static"
PANEL_JS_MODULE = f"{STATIC_URL_PREFIX}/{PANEL_URL}.js"


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


def _file_version(path: str) -> int:
    """File modification time, used as a cache-busting URL version."""
    try:
        return int(os.path.getmtime(path))
    except OSError:
        return 0


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Register the frontend panel (PRD §28 frontend/lighting-manager-panel.js).

    This scaffold registers a placeholder panel (see frontend/README.md)
    so the wiring - static path, panel_custom registration, sidebar
    entry - is proven end-to-end. The actual Rooms/Table/Inbox UI is the
    next major chunk of work, not part of this initial build.
    """
    frontend_dir = "custom_components/lighting_manager/frontend/"
    static_path = hass.config.path(frontend_dir + "lighting-manager-panel.js")
    card_paths = {
        f"{STATIC_URL_PREFIX}/{name}": hass.config.path(frontend_dir + name)
        for name in CARD_FILES
    }
    # Caching is on: every script is registered below with ?v=<file mtime>
    # in its URL, so a new release has a new URL and is fetched fresh,
    # while unchanged files come from the browser cache (fast start-up,
    # shorter "Configuration error" flash). A deploy therefore needs a
    # Home Assistant restart to take effect.
    # HA deprecated the synchronous hass.http.register_static_path() in
    # favour of an async, StaticPathConfig-based API at some point after
    # the 2024.3 release this scaffold was verified against (see the
    # Feasibility Review for why the sandbox's package index is frozen
    # there). Try the modern API first and fall back for older cores,
    # rather than guessing which one Hans's actual HA version wants.
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_JS_MODULE, static_path, cache_headers=True)]
            + [
                StaticPathConfig(url, path, cache_headers=True)
                for url, path in card_paths.items()
            ]
        )
    except ImportError:
        hass.http.register_static_path(PANEL_JS_MODULE, static_path, cache_headers=True)
        for url, path in card_paths.items():
            hass.http.register_static_path(url, path, cache_headers=True)

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="lighting-manager-panel",
        frontend_url_path=PANEL_URL,
        module_url=f"{PANEL_JS_MODULE}?v={_file_version(static_path)}",
        sidebar_title="Lighting",
        sidebar_icon="mdi:lightbulb-group",
        require_admin=False,
        embed_iframe=False,
    )

    # After the panel registration (which pulls in the frontend), so the
    # frontend's url registry is guaranteed to exist. add_extra_js_url is
    # idempotent (a set), so a reload doesn't duplicate the entries.
    #
    # The URL carries the file's modification time as a version, so a
    # new release is always fetched fresh - browsers (Safari especially)
    # otherwise keep running a cached copy of a script whose URL has not
    # changed. Takes effect after a Home Assistant restart.
    for url, path in card_paths.items():
        frontend.add_extra_js_url(hass, f"{url}?v={_file_version(path)}")
