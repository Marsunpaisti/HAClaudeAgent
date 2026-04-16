"""Conversation platform for HA Claude Agent."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import aiohttp
import openai
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKError,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    SystemMessage,
)
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
from homeassistant.helpers.dispatcher import async_dispatcher_send
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
    SIGNAL_USAGE_UPDATED,
)
from .helpers import build_system_prompt
from .models import QueryRequest
from .openai_events import OpenAIInitEvent, OpenAIResultEvent
from .stream import sdk_stream
from .stream_filters import SourcesFilter, StreamingFilterProcessor
from .usage import UsagePayload

# openai-agents event types used for matching; import lazily-guarded so a
# missing openai-agents install (unlikely in practice, but possible during
# testing) degrades gracefully to Claude-only behavior.
try:
    from agents import RawResponsesStreamEvent, RunItemStreamEvent
except ImportError:
    RawResponsesStreamEvent = None  # type: ignore[assignment,misc]
    RunItemStreamEvent = None  # type: ignore[assignment,misc]

_LOGGER = logging.getLogger(__name__)

# Error messages keyed by:
#   - SDK exception class name (CLINotFoundError, ProcessError, ...)
#   - ResultMessage subtype (error_max_turns, error_max_budget_usd, ...)
#   - AssistantMessageError value (authentication_failed, billing_error, ...)
#   - Transport-layer error code (addon_unreachable)
_ERROR_MESSAGES: dict[str, str] = {
    # SDK exceptions
    "CLINotFoundError": (
        "Claude Code CLI not found in the add-on container. Try restarting the add-on."
    ),
    "ProcessError": "Claude Code process crashed. Check the add-on logs.",
    "CLIConnectionError": (
        "Could not connect to Claude Code CLI. Check the add-on logs."
    ),
    "CLIJSONDecodeError": "Received an invalid response from Claude. Try again.",
    "ClaudeSDKError": "An unexpected error occurred in the add-on.",
    # ResultMessage error subtypes
    "error_max_turns": (
        "Used all tool turns and couldn't finish. "
        "Try a simpler request or increase the max turns setting."
    ),
    "error_max_budget_usd": "This request hit the spending limit.",
    "error_during_execution": "Something went wrong while processing.",
    # AssistantMessage.error values
    "authentication_failed": (
        "Claude authentication failed. Check the auth token in the add-on settings."
    ),
    "billing_error": "Billing issue — check your account at console.anthropic.com.",
    "rate_limit": "Rate limited. Please wait a moment and try again.",
    "invalid_request": "The request to Claude was invalid.",
    "server_error": "Claude's servers returned an error. Please try again.",
    "unknown": "An unknown error occurred.",
    # Transport layer
    "addon_unreachable": (
        "Cannot reach the HA Claude Agent add-on. Is the add-on installed and running?"
    ),
    "stream_interrupted": (
        "The connection to the add-on was interrupted mid-stream. Please try again."
    ),
    # OpenAI exceptions
    "openai_auth_failed": (
        "OpenAI authentication failed. Check the API key in the add-on settings."
    ),
    "openai_rate_limit": ("OpenAI rate limit hit. Please wait a moment and try again."),
    "openai_server_error": (
        "The model provider returned a server error. Please try again."
    ),
    "openai_invalid_model": (
        "The configured model was not accepted by the provider. "
        "Check the model name in the conversation agent settings."
    ),
    "openai_connection_error": (
        "Could not reach the OpenAI-compatible endpoint. "
        "Check the base URL in the add-on settings."
    ),
}


def _map_openai_exception_to_key(err: BaseException) -> str | None:
    """Return the `_ERROR_MESSAGES` key for an openai.* exception, or None."""
    if isinstance(err, openai.AuthenticationError):
        return "openai_auth_failed"
    if isinstance(err, openai.RateLimitError):
        return "openai_rate_limit"
    if isinstance(err, openai.NotFoundError):
        return "openai_invalid_model"
    if isinstance(err, openai.APIConnectionError):
        return "openai_connection_error"
    # Catch-all for remaining openai.APIError subclasses (InternalServerError,
    # etc.). Keep this check last — more specific subclasses should match
    # before this catch.
    if isinstance(err, openai.APIError):
        return "openai_server_error"
    return None


@dataclass
class _StreamResult:
    """Mutable holder for stream side-effects consumed by the delta adapter."""

    session_id: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    result_error_subtype: str | None = None  # ResultMessage.subtype if != "success"
    assistant_error: str | None = None  # AssistantMessage.error if set
    usage_dict: dict[str, Any] | None = None  # Raw ResultMessage.usage dict


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

        # Build request payload
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

        addon_url = runtime_data.addon_url
        http_session = async_get_clientsession(self.hass)
        result_state = _StreamResult()

        t_request_start = time.monotonic()
        t_first_token: float | None = None
        stream_started = False
        try:
            async with http_session.post(
                f"{addon_url}/query",
                json=request.model_dump(exclude_none=True),
                timeout=aiohttp.ClientTimeout(total=QUERY_TIMEOUT_SECONDS),
                headers={"Accept": "text/event-stream"},
            ) as resp:
                resp.raise_for_status()
                stream_started = True
                async for _content in chat_log.async_add_delta_content_stream(
                    user_input.agent_id,
                    _deltas_from_sdk_stream(resp, result_state),
                ):
                    if t_first_token is None:
                        t_first_token = time.monotonic()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.error(
                "Add-on request failed (stream_started=%s): %s", stream_started, err
            )
            key = "stream_interrupted" if stream_started else "addon_unreachable"
            return self._error_response(
                _ERROR_MESSAGES[key],
                chat_log,
                user_input.language,
            )
        except CLINotFoundError as err:
            _LOGGER.error("Claude CLI not found: %s", err)
            return self._error_response(
                _ERROR_MESSAGES["CLINotFoundError"], chat_log, user_input.language
            )
        except ProcessError as err:
            _LOGGER.error("Claude process crashed: exit=%s", err.exit_code)
            return self._error_response(
                _ERROR_MESSAGES["ProcessError"], chat_log, user_input.language
            )
        except CLIConnectionError as err:
            _LOGGER.error("Claude CLI connection error: %s", err)
            return self._error_response(
                _ERROR_MESSAGES["CLIConnectionError"],
                chat_log,
                user_input.language,
            )
        except CLIJSONDecodeError as err:
            _LOGGER.error("Claude CLI JSON decode error: %s", err)
            return self._error_response(
                _ERROR_MESSAGES["CLIJSONDecodeError"],
                chat_log,
                user_input.language,
            )
        except ClaudeSDKError as err:
            _LOGGER.error("Unknown SDK error: %s", err)
            return self._error_response(
                _ERROR_MESSAGES["ClaudeSDKError"], chat_log, user_input.language
            )
        except openai.APIError as err:
            key = _map_openai_exception_to_key(err) or "openai_server_error"
            _LOGGER.error("OpenAI error (%s): %s", type(err).__name__, err)
            return self._error_response(
                _ERROR_MESSAGES[key],
                chat_log,
                user_input.language,
            )

        t_total = time.monotonic() - t_request_start
        ttft_str = (
            f"{t_first_token - t_request_start:.2f}s"
            if t_first_token is not None
            else "n/a"
        )
        _LOGGER.info(
            "Stream complete: session=%s, cost=$%s, turns=%s, "
            "ttft=%s, total=%.2fs, "
            "result_error=%s, assistant_error=%s",
            result_state.session_id,
            result_state.cost_usd,
            result_state.num_turns,
            ttft_str,
            t_total,
            result_state.result_error_subtype,
            result_state.assistant_error,
        )

        # Store session mapping BEFORE returning error responses — even on
        # soft errors, we want the user's prior conversation context to be
        # preserved for retry. The session is still valid on Claude's side;
        # it's only the current turn that failed.
        if result_state.session_id:
            runtime_data.sessions[chat_log.conversation_id] = result_state.session_id

        # Dispatch usage signal to sensor platform. Fires even on soft errors
        # (error_max_turns, assistant_error) — the API still billed for those.
        # cost_usd is the presence proxy: if it is None, no ResultMessage was
        # received, so there is nothing to record.
        if result_state.cost_usd is not None:
            usage_payload = _usage_from_state(result_state)
            async_dispatcher_send(
                self.hass,
                SIGNAL_USAGE_UPDATED,
                self.subentry.subentry_id,
                usage_payload,
            )

        # Soft errors: ResultMessage with error subtype, or AssistantMessage.error
        if result_state.result_error_subtype:
            msg = _ERROR_MESSAGES.get(
                result_state.result_error_subtype,
                f"Query failed: {result_state.result_error_subtype}",
            )
            return self._error_response(msg, chat_log, user_input.language)
        if result_state.assistant_error:
            msg = _ERROR_MESSAGES.get(
                result_state.assistant_error,
                f"Assistant error: {result_state.assistant_error}",
            )
            return self._error_response(msg, chat_log, user_input.language)

        # Build HA response
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


async def _deltas_from_sdk_stream(
    resp: aiohttp.ClientResponse,
    state: _StreamResult,
) -> AsyncIterator[AssistantContentDeltaDict]:
    """Route to the right per-backend delta adapter.

    Peeks the first typed message to decide which backend's event model
    is in play, then dispatches. Both adapters share ``_StreamResult``.
    """
    stream = sdk_stream(resp)
    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        return

    if _is_openai_event(first):
        async for delta in _deltas_from_openai_with_first(first, stream, state):
            yield delta
    else:
        async for delta in _deltas_from_claude_with_first(first, stream, state):
            yield delta


def _is_openai_event(obj: object) -> bool:
    """Return True if `obj` is one of the openai-agents wire types."""
    if isinstance(obj, (OpenAIInitEvent, OpenAIResultEvent)):
        return True
    if RawResponsesStreamEvent is not None and isinstance(obj, RawResponsesStreamEvent):
        return True
    return RunItemStreamEvent is not None and isinstance(obj, RunItemStreamEvent)


async def _deltas_from_claude_with_first(
    first,
    stream,
    state: _StreamResult,
) -> AsyncIterator[AssistantContentDeltaDict]:
    """Claude-path delta adapter. Body is the pre-refactor
    ``_deltas_from_sdk_stream`` logic, with the iteration sourced from
    ``chain([first], stream)`` instead of iterating ``sdk_stream(resp)``
    directly."""
    processor = StreamingFilterProcessor([SourcesFilter()])
    role_yielded = False

    async def _iter():
        yield first
        async for m in stream:
            yield m

    async for message in _iter():
        match message:
            case StreamEvent(event=ev):
                delta = _delta_from_anthropic_event(ev)
                if delta is None:
                    continue
                if "content" in delta:
                    filtered = processor.feed(delta["content"])
                    if not filtered:
                        continue
                    delta = {"content": filtered}
                if not role_yielded:
                    yield {"role": "assistant"}
                    role_yielded = True
                yield delta

            case SystemMessage(subtype="init", data=data):
                state.session_id = data.get("session_id") or state.session_id

            case ResultMessage(
                session_id=sid,
                subtype=subtype,
                total_cost_usd=cost,
                num_turns=turns,
                usage=usage_dict,
            ):
                state.session_id = sid or state.session_id
                state.cost_usd = cost
                state.num_turns = turns
                state.usage_dict = usage_dict
                if subtype != "success":
                    state.result_error_subtype = subtype

            case AssistantMessage(error=error) if error is not None:
                state.assistant_error = error

            case RateLimitEvent(rate_limit_info=info):
                level = logging.WARNING if info.status != "allowed" else logging.DEBUG
                _LOGGER.log(
                    level,
                    "Claude rate limit: status=%s type=%s utilization=%s",
                    info.status,
                    info.rate_limit_type,
                    info.utilization,
                )

            case _:
                pass

    final = processor.flush()
    if final:
        if not role_yielded:
            yield {"role": "assistant"}
            role_yielded = True
        yield {"content": final}


async def _deltas_from_openai_with_first(
    first,
    stream,
    state: _StreamResult,
) -> AsyncIterator[AssistantContentDeltaDict]:
    """OpenAI-path delta adapter — prepends the already-peeked ``first``
    item back onto ``stream`` and delegates to ``_deltas_from_openai``."""

    async def _iter(_resp):
        yield first
        async for m in stream:
            yield m

    async for delta in _deltas_from_openai(resp=None, state=state, sdk_stream=_iter):
        yield delta


async def _deltas_from_openai(
    resp,
    state: _StreamResult,
    *,
    sdk_stream=sdk_stream,  # injectable for tests
) -> AsyncIterator[AssistantContentDeltaDict]:
    """Map openai-agents events to ChatLog deltas + populate _StreamResult."""
    role_yielded = False

    async for item in sdk_stream(resp):
        match item:
            case OpenAIInitEvent(session_id=sid):
                state.session_id = sid

            case OpenAIResultEvent(input_tokens=ti, output_tokens=to, error=err):
                state.usage_dict = {
                    "input_tokens": ti,
                    "output_tokens": to,
                }
                if err:
                    state.assistant_error = err

            case _ if RawResponsesStreamEvent is not None and isinstance(
                item, RawResponsesStreamEvent
            ):
                # Nested .data is the raw OpenAI Responses streaming event.
                delta = _delta_from_openai_response_event(item.data)
                if delta is None:
                    continue
                if not role_yielded:
                    yield {"role": "assistant"}
                    role_yielded = True
                yield delta

            case _ if RunItemStreamEvent is not None and isinstance(
                item, RunItemStreamEvent
            ):
                # High-level items (tool_called, tool_output,
                # message_output_created). We don't yield deltas here — the
                # text already came via RawResponsesStreamEvent. Logged for
                # visibility.
                _LOGGER.debug(
                    "OpenAI run item: %s",
                    getattr(item, "name", type(item).__name__),
                )

            case _:
                pass


def _delta_from_openai_response_event(data) -> AssistantContentDeltaDict | None:
    """Map an OpenAI Responses streaming event (dict or Pydantic model)
    to a ChatLog delta.

    The `data` field of ``RawResponsesStreamEvent`` is a Responses API
    event object; when it represents an output-text delta, we yield
    ``{"content": str}``. Other event shapes (start, done, tool deltas,
    ...) are ignored — downstream types already cover those channels.
    """
    # Accept both a dict (post-serialization round-trip) and the openai
    # SDK's typed event. Try attribute first, fall back to dict key.
    evt_type = getattr(data, "type", None) or (
        data.get("type") if isinstance(data, dict) else None
    )
    if evt_type == "response.output_text.delta":
        text = getattr(data, "delta", None) or (
            data.get("delta") if isinstance(data, dict) else None
        )
        if isinstance(text, str) and text:
            return {"content": text}
    return None


def _delta_from_anthropic_event(
    event: dict,
) -> AssistantContentDeltaDict | None:
    """Map a raw Anthropic stream event dict to a ChatLog delta, or None."""
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


def _usage_from_state(state: _StreamResult) -> UsagePayload:
    """Build a UsagePayload from the already-captured _StreamResult.

    Kept as a small bridge because the pure helper in ``usage.py`` takes
    a ``ResultMessage``, but at dispatch time we only have the extracted
    fields. Mirrors the default-handling semantics of ``_usage_from_result``.
    """
    usage = state.usage_dict or {}
    cost = state.cost_usd if state.cost_usd is not None else 0.0
    return UsagePayload(
        cost_usd=float(cost),
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
    )
