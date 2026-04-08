"""Conversation platform for HA Claude Agent."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.components import conversation
from homeassistant.components.conversation import (
    AssistantContent,
    ChatLog,
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
    ConversationResult,
    async_should_expose,
)
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CHAT_MODEL,
    CONF_MAX_TURNS,
    CONF_PROMPT,
    CONF_THINKING_EFFORT,
    DEFAULT_CHAT_MODEL,
    DEFAULT_MAX_TURNS,
    DEFAULT_PROMPT,
    DEFAULT_THINKING_EFFORT,
    DOMAIN,
    QUERY_TIMEOUT_SECONDS,
)
from .helpers import build_system_prompt
from .models import QueryRequest, QueryResponse

_LOGGER = logging.getLogger(__name__)

# Error messages keyed by error_code from the add-on response
_ERROR_MESSAGES = {
    "error_max_turns": (
        "Used all tool turns and couldn't finish. "
        "Try a simpler request or increase the max turns setting."
    ),
    "error_max_budget_usd": "This request hit the spending limit.",
    "error_during_execution": "Something went wrong while processing.",
    "authentication_failed": (
        "Claude authentication failed. "
        "Check the auth token in the add-on settings."
    ),
    "billing_error": (
        "Billing issue — check your account at console.anthropic.com."
    ),
    "rate_limit": "Rate limited. Please wait a moment and try again.",
    "cli_not_found": (
        "Claude Code CLI not found in the add-on container. "
        "Try restarting the add-on."
    ),
    "process_error": (
        "Claude Code process crashed. Check the add-on logs."
    ),
    "parse_error": (
        "Received an invalid response from Claude. Try again."
    ),
    "internal_error": (
        "An unexpected error occurred in the add-on."
    ),
    "addon_unreachable": (
        "Cannot reach the HA Claude Agent add-on. "
        "Is the add-on installed and running?"
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up conversation entities from config subentries."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue
        async_add_entities(
            [HAClaudeAgentConversationEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class HAClaudeAgentConversationEntity(ConversationEntity):
    """HA Claude Agent conversation entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = False
    _attr_supported_features = ConversationEntityFeature.CONTROL

    def __init__(
        self, config_entry: ConfigEntry, subentry: ConfigSubentry
    ) -> None:
        """Initialize the entity."""
        self.entry = config_entry
        self.subentry = subentry
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Anthropic",
            model=subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def supported_languages(self) -> list[str] | str:
        """Return MATCH_ALL — Claude supports all languages."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register as a conversation agent when added to HA."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister as a conversation agent when removed."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    def _error_response(
        self,
        message: str,
        chat_log: ChatLog,
        language: str,
    ) -> ConversationResult:
        """Build an error ConversationResult."""
        intent_response = intent.IntentResponse(language=language)
        intent_response.async_set_error(
            intent.IntentResponseErrorCode.UNKNOWN, message
        )
        return ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
        )

    def _get_exposed_entity_ids(self) -> list[str]:
        """Return entity IDs exposed to the conversation agent."""
        return [
            state.entity_id
            for state in self.hass.states.async_all()
            if async_should_expose(
                self.hass, "conversation", state.entity_id
            )
        ]

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Handle a conversation turn by delegating to the add-on."""
        runtime_data = self.entry.runtime_data

        # ── Build request payload ──
        model = self.subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)
        user_prompt = self.subentry.data.get(CONF_PROMPT, DEFAULT_PROMPT)
        system_prompt = build_system_prompt(self.hass, user_prompt)

        session_id: str | None = None
        if user_input.conversation_id:
            session_id = runtime_data.sessions.get(
                user_input.conversation_id
            )

        effort = self.subentry.data.get(
            CONF_THINKING_EFFORT, DEFAULT_THINKING_EFFORT
        )
        max_turns = int(
            self.subentry.data.get(CONF_MAX_TURNS, DEFAULT_MAX_TURNS)
        )

        _LOGGER.info(
            "Handling message: model=%s, effort=%s, resume=%s",
            model, effort, session_id is not None,
        )

        request = QueryRequest(
            prompt=user_input.text,
            model=model,
            system_prompt=system_prompt,
            max_turns=max_turns,
            effort=effort,
            session_id=session_id,
            exposed_entities=self._get_exposed_entity_ids(),
        )

        # ── Call the add-on ──
        addon_url = runtime_data.addon_url
        http_session = async_get_clientsession(self.hass)

        try:
            async with http_session.post(
                f"{addon_url}/query",
                json=request.model_dump(exclude_none=True),
                timeout=aiohttp.ClientTimeout(
                    total=QUERY_TIMEOUT_SECONDS
                ),
            ) as resp:
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.error("Add-on request failed: %s", err)
            return self._error_response(
                _ERROR_MESSAGES["addon_unreachable"],
                chat_log,
                user_input.language,
            )

        # ── Process response ──
        response = QueryResponse.model_validate(data)

        _LOGGER.info(
            "Add-on response: error=%s, session=%s, cost=$%s, turns=%s",
            response.error_code,
            response.session_id,
            response.cost_usd,
            response.num_turns,
        )

        result_text = response.result_text or ""

        # If error with no result text, show a user-friendly message
        if response.error_code and not result_text:
            msg = _ERROR_MESSAGES.get(
                response.error_code,
                f"Add-on error: {response.error_code}",
            )
            return self._error_response(
                msg, chat_log, user_input.language
            )

        # ── Store session mapping ──
        if response.session_id:
            runtime_data.sessions[chat_log.conversation_id] = (
                response.session_id
            )

        # ── Build HA response ──
        if result_text:
            chat_log.async_add_assistant_content_without_tools(
                AssistantContent(
                    agent_id=user_input.agent_id,
                    content=result_text,
                )
            )

        intent_response = intent.IntentResponse(
            language=user_input.language
        )
        intent_response.async_set_speech(
            result_text or "I have no response."
        )
        return ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=False,
        )
