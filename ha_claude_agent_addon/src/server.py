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
    ClaudeAgentOptions,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
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
        _LOGGER.info(
            "Auth configured: %s=%s...%s", env_key, auth_token[:7], auth_token[-4:]
        )
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
) -> AsyncGenerator[str]:
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
