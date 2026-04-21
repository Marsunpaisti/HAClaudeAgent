"""Backend abstraction for the HA Claude Agent add-on.

Two implementations live here: ``ClaudeBackend`` wraps claude-agent-sdk
(preserves the existing behavior from server._stream_query); ``OpenAIBackend``
wraps openai-agents (added in a later task).

Both backends yield pre-formatted SSE strings — the FastAPI shell in
``server.py`` just pipes them into a StreamingResponse.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any, Protocol, runtime_checkable

import openai
from agents import Agent, ModelSettings, Runner
from agents.memory import SQLiteSession
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    query,
)
from ha_client import HAClient
from models import QueryRequest
from openai import AsyncOpenAI
from openai.types.shared.reasoning import Reasoning
from openai_events import OpenAIInitEvent, OpenAIResultEvent
from serialization import exception_to_dict, to_jsonable
from tools_claude import create_ha_tools_claude
from tools_openai import create_ha_tools_openai

_LOGGER = logging.getLogger(__name__)

MCP_SERVER_NAME = "homeassistant"


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


@runtime_checkable
class Backend(Protocol):
    """Interface each backend implements."""

    name: str  # "claude" | "openai"

    def stream_query(
        self,
        req: QueryRequest,
        ha_client: HAClient,
    ) -> AsyncGenerator[str]: ...


class ClaudeBackend:
    """Backend that wraps claude-agent-sdk. Behavior-identical to the
    pre-refactor ``server._stream_query``."""

    name = "claude"

    def __init__(self, auth_env: dict[str, str]) -> None:
        self._auth_env = auth_env

    async def stream_query(
        self,
        req: QueryRequest,
        ha_client: HAClient,
    ) -> AsyncGenerator[str]:
        _LOGGER.info(
            "Claude query: model=%s, effort=%s, max_turns=%d, resume=%s",
            req.model,
            req.effort,
            req.max_turns,
            req.session_id is not None,
        )

        try:
            mcp_tools = create_ha_tools_claude(ha_client, req.exposed_entities)
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
                model=req.model,
                system_prompt=req.system_prompt,
                mcp_servers={MCP_SERVER_NAME: mcp_server},
                allowed_tools=allowed_tools,
                max_turns=req.max_turns,
                env=self._auth_env,
                permission_mode="dontAsk",
                effort=req.effort,
                include_partial_messages=True,
                stderr=lambda line: _LOGGER.warning("CLI stderr: %s", line),
            )
            if req.session_id:
                options.resume = req.session_id

            async for message in query(prompt=req.prompt, options=options):
                yield _sse_event(type(message).__name__, to_jsonable(message))

        except GeneratorExit:
            raise
        except asyncio.CancelledError:
            raise
        except BaseException as err:  # noqa: BLE001
            _LOGGER.exception("Claude query failed")
            yield _sse_event("exception", exception_to_dict(err))


def _map_openai_exception_to_key(err: BaseException) -> str | None:
    """Map openai.* exceptions to `_ERROR_MESSAGES` keys used by the
    integration. Kept on the add-on side so the error-path goes through
    OpenAIResultEvent.error instead of the raising-exception channel
    (which sdk_stream converts into a terminal raise on the integration
    side and drops the terminal ResultEvent)."""
    if isinstance(err, openai.AuthenticationError):
        return "openai_auth_failed"
    if isinstance(err, openai.RateLimitError):
        return "openai_rate_limit"
    if isinstance(err, openai.NotFoundError):
        return "openai_invalid_model"
    if isinstance(err, openai.APIConnectionError):
        return "openai_connection_error"
    if isinstance(err, openai.APIError):
        return "openai_server_error"
    return None


def _openai_model_settings(effort: str) -> ModelSettings:
    reasoning_effort = {
        "low": "low",
        "medium": "medium",
        "high": "high",
        "max": "xhigh",
    }.get(effort, effort if effort in {"none", "minimal", "xhigh"} else "medium")
    return ModelSettings(reasoning=Reasoning(effort=reasoning_effort))


class OpenAIBackend:
    """Backend that wraps openai-agents for any OpenAI-compatible endpoint."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        sessions_db_path: str = "/data/sessions.db",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._sessions_db_path = sessions_db_path

    async def stream_query(
        self,
        req: QueryRequest,
        ha_client: HAClient,
    ) -> AsyncGenerator[str]:
        session_id = req.session_id or uuid.uuid4().hex
        _LOGGER.info(
            "OpenAI query: model=%s, effort=%s, max_turns=%d, session=%s, resumed=%s",
            req.model,
            req.effort,
            req.max_turns,
            session_id,
            req.session_id is not None,
        )

        # Leading init event — integration picks this up into its session cache.
        yield _sse_event(
            "OpenAIInitEvent",
            to_jsonable(OpenAIInitEvent(session_id=session_id)),
        )

        error_key: str | None = None
        input_tokens = 0
        output_tokens = 0

        try:
            client = AsyncOpenAI(base_url=self._base_url, api_key=self._api_key)
            tools = create_ha_tools_openai(ha_client, req.exposed_entities)
            agent = Agent(
                name="ha_assistant",
                instructions=req.system_prompt,
                model=OpenAIChatCompletionsModel(
                    model=req.model,
                    openai_client=client,
                ),
                model_settings=_openai_model_settings(req.effort),
                tools=tools,
            )
            session = SQLiteSession(
                session_id=session_id,
                db_path=self._sessions_db_path,
            )

            result = Runner.run_streamed(
                agent,
                req.prompt,
                session=session,
                max_turns=req.max_turns,
            )
            async for event in result.stream_events():
                yield _sse_event(type(event).__name__, to_jsonable(event))

            usage = getattr(result.context_wrapper, "usage", None)
            if usage is not None:
                input_tokens = getattr(usage, "input_tokens", 0) or 0
                output_tokens = getattr(usage, "output_tokens", 0) or 0

        except GeneratorExit:
            raise
        except asyncio.CancelledError:
            raise
        except BaseException as err:  # noqa: BLE001
            _LOGGER.exception("OpenAI query failed")
            # Route errors through the terminal ResultEvent — do NOT yield
            # an `exception` SSE event. The integration's sdk_stream would
            # raise on it and tear down the iterator before the terminal
            # ResultEvent could be consumed, losing usage + error signal.
            error_key = _map_openai_exception_to_key(err) or (
                f"{type(err).__name__}: {err}"
            )

        yield _sse_event(
            "OpenAIResultEvent",
            to_jsonable(
                OpenAIResultEvent(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    error=error_key,
                )
            ),
        )
