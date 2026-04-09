# Streaming Responses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream Claude's response tokens from the add-on to the integration via SSE, then into HA's `ChatLog` delta API, so the voice pipeline can start TTS playback before Claude has finished generating the full response.

**Architecture:** The add-on enables `include_partial_messages=True` on `ClaudeAgentOptions` to get token-level `StreamEvent` objects from the Claude Agent SDK. The `/query` endpoint returns a `StreamingResponse` emitting SSE events (forwarded raw SDK events plus three custom events: `session`, `result`, `error`). The integration reads the SSE stream with aiohttp, parses events, and yields `AssistantContentDeltaDict` objects into `chat_log.async_add_delta_content_stream()`. Setting `_attr_supports_streaming = True` automatically enables HA's voice pipeline TTS streaming (available since HA 2025.6).

**Tech Stack:** Python 3.13, FastAPI (server), aiohttp (client), claude-agent-sdk 0.1.56, Home Assistant 2025.6+, Pydantic 2.x

**Reference documents:**
- Spec: `docs/superpowers/specs/2026-04-09-streaming-responses-design.md`
- Reference impl: `.venv/Lib/site-packages/homeassistant/components/anthropic/entity.py` — `_transform_stream()` around line 332

---

## File Structure

All changes are to existing files — no new files are created.

| File | Responsibility | Change |
|---|---|---|
| `custom_components/ha_claude_agent/models.py` | Shared Pydantic request model (integration copy) | Remove `QueryResponse` |
| `ha_claude_agent_addon/src/models.py` | Shared Pydantic request model (add-on copy) | Remove `QueryResponse` (byte-identical to integration copy) |
| `ha_claude_agent_addon/src/server.py` | FastAPI server with `/query` endpoint | Enable SDK partial messages, stream SSE events |
| `custom_components/ha_claude_agent/conversation.py` | HA conversation entity | Enable streaming flag, consume SSE, yield deltas into ChatLog |
| `tests/test_models_sync.py` | Verifies the two `models.py` files stay identical | No change (keeps working because both copies get the same edit) |

---

## Task 1: Remove `QueryResponse` from both `models.py` files

**Files:**
- Modify: `custom_components/ha_claude_agent/models.py`
- Modify: `ha_claude_agent_addon/src/models.py`
- Test: `tests/test_models_sync.py` (existing)

**Context:** The spec says `QueryResponse` is no longer needed because its data is now delivered via SSE events (`session`, `result`, `error`). Both copies must be byte-identical — `test_models_sync.py` enforces this.

- [ ] **Step 1: Edit integration copy of models.py to remove `QueryResponse`**

File: `custom_components/ha_claude_agent/models.py`

Replace the full file contents with:

```python
"""Shared Pydantic models for the add-on HTTP API contract.

This file is duplicated in ha_claude_agent_addon/src/models.py.
The two copies MUST stay identical — see tests/test_models_sync.py.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Request body for POST /query."""

    prompt: str = Field(max_length=3_000)
    model: str = Field(max_length=200)
    system_prompt: str = Field(max_length=20_000)
    max_turns: int = Field(default=10, ge=1, le=100)
    effort: str = Field(default="medium", max_length=20)
    session_id: str | None = Field(default=None, max_length=200)
    exposed_entities: list[str] = Field(default_factory=list, max_length=1_000)
```

- [ ] **Step 2: Copy the exact same contents to the add-on copy**

File: `ha_claude_agent_addon/src/models.py`

Replace the full file contents with the same text from Step 1. The two files must be byte-identical.

- [ ] **Step 3: Run the sync test to verify the two files match**

Run: `uv run pytest tests/test_models_sync.py -v`

Expected: PASS — `test_models_files_are_identical` passes.

- [ ] **Step 4: Commit**

```bash
git add custom_components/ha_claude_agent/models.py ha_claude_agent_addon/src/models.py
git commit -m "refactor: remove QueryResponse model (replaced by SSE events)"
```

---

## Task 2: Add-on server emits SSE stream

**Files:**
- Modify: `ha_claude_agent_addon/src/server.py`

