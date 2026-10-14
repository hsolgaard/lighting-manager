"""Frontend registration: bundled dashboard cards (PRD Revision §4.2/§4.3)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

import custom_components.lighting_manager as integration
from custom_components.lighting_manager import (
    BUNDLE_FILE,
    CARD_FILES,
    PANEL_JS_MODULE,
    STATIC_URL_PREFIX,
)

FRONTEND_DIR = Path(integration.__file__).parent / "frontend"
BUILD_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_bundle.py"


def _load_build_script():
    spec = importlib.util.spec_from_file_location("build_bundle", BUILD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_declared_card_file_exists() -> None:
    for name in (BUNDLE_FILE, *CARD_FILES):
        assert (FRONTEND_DIR / name).is_file(), name


def test_bundle_is_up_to_date_with_the_card_sources() -> None:
    """The committed bundle must equal a fresh build, or a card edit was not rebuilt."""
    build = _load_build_script()
    assert (FRONTEND_DIR / BUNDLE_FILE).read_text(encoding="utf-8") == build.build(), (
        "lighting-manager-cards.js is stale - run: python3 scripts/build_bundle.py"
    )


def test_bundle_contains_every_card_source() -> None:
    bundle = (FRONTEND_DIR / BUNDLE_FILE).read_text(encoding="utf-8")
    for name in CARD_FILES:
        assert f"===== {name} =====" in bundle


async def test_bundle_is_registered_as_extra_js_and_cache_policy(hass: HomeAssistant) -> None:
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()

    with patch.object(
        integration.panel_custom, "async_register_panel", AsyncMock()
    ), patch.object(integration.frontend, "add_extra_js_url") as add_url:
        await integration._async_register_panel(hass)

    # Only the bundle is auto-registered, with a ?v=<mtime> version.
    urls = [c.args[1] for c in add_url.call_args_list]
    assert len(urls) == 1
    assert urls[0].startswith(f"{STATIC_URL_PREFIX}/{BUNDLE_FILE}?v=")

    # Modern cores use async_register_static_paths(StaticPathConfig list);
    # older ones (incl. the sandbox's pinned 2024.3) fall back to
    # register_static_path(url, path) - accept whichever path was taken.
    if hass.http.async_register_static_paths.called:
        configs = hass.http.async_register_static_paths.call_args.args[0]
        served = {cfg.url_path: cfg.cache_headers for cfg in configs}
    else:
        calls = hass.http.register_static_path.call_args_list
        served = {c.args[0]: c.kwargs.get("cache_headers") for c in calls}

    # Bundle and individual cards (all reachable by a fixed address, e.g. a
    # Lovelace resource) are served uncached so they can never go stale;
    # only the panel, whose URL carries the version, is cached.
    assert served[f"{STATIC_URL_PREFIX}/{BUNDLE_FILE}"] is False
    for name in CARD_FILES:
        assert served[f"{STATIC_URL_PREFIX}/{name}"] is False
    assert served[PANEL_JS_MODULE] is True


async def test_ready_made_pages_are_registered_in_the_sidebar(hass: HomeAssistant) -> None:
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    register = AsyncMock()

    with patch.object(integration.panel_custom, "async_register_panel", register), patch.object(
        integration.frontend, "add_extra_js_url"
    ):
        await integration._async_register_panel(hass)

    pages = {c.kwargs["frontend_url_path"]: c.kwargs for c in register.call_args_list}
    assert set(pages) == {
        "lighting-manager-panel",
        "lighting-manager-lights",
        "lighting-manager-schedules",
    }
    for url_path, element, title, _icon in integration.EXTRA_PAGES:
        kwargs = pages[url_path]
        assert kwargs["webcomponent_name"] == element
        assert kwargs["sidebar_title"] == title
        # Everyone in the house can use them; the bundle defines the element.
        assert kwargs["require_admin"] is False
        assert kwargs["module_url"].startswith(f"{STATIC_URL_PREFIX}/{BUNDLE_FILE}?v=")


async def test_pages_can_be_switched_off_and_on_without_a_reload(hass: HomeAssistant) -> None:
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    register = AsyncMock()
    remove = MagicMock()

    with patch.object(integration.panel_custom, "async_register_panel", register), patch.object(
        integration.frontend, "async_remove_panel", remove
    ), patch.object(integration.frontend, "add_extra_js_url"):
        # Off from the start: only the admin panel is registered.
        await integration._async_register_panel(hass, show_pages=False)
        assert [c.kwargs["frontend_url_path"] for c in register.call_args_list] == [
            "lighting-manager-panel"
        ]

        entry = MagicMock()
        entry.options = {"show_sidebar_pages": True}
        await integration._async_options_updated(hass, entry)
        urls = {c.kwargs["frontend_url_path"] for c in register.call_args_list}
        assert urls == {"lighting-manager-panel", "lighting-manager-lights", "lighting-manager-schedules"}

        # Saving the same option twice does not register the pages twice.
        register.reset_mock()
        await integration._async_options_updated(hass, entry)
        register.assert_not_called()

        entry.options = {"show_sidebar_pages": False}
        await integration._async_options_updated(hass, entry)
        assert {c.args[1] for c in remove.call_args_list} == {
            "lighting-manager-lights",
            "lighting-manager-schedules",
        }

        # Switching off twice is harmless too.
        remove.reset_mock()
        await integration._async_options_updated(hass, entry)
        remove.assert_not_called()


async def test_options_flow_defaults_to_pages_on_and_saves_the_choice(hass: HomeAssistant) -> None:
    """Drives the flow class directly: going through hass.config_entries would
    set up the manifest's real dependencies (frontend etc.), which this older
    test environment cannot import."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.lighting_manager.config_flow import (
        LightingManagerConfigFlow,
        LightingManagerOptionsFlow,
    )
    from custom_components.lighting_manager.const import CONF_SHOW_SIDEBAR_PAGES, DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, data={})
    flow = LightingManagerConfigFlow.async_get_options_flow(entry)
    assert isinstance(flow, LightingManagerOptionsFlow)
    flow.hass = hass
    flow.handler = entry.entry_id

    form = await flow.async_step_init()
    assert form["type"] == "form"
    key = next(k for k in form["data_schema"].schema if k == CONF_SHOW_SIDEBAR_PAGES)
    assert key.default() is True

    entry.options = {CONF_SHOW_SIDEBAR_PAGES: False}
    form = await flow.async_step_init()
    key = next(k for k in form["data_schema"].schema if k == CONF_SHOW_SIDEBAR_PAGES)
    assert key.default() is False

    done = await flow.async_step_init({CONF_SHOW_SIDEBAR_PAGES: True})
    assert done["type"] == "create_entry"
    assert done["data"] == {CONF_SHOW_SIDEBAR_PAGES: True}
