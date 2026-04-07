"""Config flow for HA Claude Agent integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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
)
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .const import (
    CONF_ADDON_HOST,
    CONF_ADDON_PORT,
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_MAX_TURNS,
    CONF_PROMPT,
    CONF_TEMPERATURE,
    CONF_THINKING_EFFORT,
    DEFAULT_ADDON_HOST,
    DEFAULT_ADDON_PORT,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MAX_TURNS,
    DEFAULT_PROMPT,
    DEFAULT_TEMPERATURE,
    DEFAULT_THINKING_EFFORT,
    DOMAIN,
    THINKING_EFFORT_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)

MODELS = [
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-opus-4-6",
]

DEFAULT_SUBENTRY_DATA: dict[str, Any] = {
    CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
    CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
    CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
    CONF_THINKING_EFFORT: DEFAULT_THINKING_EFFORT,
    CONF_MAX_TURNS: DEFAULT_MAX_TURNS,
    CONF_PROMPT: DEFAULT_PROMPT,
}


class HAClaudeAgentConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA Claude Agent."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_host: str | None = None
        self._discovered_port: int | None = None

    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> ConfigFlowResult:
        """Handle Supervisor add-on discovery (auto-detect).

        The add-on publishes its host/port to the Supervisor discovery
        service on startup. This step receives that info automatically.
        """
        self._discovered_host = discovery_info.config.get("host")
        self._discovered_port = discovery_info.config.get(
            "port", DEFAULT_ADDON_PORT
        )

        _LOGGER.info(
            "Discovered add-on at %s:%s",
            self._discovered_host,
            self._discovered_port,
        )

        # Check we can actually reach it
        addon_url = (
            f"http://{self._discovered_host}:{self._discovered_port}"
        )
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                f"{addon_url}/health",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return self.async_abort(reason="cannot_connect")
        except (aiohttp.ClientError, TimeoutError):
            return self.async_abort(reason="cannot_connect")

        # Show confirmation step
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovered add-on setup."""
        if user_input is not None:
            return self.async_create_entry(
                title="HA Claude Agent",
                data={
                    CONF_ADDON_HOST: self._discovered_host,
                    CONF_ADDON_PORT: self._discovered_port,
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
            step_id="hassio_confirm",
            description_placeholders={
                "addon_url": (
                    f"http://{self._discovered_host}"
                    f":{self._discovered_port}"
                ),
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup — fallback when discovery unavailable."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_ADDON_HOST]
            port = int(user_input[CONF_ADDON_PORT])

            # Validate connectivity
            addon_url = f"http://{host}:{port}"
            session = async_get_clientsession(self.hass)
            try:
                async with session.get(
                    f"{addon_url}/health",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        errors["base"] = "cannot_connect"
            except (aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(
                    title="HA Claude Agent",
                    data={
                        CONF_ADDON_HOST: host,
                        CONF_ADDON_PORT: port,
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
                    vol.Required(
                        CONF_ADDON_HOST, default=DEFAULT_ADDON_HOST
                    ): TextSelector(TextSelectorConfig()),
                    vol.Required(
                        CONF_ADDON_PORT, default=DEFAULT_ADDON_PORT
                    ): int,
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
            data_schema=self._build_schema(
                subentry.data, subentry.title
            ),
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
                    default=defaults.get(
                        CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL
                    ),
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
                    CONF_THINKING_EFFORT,
                    default=defaults.get(
                        CONF_THINKING_EFFORT, DEFAULT_THINKING_EFFORT
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=THINKING_EFFORT_OPTIONS,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_MAX_TURNS,
                    default=defaults.get(
                        CONF_MAX_TURNS, DEFAULT_MAX_TURNS
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=50,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_PROMPT,
                    default=defaults.get(CONF_PROMPT, DEFAULT_PROMPT),
                ): TemplateSelector(TemplateSelectorConfig()),
            }
        )
