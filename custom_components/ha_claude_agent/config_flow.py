"""Config flow for HA Claude Agent integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_API_KEY, DOMAIN

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_API_KEY): str,
    }
)


class HAClaudeAgentConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA Claude Agent."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(
                title="HA Claude Agent",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
        )
