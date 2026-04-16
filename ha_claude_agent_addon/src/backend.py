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
from collections.abc import AsyncGenerator
from typing import Any, Protocol, runtime_checkable

from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    query,
)

from ha_client import HAClient
from models import QueryRequest
from serialization import exception_to_dict, to_jsonable
from tools_claude import create_ha_tools_claude

_LOGGER = logging.getLogger(__name__)

MCP_SERVER_NAME = "homeassistant"


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


@runtime_checkable
class Backend(Protocol):
    """Interface each backend implements."""

    name: str  # "claude" | "openai"

    async def stream_query(
        self,
        req: QueryRequest,
        ha_client: HAClient,
    ) -> AsyncGenerator[str, None]:
        ...


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
    ) -> AsyncGenerator[str, None]:
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
