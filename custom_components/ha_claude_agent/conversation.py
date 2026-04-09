"""Conversation platform for HA Claude Agent."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import aiohttp
from homeassistant.components import conversation
from homeassistant.components.conversation import (
    AssistantContentDeltaDict,
    ChatLog,
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
    ConversationResult,
)
from homeassistant.components.homeassistant.exposed_entities import (
    async_should_expose,
)
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import intent
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
from .models import QueryRequest

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
        "Claude authentication failed. Check the auth token in the add-on settings."
    ),
    "billing_error": ("Billing issue — check your account at console.anthropic.com."),
    "rate_limit": "Rate limited. Please wait a moment and try again.",
    "cli_not_found": (
        "Claude Code CLI not found in the add-on container. Try restarting the add-on."
    ),
    "process_error": ("Claude Code process crashed. Check the add-on logs."),
    "cli_connection_error": (
        "Could not connect to Claude Code CLI. Check the add-on logs."
    ),
    "parse_error": ("Received an invalid response from Claude. Try again."),
    "internal_error": ("An unexpected error occurred in the add-on."),
    "addon_unreachable": (
        "Cannot reach the HA Claude Agent add-on. Is the add-on installed and running?"
    ),
    "stream_interrupted": (
        "The connection to the add-on was interrupted. Please try again."
    ),
}


class _StreamState:
    """Mutable state carried across the SSE-consumer generator."""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.cost_usd: float | None = None
        self.num_turns: int | None = None
        self.error_code: str | None = None
        self.error_message: str | None = None


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
    _attr_supports_streaming = True
    _attr_supported_features = ConversationEntityFeature.CONTROL

    def __init__(self, config_entry: ConfigEntry, subentry: ConfigSubentry) -> None:
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
        intent_response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, message)
        return ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
        )

    def _get_exposed_entity_ids(self) -> list[str]:
        """Return entity IDs exposed to the conversation agent."""
        return [
            state.entity_id
            for state in self.hass.states.async_all()
            if async_should_expose(self.hass, "conversation", state.entity_id)
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
        system_prompt = build_system_prompt(
            self.hass, user_prompt, location=runtime_data.location
        )

        session_id: str | None = None
        if user_input.conversation_id:
            session_id = runtime_data.sessions.get(user_input.conversation_id)

        effort = self.subentry.data.get(CONF_THINKING_EFFORT, DEFAULT_THINKING_EFFORT)
        max_turns = int(self.subentry.data.get(CONF_MAX_TURNS, DEFAULT_MAX_TURNS))

        _LOGGER.info(
            "Handling message: model=%s, effort=%s, resume=%s",
            model,
            effort,
            session_id is not None,
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

        # ── Open SSE stream to the add-on ──
        addon_url = runtime_data.addon_url
        http_session = async_get_clientsession(self.hass)
        state = _StreamState()

        try:
            async with http_session.post(
                f"{addon_url}/query",
                json=request.model_dump(exclude_none=True),
                timeout=aiohttp.ClientTimeout(total=QUERY_TIMEOUT_SECONDS),
                headers={"Accept": "text/event-stream"},
            ) as resp:
                resp.raise_for_status()
                async for _content in chat_log.async_add_delta_content_stream(
                    user_input.agent_id,
                    _transform_stream(resp, state),
                ):
                    # The ChatLog accumulates deltas internally; we just need
                    # to drive the generator to completion.
                    pass
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.error("Add-on request failed: %s", err)
            return self._error_response(
                _ERROR_MESSAGES["addon_unreachable"],
                chat_log,
                user_input.language,
            )

        _LOGGER.info(
            "Stream complete: error=%s, session=%s, cost=$%s, turns=%s",
            state.error_code,
            state.session_id,
            state.cost_usd,
            state.num_turns,
        )

        # ── Handle stream-level errors ──
        if state.error_code:
            msg = _ERROR_MESSAGES.get(
                state.error_code,
                state.error_message or f"Add-on error: {state.error_code}",
            )
            return self._error_response(msg, chat_log, user_input.language)

        # ── Store session mapping ──
        if state.session_id:
            runtime_data.sessions[chat_log.conversation_id] = state.session_id

        # ── Build HA response ──
        # The ChatLog already has the assistant content from the delta stream.
        # For the intent response, pull the spoken text from the last assistant
        # message in the chat log.
        speech = _last_assistant_text(chat_log) or "I have no response."

        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(speech)
        return ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=False,
        )


def _last_assistant_text(chat_log: ChatLog) -> str:
    """Return the text content of the most recent assistant message, or ''."""
    for content in reversed(chat_log.content):
        if content.role == "assistant" and content.content:
            return content.content
    return ""


async def _transform_stream(
    resp: aiohttp.ClientResponse,
    state: _StreamState,
) -> AsyncGenerator[AssistantContentDeltaDict]:
    """Read SSE events from the add-on and yield ChatLog deltas.

    Side-effects: stashes session/result/error metadata onto `state`.
    """
    role_yielded = False

    async for event_type, data in _parse_sse(resp):
        if event_type == "stream":
            delta = _map_stream_event(data)
            if delta is None:
                continue
            # Ensure we open an assistant message before the first text delta.
            if not role_yielded:
                yield {"role": "assistant"}
                role_yielded = True
            yield delta

        elif event_type == "session":
            state.session_id = data.get("session_id")

        elif event_type == "result":
            state.session_id = data.get("session_id") or state.session_id
            state.cost_usd = data.get("cost_usd")
            state.num_turns = data.get("num_turns")
            error_code = data.get("error_code")
            if error_code:
                state.error_code = error_code

        elif event_type == "error":
            state.error_code = data.get("error_code") or "internal_error"
            state.error_message = data.get("message")
            # No more events after error — return to close the generator.
            return


def _map_stream_event(event: dict[str, Any]) -> AssistantContentDeltaDict | None:
    """Map a raw SDK StreamEvent.event dict to a ChatLog delta, or None."""
    if event.get("type") != "content_block_delta":
        return None
    delta = event.get("delta") or {}
    delta_type = delta.get("type")
    if delta_type == "text_delta":
        text = delta.get("text", "")
        return {"content": text} if text else None
    if delta_type == "thinking_delta":
        thinking = delta.get("thinking", "")
        return {"thinking_content": thinking} if thinking else None
    return None


async def _parse_sse(
    resp: aiohttp.ClientResponse,
) -> AsyncGenerator[tuple[str, dict[str, Any]]]:
    """Parse an SSE stream from an aiohttp response.

    Yields (event_type, data_dict) tuples. Skips events without a valid
    `event:` and `data:` pair. Handles only the simple single-line
    `data:` format emitted by our add-on.
    """
    event_type: str | None = None
    data_line: str | None = None

    async for raw_line in resp.content:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if line == "":
            # End of event: emit if complete.
            if event_type is not None and data_line is not None:
                try:
                    data = json.loads(data_line)
                except json.JSONDecodeError:
                    _LOGGER.warning(
                        "Bad SSE data payload for event %s: %r", event_type, data_line
                    )
                else:
                    if isinstance(data, dict):
                        yield event_type, data
            event_type = None
            data_line = None
            continue
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_line = line[5:].strip()
        # Lines starting with `:` (comments) or anything else are ignored.