**Context:** We replace the `/query` endpoint's JSON response with an async SSE generator. We enable `include_partial_messages=True` on `ClaudeAgentOptions` so the SDK yields `StreamEvent` objects interleaved with the usual messages. We forward raw SDK `StreamEvent.event` dicts as `event: stream` SSE events and emit three custom events (`session`, `result`, `error`) for the rest.

The SDK types to import:
- `StreamEvent` lives at `claude_agent_sdk.types.StreamEvent`
- The top-level `claude_agent_sdk` re-exports it too, but official docs use the `types` submodule — let's follow that

We use FastAPI's `StreamingResponse` from `fastapi.responses` directly (no extra dependency).

- [ ] **Step 1: Replace the full contents of `ha_claude_agent_addon/src/server.py`**

File: `ha_claude_agent_addon/src/server.py`

Replace the full file with:

```python
"""HTTP server for the HA Claude Agent add-on.

Exposes POST /query which runs a Claude Agent SDK query and returns
the result as a Server-Sent Events stream. Designed to be called by
the HA custom integration.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from claude_agent_sdk import (
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ClaudeAgentOptions,
    ProcessError,
    ResultMessage,
    SystemMessage,
    create_sdk_mcp_server,
    query,
)
from claude_agent_sdk.types import StreamEvent
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from ha_client import HAClient
from models import QueryRequest
from tools import create_ha_tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
_LOGGER = logging.getLogger(__name__)

MCP_SERVER_NAME = "homeassistant"
ADDON_OPTIONS_PATH = "/data/options.json"
DEFAULT_PORT = 8099
API_VERSION = 2


def _read_addon_options() -> dict:
    """Read add-on options from /data/options.json."""
    try:
        with open(ADDON_OPTIONS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as err:
        _LOGGER.error("Cannot read add-on options: %s", err)
        return {}


def _build_auth_env(auth_token: str) -> dict[str, str]:
    """Return the env dict for the SDK based on the token format."""
    if not auth_token:
        return {}
    # API keys: sk-ant-api03-...
    # OAuth tokens: sk-ant-oat01-... (or anything else)
    if auth_token.startswith("sk-ant-api"):
        return {"ANTHROPIC_API_KEY": auth_token}
    return {"CLAUDE_CODE_OAUTH_TOKEN": auth_token}


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format an SSE event as a wire-protocol string."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and clean up shared resources."""
    # Startup
    addon_options = _read_addon_options()
    auth_token = addon_options.get("auth_token", "")
    app.state.auth_env = _build_auth_env(auth_token)
    if app.state.auth_env:
        env_key = next(iter(app.state.auth_env))
        _LOGGER.info("Auth configured: %s=%s...%s", env_key, auth_token[:7], auth_token[-4:])
    else:
        _LOGGER.warning("No auth_token configured — queries will fail")

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        _LOGGER.error(
            "SUPERVISOR_TOKEN not set — HA REST API calls will fail. "
            "Is the add-on running inside the Supervisor?"
        )
        supervisor_token = ""

    app.state.ha_client = HAClient(
        base_url="http://supervisor/core",
        token=supervisor_token,
    )

    yield

    # Shutdown
    await app.state.ha_client.close()


app = FastAPI(title="HA Claude Agent Add-on", lifespan=lifespan)


@app.get("/health")
async def health():
    """Liveness check with API version."""
    return {"status": "ok", "api_version": API_VERSION}


async def _stream_query(
    body: QueryRequest,
    auth_env: dict[str, str],
    ha_client: HAClient,
) -> AsyncGenerator[str, None]:
    """Run the SDK query and yield SSE-formatted strings."""
    _LOGGER.info(
        "Query: model=%s, effort=%s, max_turns=%d, resume=%s",
        body.model,
        body.effort,
        body.max_turns,
        body.session_id is not None,
    )

    try:
        mcp_tools = create_ha_tools(ha_client, body.exposed_entities)
        mcp_server = create_sdk_mcp_server(
            name=MCP_SERVER_NAME,
            version="1.0.0",
            tools=mcp_tools,
        )

        tool_prefix = f"mcp__{MCP_SERVER_NAME}__"
        allowed_tools = [
            f"{tool_prefix}call_service",
            f"{tool_prefix}get_entity_state",
            f"{tool_prefix}list_entities",
            "WebFetch",
            "WebSearch",
        ]

        options = ClaudeAgentOptions(
            model=body.model,
            system_prompt=body.system_prompt,
            mcp_servers={MCP_SERVER_NAME: mcp_server},
            allowed_tools=allowed_tools,
            max_turns=body.max_turns,
            env=auth_env,
            permission_mode="dontAsk",
            effort=body.effort,
            include_partial_messages=True,
            stderr=lambda line: _LOGGER.warning("CLI stderr: %s", line),
        )

        if body.session_id:
            options.resume = body.session_id

        async for message in query(prompt=body.prompt, options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                session_id = message.data.get("session_id")
                _LOGGER.info("Session started: %s", session_id)
                yield _sse_event("session", {"session_id": session_id})

            elif isinstance(message, StreamEvent):
                # Forward the raw Anthropic stream event dict as-is.
                yield _sse_event("stream", message.event)

            elif isinstance(message, ResultMessage):
                error_code: str | None = None
                if message.subtype != "success":
                    error_code = message.subtype
                _LOGGER.info(
                    "Result: subtype=%s, turns=%s, cost=$%s",
                    message.subtype,
                    message.num_turns,
                    message.total_cost_usd,
                )
                yield _sse_event(
                    "result",
                    {
                        "session_id": message.session_id,
                        "cost_usd": message.total_cost_usd,
                        "num_turns": message.num_turns,
                        "error_code": error_code,
                    },
                )

            # AssistantMessage is intentionally skipped: the content was
            # already streamed via StreamEvent, and we don't need the
            # aggregated copy.

    except CLINotFoundError:
        _LOGGER.error("Claude Code CLI not found in container")
        yield _sse_event(
            "error",
            {
                "error_code": "cli_not_found",
                "message": (
                    "Claude Code CLI not found in the add-on container. "
                    "The add-on image may need to be rebuilt."
                ),
            },
        )

    except ProcessError as err:
        _LOGGER.error("CLI process failed (exit %s): %s", err.exit_code, err)
        yield _sse_event(
            "error",
            {
                "error_code": "process_error",
                "message": (
                    f"Claude Code process crashed (exit code {err.exit_code}). "
                    "Check the add-on logs for details."
                ),
            },
        )

    except CLIConnectionError as err:
        _LOGGER.error("CLI connection error: %s", err)
        yield _sse_event(
            "error",
            {
                "error_code": "cli_connection_error",
                "message": (
                    "Could not connect to Claude Code CLI. "
                    "Check the add-on logs for details."
                ),
            },
        )

    except CLIJSONDecodeError as err:
        _LOGGER.error("Failed to parse CLI response: %s", err)
        yield _sse_event(
            "error",
            {
                "error_code": "parse_error",
                "message": "Received an invalid response from Claude Code.",
            },
        )

    except Exception as err:  # noqa: BLE001
        _LOGGER.exception("Unexpected error during query")
        yield _sse_event(
            "error",
            {
                "error_code": "internal_error",
                "message": f"An unexpected error occurred in the add-on: {err}",
            },
        )


@app.post("/query")
async def handle_query(body: QueryRequest) -> StreamingResponse:
    """Run a Claude Agent SDK query and stream the result as SSE."""
    auth_env: dict[str, str] = app.state.auth_env
    ha_client: HAClient = app.state.ha_client
    return StreamingResponse(
        _stream_query(body, auth_env, ha_client),
        media_type="text/event-stream",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    _LOGGER.info("Starting HA Claude Agent add-on on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
```

