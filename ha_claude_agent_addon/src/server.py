"""HTTP server for the HA Claude Agent add-on.

Exposes POST /query which runs a Claude Agent SDK query and returns
the result as JSON. Designed to be called by the HA custom integration.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from aiohttp import web

from claude_agent_sdk import (
    AssistantMessage,
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

from ha_client import HAClient
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
    # Anthropic API keys start with sk-ant-
    if auth_token.startswith("sk-ant-"):
        return {"ANTHROPIC_API_KEY": auth_token}
    return {"CLAUDE_CODE_OAUTH_TOKEN": auth_token}


async def handle_health(request: web.Request) -> web.Response:
    """GET /health — liveness check with API version."""
    return web.json_response({"status": "ok", "api_version": API_VERSION})


async def handle_query(request: web.Request) -> web.Response:
    """POST /query — run a Claude Agent SDK query and return the result."""
    try:
        data = await request.json()
        prompt: str = data["prompt"]
        model: str = data["model"]
        system_prompt: str = data["system_prompt"]
    except (json.JSONDecodeError, KeyError) as err:
        return web.json_response(
            {"error_code": "bad_request", "result_text": f"Invalid request: {err}"},
            status=400,
        )

    max_turns: int = data.get("max_turns", 10)
    effort: str = data.get("effort", "medium")
    session_id: str | None = data.get("session_id")
    exposed_entities: list[str] = data.get("exposed_entities", [])

    _LOGGER.info(
        "Query: model=%s, effort=%s, max_turns=%d, resume=%s",
        model, effort, max_turns, session_id is not None,
    )

    # ── Auth + HA client from app state (created once at startup) ──
    auth_env: dict[str, str] = request.app["auth_env"]
    ha_client: HAClient = request.app["ha_client"]

    try:
        mcp_tools = create_ha_tools(ha_client, exposed_entities)
        mcp_server = create_sdk_mcp_server(
            name=MCP_SERVER_NAME,
            version="1.0.0",
            tools=mcp_tools,
        )

        # ── Build SDK options ──
        tool_prefix = f"mcp__{MCP_SERVER_NAME}__"
        allowed_tools = [
            f"{tool_prefix}call_service",
            f"{tool_prefix}get_entity_state",
            f"{tool_prefix}list_entities",
            "WebFetch",
            "WebSearch",
        ]

        options = ClaudeAgentOptions(
            model=model,
            system_prompt=system_prompt,
            mcp_servers={MCP_SERVER_NAME: mcp_server},
            tools=allowed_tools,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
            env=auth_env,
            permission_mode="dontAsk",
            effort=effort,
        )

        if session_id:
            options.resume = session_id

        # ── Run query loop ──
        new_session_id: str | None = None
        text_parts: list[str] = []
        result_text = ""
        error_code: str | None = None
        cost_usd: float | None = None
        num_turns: int | None = None

        async for message in query(prompt=prompt, options=options):
            # Capture session ID
            if (
                isinstance(message, SystemMessage)
                and message.subtype == "init"
            ):
                new_session_id = message.data.get("session_id")
                _LOGGER.info("Session started: %s", new_session_id)

            elif isinstance(message, AssistantMessage):
                # Check for API-level errors
                if message.error:
                    error_code = message.error
                    _LOGGER.warning("Assistant error: %s", message.error)
                # Accumulate text
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
                    message.subtype, num_turns, cost_usd,
                )
                if message.subtype == "success":
                    result_text = message.result or ""
                else:
                    error_code = message.subtype
                    errors = getattr(message, "errors", [])
                    result_text = (
                        "; ".join(errors) if errors else message.subtype
                    )

        # Fall back to accumulated text if no result message text
        if not result_text and text_parts:
            _LOGGER.warning("No ResultMessage text, falling back to accumulated text blocks")
            result_text = "\n\n".join(text_parts)

        # If only an assistant-level error and no text, surface it
        if not result_text and error_code:
            result_text = f"Claude error: {error_code}"

    except CLINotFoundError:
        _LOGGER.error("Claude Code CLI not found in container")
        error_code = "cli_not_found"
        result_text = (
            "Claude Code CLI not found in the add-on container. "
            "The add-on image may need to be rebuilt."
        )
        new_session_id = None
        cost_usd = None
        num_turns = None

    except ProcessError as err:
        _LOGGER.error("CLI process failed (exit %s): %s", err.exit_code, err)
        error_code = "process_error"
        result_text = (
            f"Claude Code process crashed (exit code {err.exit_code}). "
            "Check the add-on logs for details."
        )
        new_session_id = None
        cost_usd = None
        num_turns = None

    except CLIJSONDecodeError as err:
        _LOGGER.error("Failed to parse CLI response: %s", err)
        error_code = "parse_error"
        result_text = "Received an invalid response from Claude Code."
        new_session_id = None
        cost_usd = None
        num_turns = None

    except Exception:
        _LOGGER.exception("Unexpected error during query")
        error_code = "internal_error"
        result_text = "An unexpected error occurred in the add-on."
        new_session_id = None
        cost_usd = None
        num_turns = None

    return web.json_response(
        {
            "result_text": result_text or None,
            "session_id": new_session_id,
            "cost_usd": cost_usd,
            "num_turns": num_turns,
            "error_code": error_code,
        }
    )


async def on_startup(app: web.Application) -> None:
    """Initialize shared resources at startup."""
    # Read auth token once
    addon_options = _read_addon_options()
    auth_token = addon_options.get("auth_token", "")
    app["auth_env"] = _build_auth_env(auth_token)

    # Validate SUPERVISOR_TOKEN is available
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        _LOGGER.error(
            "SUPERVISOR_TOKEN not set — HA REST API calls will fail. "
            "Is the add-on running inside the Supervisor?"
        )
        supervisor_token = ""

    # Create shared HA client (reused across requests)
    app["ha_client"] = HAClient(
        base_url="http://supervisor/core",
        token=supervisor_token,
    )


async def on_cleanup(app: web.Application) -> None:
    """Clean up shared resources on shutdown."""
    ha_client: HAClient | None = app.get("ha_client")
    if ha_client:
        await ha_client.close()


def create_app() -> web.Application:
    """Create and return the aiohttp application."""
    app = web.Application(client_max_size=1024 * 1024)  # 1MB max request
    app.router.add_get("/health", handle_health)
    app.router.add_post("/query", handle_query)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    _LOGGER.info("Starting HA Claude Agent add-on on port %d", port)
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=port)
