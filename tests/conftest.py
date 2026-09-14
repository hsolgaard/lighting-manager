"""Shared fixtures for the Lighting Manager test suite.

Version note (see the Feasibility Review's "Live test results" and
deployment notes): this suite runs against `pytest-homeassistant-
custom-component==0.13.108`, which pins `homeassistant==2024.3.1`.
That's the newest combination this environment's package index can
resolve - Hans's real instance runs HA 2026.7.0, well over a year
ahead. These tests validate the integration's own logic (and are
exactly the kind of thing that would have caught both real bugs found
during manual testing - the Label id scheme, and countdown.async_start
never calling light.turn_on) but they are not a substitute for
exercising the integration against a current HA core; the live
websocket testing already done against Hans's actual instance is what
covers that gap for the parts it exercised.
"""
from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components/lighting_manager discoverable by hass's loader."""
    yield
