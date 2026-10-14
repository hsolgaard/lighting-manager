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
from homeassistant.core import HomeAssistant, callback
from homeassistant.components import frontend, panel_custom

from .api import async_register_websocket_commands
from .const import CONF_SHOW_SIDEBAR_PAGES, DOMAIN, PLATFORMS
from .coordinator import LightingManagerCoordinator
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PANEL_URL = "lighting-manager-panel"
# Dashboard cards that ship inside this integration (PRD Revision -
# Lighting-Aware Dashboard Automation §4.2/§4.3). They are built into ONE
# script, BUNDLE_FILE (see scripts/build_bundle.py), served from the same
# static prefix as the panel. Two ways it reaches the browser:
#   1. Recommended: add /lighting_manager_static/lighting-manager-cards.js
#      once as a Lovelace resource (Settings > Dashboards > Resources).
#      Home Assistant waits for resources before drawing a dashboard, so
#      every card is defined in time.
#   2. Automatically, via frontend.add_extra_js_url - but Home Assistant
#      does NOT wait for those, so on some page loads a card can lose the
#      race and show "Custom element doesn't exist". Loading both is
#      harmless: only the first load runs.
# The individual card files stay served at their old URLs so any existing
# Lovelace resource pointing at one keeps working.
BUNDLE_FILE = "lighting-manager-cards.js"
CARD_FILES = (
    "lighting-manager-countdown-card.js",
    "light-scheduler-list-card.js",
    "lighting-manager-scheduler-card.js",
    "lighting-manager-room-card.js",
    "lighting-manager-overview-card.js",
    "lighting-manager-pages.js",
)
STATIC_URL_PREFIX = "/lighting_manager_static"
PANEL_JS_MODULE = f"{STATIC_URL_PREFIX}/{PANEL_URL}.js"

# Ready-made sidebar pages (PRD Revision §4.10): created by the integration so
# a new user needs no YAML and no dashboard. Each is a custom panel whose web
# component lives in the card bundle.
# (url path, element, sidebar title, sidebar icon)
EXTRA_PAGES = (
    ("lighting-manager-lights", "lighting-manager-lights-page", "Lights (Lighting Manager)", "mdi:lightbulb-group-outline"),
    ("lighting-manager-schedules", "lighting-manager-schedules-page", "Schedules (Lighting Manager)", "mdi:calendar-clock"),
)


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

    await _async_register_panel(
        hass, show_pages=entry.options.get(CONF_SHOW_SIDEBAR_PAGES, True)
    )
    # Switching the pages on or off in Configure takes effect straight away,
    # with no reload (a reload would try to register the static paths twice).
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    if PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: LightingManagerCoordinator = hass.data[DOMAIN]["coordinator"]
    await coordinator.async_unload()

    if PLATFORMS:
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    frontend.async_remove_panel(hass, PANEL_URL)
    _async_remove_pages(hass)
    async_unregister_services(hass)
    hass.data.pop(DOMAIN, None)
    return True


def _file_version(path: str) -> int:
    """File modification time, used as a cache-busting URL version."""
    try:
        return int(os.path.getmtime(path))
    except OSError:
        return 0


async def _async_register_panel(hass: HomeAssistant, show_pages: bool = True) -> None:
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
        for name in (BUNDLE_FILE, *CARD_FILES)
    }
    # Caching policy: only the panel is cached by the browser - its URL
    # is registered with ?v=<file mtime>, so a new release has a new URL.
    # The card scripts can be referenced by a FIXED address (a Lovelace
    # resource), so they are served uncached and can never go stale; the
    # bundle is small and the browser revalidates it cheaply.
    # A deploy needs a Home Assistant restart (versions are read at start-up).
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
                StaticPathConfig(url, path, cache_headers=False)
                for url, path in card_paths.items()
            ]
        )
    except ImportError:
        hass.http.register_static_path(PANEL_JS_MODULE, static_path, cache_headers=True)
        for url, path in card_paths.items():
            hass.http.register_static_path(url, path, cache_headers=False)

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="lighting-manager-panel",
        frontend_url_path=PANEL_URL,
        module_url=f"{PANEL_JS_MODULE}?v={_file_version(static_path)}",
        sidebar_title="Lighting Manager",
        sidebar_icon="mdi:lightbulb-group",
        require_admin=False,
        embed_iframe=False,
    )

    if show_pages:
        await _async_register_pages(hass)

    # After the panel registration (which pulls in the frontend), so the
    # frontend's url registry is guaranteed to exist. add_extra_js_url is
    # idempotent (a set), so a reload doesn't duplicate the entry. Only the
    # bundle is registered; the version in the URL (file mtime) makes a new
    # release load fresh.
    bundle_url = f"{STATIC_URL_PREFIX}/{BUNDLE_FILE}"
    frontend.add_extra_js_url(
        hass, f"{bundle_url}?v={_file_version(card_paths[bundle_url])}"
    )


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Options changed: show or hide the Lights and Schedules pages."""
    if entry.options.get(CONF_SHOW_SIDEBAR_PAGES, True):
        await _async_register_pages(hass)
    else:
        _async_remove_pages(hass)


async def _async_register_pages(hass: HomeAssistant) -> None:
    """Add the two ready-made sidebar pages (no-op if they are already there).

    Their web components live in the card bundle, which doubles as the
    panel module.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("pages_registered"):
        return
    bundle_path = hass.config.path(
        "custom_components/lighting_manager/frontend/" + BUNDLE_FILE
    )
    bundle_module = f"{STATIC_URL_PREFIX}/{BUNDLE_FILE}?v={_file_version(bundle_path)}"
    for url_path, element, title, icon in EXTRA_PAGES:
        await panel_custom.async_register_panel(
            hass,
            webcomponent_name=element,
            frontend_url_path=url_path,
            module_url=bundle_module,
            sidebar_title=title,
            sidebar_icon=icon,
            require_admin=False,
            embed_iframe=False,
        )
    domain_data["pages_registered"] = True


@callback
def _async_remove_pages(hass: HomeAssistant) -> None:
    """Take the two ready-made sidebar pages away (no-op if they are not there)."""
    domain_data = hass.data.get(DOMAIN, {})
    if not domain_data.get("pages_registered"):
        return
    for url_path, *_rest in EXTRA_PAGES:
        frontend.async_remove_panel(hass, url_path)
    domain_data["pages_registered"] = False
