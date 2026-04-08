"""HTTP server for the HA Claude Agent add-on.

Exposes POST /query which runs a Claude Agent SDK query and returns
the result as JSON. Designed to be called by the HA custom integration.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from claude_agent_sdk import (
    AssistantMessage,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ClaudeAgentOptions,
    ProcessError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    create_sdk_mcp_server,
    query,
)
from fastapi import FastAPI
from ha_client import HAClient
from models import QueryRequest, QueryResponse
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
API_VERSION = 1


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
    if auth_token.startswith("sk-ant-"):
        return {"ANTHROPIC_API_KEY": auth_token}
    return {"CLAUDE_CODE_OAUTH_TOKEN": auth_token}


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


@app.post("/query", response_model=QueryResponse)
async def handle_query(body: QueryRequest) -> QueryResponse:
    """Run a Claude Agent SDK query and return the result."""
    _LOGGER.info(
        "Query: model=%s, effort=%s, max_turns=%d, resume=%s",
        body.model,
        body.effort,
        body.max_turns,
        body.session_id is not None,
    )

    auth_env: dict[str, str] = app.state.auth_env
    ha_client: HAClient = app.state.ha_client

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
            stderr=lambda line: _LOGGER.debug("CLI stderr: %s", line),
        )

        if body.session_id:
            options.resume = body.session_id

        new_session_id: str | None = None
        text_parts: list[str] = []
        result_text = ""
        error_code: str | None = None
        cost_usd: float | None = None
        num_turns: int | None = None

        async for message in query(prompt=body.prompt, options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                new_session_id = message.data.get("session_id")
                _LOGGER.info("Session started: %s", new_session_id)

            elif isinstance(message, AssistantMessage):
                if message.error:
                    error_code = message.error
                    _LOGGER.warning("Assistant error: %s", message.error)
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
                    elif hasattr(block, "name"):
                        _LOGGER.info("Tool call: %s", block.name)

            elif isinstance(message, ResultMessage):
                if message.session_id:
                    new_session_id = message.session_id
                cost_usd = message.total_cost_usd
                num_turns = message.num_turns
                _LOGGER.info(
                    "Result: subtype=%s, turns=%s, cost=$%s",
                    message.subtype,
                    num_turns,
                    cost_usd,
                )
                if message.subtype == "success":
                    result_text = message.result or ""
                else:
                    error_code = message.subtype
                    errors = getattr(message, "errors", [])
                    result_text = "; ".join(errors) if errors else message.subtype

        if not result_text and text_parts:
            _LOGGER.warning(
                "No ResultMessage text, falling back to accumulated text blocks"
            )
            result_text = "\n\n".join(text_parts)

        if not result_text and error_code:
            result_text = f"Claude error: {error_code}"

    except CLINotFoundError:
        _LOGGER.error("Claude Code CLI not found in container")
        return QueryResponse(
            error_code="cli_not_found",
            result_text=(
                "Claude Code CLI not found in the add-on container. "
                "The add-on image may need to be rebuilt."
            ),
        )

    except ProcessError as err:
        _LOGGER.error("CLI process failed (exit %s): %s", err.exit_code, err)
        return QueryResponse(
            error_code="process_error",
            result_text=(
                f"Claude Code process crashed (exit code {err.exit_code}). "
                "Check the add-on logs for details."
            ),
        )

    except CLIConnectionError as err:
        _LOGGER.error("CLI connection error: %s", err)
        return QueryResponse(
            error_code="cli_connection_error",
            result_text=(
                "Could not connect to Claude Code CLI. "
                "Check the add-on logs for details."
            ),
        )

    except CLIJSONDecodeError as err:
        _LOGGER.error("Failed to parse CLI response: %s", err)
        return QueryResponse(
            error_code="parse_error",
            result_text="Received an invalid response from Claude Code.",
        )

    except Exception as err:
        _LOGGER.exception("Unexpected error during query")
        return QueryResponse(
            error_code="internal_error",
            result_text=f"An unexpected error occurred in the add-on: {err}",
        )

    return QueryResponse(
        result_text=result_text or None,
        session_id=new_session_id,
        cost_usd=cost_usd,
        num_turns=num_turns,
        error_code=error_code,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    _LOGGER.info("Starting HA Claude Agent add-on on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
