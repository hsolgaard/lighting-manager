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