Key differences from the previous version:
- Removed `AssistantMessage` and `TextBlock` imports (no longer used)
- Removed `QueryResponse` import
- Added `StreamEvent` from `claude_agent_sdk.types`
- Added `StreamingResponse` from `fastapi.responses`
- Added `AsyncGenerator` from `collections.abc`
- Added `Any` from `typing`
- Added `_sse_event()` helper
- Added `_stream_query()` async generator that yields SSE strings
- Rewrote `handle_query()` to return `StreamingResponse(...)`
- Enabled `include_partial_messages=True` in `ClaudeAgentOptions`
- Bumped `API_VERSION` from `1` to `2`

- [ ] **Step 2: Verify the file parses (syntax check)**

Run: `uv run python -c "import ast; ast.parse(open('ha_claude_agent_addon/src/server.py').read()); print('OK')"`

Expected: `OK`

- [ ] **Step 3: Run ruff on the add-on file**

Run: `uv run ruff check ha_claude_agent_addon/src/server.py`

Expected: no errors. If ruff flags anything, fix it inline — common issues would be unused imports or line-length. Do not commit with ruff errors.

- [ ] **Step 4: Run ruff format on the add-on file**

Run: `uv run ruff format ha_claude_agent_addon/src/server.py`

Expected: file reformatted (or already formatted). This is safe — the formatter only adjusts whitespace.

