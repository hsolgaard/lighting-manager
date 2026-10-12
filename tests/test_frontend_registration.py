"""Frontend registration: bundled dashboard cards auto-load (PRD Revision §4.2/§4.3)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

import custom_components.lighting_manager as integration
from custom_components.lighting_manager import CARD_FILES, STATIC_URL_PREFIX

FRONTEND_DIR = Path(integration.__file__).parent / "frontend"


def test_every_declared_card_file_exists() -> None:
    for name in CARD_FILES:
        assert (FRONTEND_DIR / name).is_file(), name


async def test_cards_are_served_and_registered_as_extra_js(hass: HomeAssistant) -> None:
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()

    with patch.object(
        integration.panel_custom, "async_register_panel", AsyncMock()
    ), patch.object(integration.frontend, "add_extra_js_url") as add_url:
        await integration._async_register_panel(hass)

    urls = [c.args[1] for c in add_url.call_args_list]
    # Each URL carries a ?v=<mtime> cache-busting version.
    assert all("?v=" in u for u in urls)
    registered = {u.split("?")[0] for u in urls}
    assert registered == {f"{STATIC_URL_PREFIX}/{name}" for name in CARD_FILES}

    # Modern cores use async_register_static_paths(StaticPathConfig list);
    # older ones (incl. the sandbox's pinned 2024.3) fall back to
    # register_static_path(url, path) - accept whichever path was taken.
    if hass.http.async_register_static_paths.called:
        served = {
            cfg.url_path
            for cfg in hass.http.async_register_static_paths.call_args.args[0]
        }
    else:
        served = {c.args[0] for c in hass.http.register_static_path.call_args_list}
    assert registered <= served
