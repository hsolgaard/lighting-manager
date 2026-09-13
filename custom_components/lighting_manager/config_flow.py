"""Config flow for Lighting Manager.

Lighting Manager manages the whole house, not one device or account, so
the flow is deliberately trivial: confirm once, create a single config
entry, done. `single_config_entry` in manifest.json prevents a second
instance being added by mistake.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN


class LightingManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the (single-step) Lighting Manager setup flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Single confirmation step - no user input required.

        Open technical investigation #6 (PRD §38): registry-modifying
        actions triggered from the frontend panel should be gated to
        admin users, matching the convention HA's own Settings pages
        already use. That check lives in the websocket command handlers
        (see api.py), not here - setup itself doesn't need it.
        """
        if user_input is not None:
            return self.async_create_entry(title="Lighting Manager", data={})

        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