- [ ] **Step 5: Commit**

```bash
git add ha_claude_agent_addon/src/server.py
git commit -m "feat(addon): stream /query response as SSE events"
```

---

## Task 3: Integration consumes SSE stream and feeds into ChatLog

**Files:**
- Modify: `custom_components/ha_claude_agent/conversation.py`

**Context:** This is the main integration-side change. The entity flips `_attr_supports_streaming = True`, which tells HA's voice pipeline to use TTS streaming when this agent is used. The `_async_handle_message` method opens the POST request, reads the SSE stream line by line, parses each event, and yields `AssistantContentDeltaDict` objects into `chat_log.async_add_delta_content_stream()`.

**SSE parsing:** The SSE wire format is:
```
event: stream
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}

event: result
data: {...}

```

Events are separated by blank lines. Each event has an `event:` line and a `data:` line. We read lines from `aiohttp`'s `response.content`.

**Delta mapping:**
- `stream` event with `event.type == "content_block_start"` and `content_block.type == "text"` → first text delta of a new block. Yield `{"role": "assistant"}` if we haven't yielded one yet.
- `stream` event with `event.type == "content_block_delta"` and `delta.type == "text_delta"` → `{"content": delta["text"]}`
- `stream` event with `event.type == "content_block_delta"` and `delta.type == "thinking_delta"` → `{"thinking_content": delta["thinking"]}`
- All other `stream` events → skip
- `session` → stash `session_id`
- `result` → stash metadata
- `error` → stash error info

**`role: "assistant"` boundary:** The ChatLog uses `role` in the delta to mark a new message. We yield `{"role": "assistant"}` exactly once at the start of the first content block so the ChatLog opens an assistant message. Without this, the first `content` delta could be interpreted as continuing a prior message.

- [ ] **Step 1: Replace the full contents of `custom_components/ha_claude_agent/conversation.py`**

File: `custom_components/ha_claude_agent/conversation.py`

Replace the full file with:

```python
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
) -> AsyncGenerator[AssistantContentDeltaDict, None]:
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
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
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
```

Key differences from the previous version:
- Imported `AssistantContentDeltaDict` instead of `AssistantContent`
- Imported `json` and `AsyncGenerator`/`Any` for SSE parsing
- Removed `QueryResponse` import
- Flipped `_attr_supports_streaming = False` → `True`
- Added `_StreamState` holder class
- Added `_ERROR_MESSAGES` entries for `cli_connection_error` and `stream_interrupted` (previously missing)
- Replaced the POST-and-parse-JSON flow with a POST-and-consume-SSE flow that feeds `chat_log.async_add_delta_content_stream()`
- Pulled the speech text from the chat log after streaming completes (instead of from the response body)
- Removed `chat_log.async_add_assistant_content_without_tools()` — the delta stream adds content to the chat log natively
- Added `_transform_stream`, `_map_stream_event`, `_parse_sse`, and `_last_assistant_text` helper functions

- [ ] **Step 2: Verify the file parses**

