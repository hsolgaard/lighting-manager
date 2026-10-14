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
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_SHOW_SIDEBAR_PAGES, DOMAIN


class LightingManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the (single-step) Lighting Manager setup flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return LightingManagerOptionsFlow(config_entry)

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


class LightingManagerOptionsFlow(config_entries.OptionsFlow):
    """Configure: switch the ready-made Lights and Schedules sidebar pages on or off."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # Kept under our own name: newer Home Assistant versions provide
        # `self.config_entry` themselves and deprecate assigning it.
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._entry.options.get(CONF_SHOW_SIDEBAR_PAGES, True)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_SHOW_SIDEBAR_PAGES, default=current): bool}
            ),
        )
