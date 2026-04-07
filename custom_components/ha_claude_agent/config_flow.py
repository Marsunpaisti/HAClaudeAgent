"""Config flow for HA Claude Agent integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TemplateSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_API_KEY,
    CONF_CHAT_MODEL,
    CONF_CLI_PATH,
    CONF_MAX_TOKENS,
    CONF_PROMPT,
    CONF_TEMPERATURE,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_MAX_TOKENS,
    DEFAULT_PROMPT,
    DEFAULT_TEMPERATURE,
    DOMAIN,
)

MODELS = [
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-opus-4-6",
]

DEFAULT_SUBENTRY_DATA: dict[str, Any] = {
    CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
    CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
    CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
    CONF_PROMPT: DEFAULT_PROMPT,
}


class HAClaudeAgentConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA Claude Agent."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — collect API key and optional CLI path."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY, "").strip()
            if not api_key:
                errors["base"] = "invalid_auth"
            else:
                return self.async_create_entry(
                    title="HA Claude Agent",
                    data={
                        CONF_API_KEY: api_key,
                        CONF_CLI_PATH: user_input.get(CONF_CLI_PATH, ""),
                    },
                    subentries=[
                        {
                            "subentry_type": "conversation",
                            "data": dict(DEFAULT_SUBENTRY_DATA),
                            "title": DEFAULT_CONVERSATION_NAME,
                        }
                    ],
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_CLI_PATH, default=""): str,
                }
            ),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentry types supported by this integration."""
        return {"conversation": ConversationSubentryFlowHandler}


class ConversationSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for conversation agents."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle adding a new conversation agent."""
        if user_input is not None:
            return self.async_create_entry(
                title=user_input.pop("name", DEFAULT_CONVERSATION_NAME),
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=self._build_schema(DEFAULT_SUBENTRY_DATA),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle reconfiguring an existing conversation agent."""
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=user_input.pop("name", subentry.title),
                data=user_input,
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._build_schema(subentry.data, subentry.title),
        )

    @staticmethod
    def _build_schema(
        defaults: dict[str, Any],
        default_name: str = DEFAULT_CONVERSATION_NAME,
    ) -> vol.Schema:
        """Build the subentry form schema with given defaults."""
        return vol.Schema(
            {
                vol.Required("name", default=default_name): str,
                vol.Optional(
                    CONF_CHAT_MODEL,
                    default=defaults.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=MODELS,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_MAX_TOKENS,
                    default=defaults.get(
                        CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=16384,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_TEMPERATURE,
                    default=defaults.get(
                        CONF_TEMPERATURE, DEFAULT_TEMPERATURE
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0,
                        max=2.0,
                        step=0.05,
                        mode=NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Optional(
                    CONF_PROMPT,
                    default=defaults.get(CONF_PROMPT, DEFAULT_PROMPT),
                ): TemplateSelector(TemplateSelectorConfig()),
            }
        )