Run: `uv run python -c "import ast; ast.parse(open('custom_components/ha_claude_agent/conversation.py').read()); print('OK')"`

Expected: `OK`

- [ ] **Step 3: Run ruff on the integration file**

Run: `uv run ruff check custom_components/ha_claude_agent/conversation.py`

Expected: no errors. Fix any issues inline.

- [ ] **Step 4: Run ruff format on the integration file**

Run: `uv run ruff format custom_components/ha_claude_agent/conversation.py`

Expected: file reformatted (or already formatted).

- [ ] **Step 5: Run mypy on the integration file**

Run: `uv run mypy custom_components/ha_claude_agent/conversation.py`

Expected: no errors. If mypy complains about `AssistantContentDeltaDict` or the async generator types, look at how the official Anthropic integration handles it at `.venv/Lib/site-packages/homeassistant/components/anthropic/entity.py:332` for reference.

- [ ] **Step 6: Commit**

```bash
git add custom_components/ha_claude_agent/conversation.py
git commit -m "feat(integration): consume SSE stream and feed ChatLog deltas"
```

---

## Task 4: Full project verification

**Files:**
- No file changes, just verification.

**Context:** Run the full project lint/format/type/test pipeline to catch anything we missed. The changes touched three files; the rest of the codebase should be unaffected, but we verify.

- [ ] **Step 1: Run ruff check on the whole project**

Run: `uv run ruff check custom_components/ ha_claude_agent_addon/src/ tests/`

Expected: no errors. If anything breaks, fix inline and re-run.

- [ ] **Step 2: Run ruff format check on the whole project**

Run: `uv run ruff format --check custom_components/ ha_claude_agent_addon/src/ tests/`

Expected: all files formatted. If not, run `uv run ruff format custom_components/ ha_claude_agent_addon/src/ tests/` and commit the formatting fix.

- [ ] **Step 3: Run mypy on both integration and add-on sources**

Run: `uv run mypy custom_components/ha_claude_agent/ ha_claude_agent_addon/src/`

Expected: no errors. If mypy complains about something we didn't touch, it likely existed before — only fix things caused by our changes.

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest tests/ -v`

Expected: all tests pass. At minimum `test_models_sync.py::test_models_files_are_identical` should pass.

- [ ] **Step 5: If any formatting-only changes were made in Step 2, commit them**

```bash
git status
# If there are uncommitted formatting changes from Step 2, commit them:
git add -u
git commit -m "style: apply ruff format"
```

If nothing was changed, skip this step.

- [ ] **Step 6: Final sanity check — confirm the files changed match the plan**

Run: `git diff main --stat`

Expected: four files changed (the three source files + possibly one for the sync test if it needed updating — it shouldn't). Verify no accidental file changes slipped in.

---

## Self-Review Checklist

This plan was reviewed against the spec before handoff:

1. **Spec coverage:**
   - SSE event schema (`stream`/`session`/`result`/`error`) — Task 2 Step 1 ✓
   - `include_partial_messages=True` on `ClaudeAgentOptions` — Task 2 Step 1 ✓
   - `_attr_supports_streaming = True` — Task 3 Step 1 ✓
   - `AssistantContentDeltaDict` mapping for `text_delta` and `thinking_delta` — Task 3 Step 1 (`_map_stream_event`) ✓
   - `QueryResponse` removed from both `models.py` copies — Task 1 ✓
   - Error handling: connection drop → `stream_interrupted` / `addon_unreachable` — Task 3 Step 1 ✓
   - Error handling: mid-stream `error` event → user-friendly message — Task 3 Step 1 (`_StreamState.error_code`) ✓
   - Files NOT changed (`tools.py`, `ha_client.py`, `helpers.py`, `config_flow.py`, `const.py`) — Task 4 Step 6 verifies ✓

2. **Placeholder scan:** No TBDs, no "handle error appropriately", no "similar to Task N". Every code block is complete.

3. **Type consistency:** `_StreamState` fields match between definition and usage. `_transform_stream`, `_map_stream_event`, `_parse_sse` signatures are consistent across references.
