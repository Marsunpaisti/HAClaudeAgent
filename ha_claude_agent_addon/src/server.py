"""HTTP server for the HA Claude Agent add-on.

Reads options at startup, selects a Backend, and routes /query to it. The
backend yields SSE-formatted strings; this shell does no query logic.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from backend import Backend, ClaudeBackend, OpenAIBackend
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from ha_client import HAClient
from models import QueryRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
_LOGGER = logging.getLogger(__name__)

ADDON_OPTIONS_PATH = "/data/options.json"
DEFAULT_PORT = 8099
API_VERSION = 3  # bumped: /health now exposes `backend`


def _read_addon_options() -> dict:
    try:
        with open(ADDON_OPTIONS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as err:
        _LOGGER.error("Cannot read add-on options: %s", err)
        return {}


def _build_claude_auth_env(token: str) -> dict[str, str]:
    if not token:
        return {}
    if token.startswith("sk-ant-api"):
        return {"ANTHROPIC_API_KEY": token}
    return {"CLAUDE_CODE_OAUTH_TOKEN": token}


def _select_backend(options: dict) -> Backend:
    backend_name = (options.get("backend") or "claude").strip().lower()

    if backend_name == "claude":
        token = options.get("claude_auth_token") or options.get("auth_token") or ""
        if not token:
            raise RuntimeError(
                "Missing claude_auth_token — required when backend=claude."
            )
        if options.get("auth_token") and not options.get("claude_auth_token"):
            _LOGGER.warning(
                "Using legacy `auth_token` option; rename to `claude_auth_token` "
                "before the next minor release."
            )
        return ClaudeBackend(auth_env=_build_claude_auth_env(token))

    if backend_name == "openai":
        api_key = options.get("openai_api_key") or ""
        base_url = options.get("openai_base_url") or ""
        if not api_key:
            raise RuntimeError("Missing openai_api_key — required when backend=openai.")
        if not base_url:
            raise RuntimeError(
                "Missing openai_base_url — required when backend=openai. "
                "Example: https://generativelanguage.googleapis.com/v1beta/openai/"
            )
        return OpenAIBackend(api_key=api_key, base_url=base_url)

    raise RuntimeError(
        f"Unknown backend {backend_name!r}. Must be 'claude' or 'openai'."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    options = _read_addon_options()
    app.state.backend = _select_backend(options)
    _LOGGER.info("Selected backend: %s", app.state.backend.name)

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

    await app.state.ha_client.close()


app = FastAPI(title="HA Claude Agent Add-on", lifespan=lifespan)


@app.get("/health")
async def health():
    backend_name = getattr(getattr(app.state, "backend", None), "name", "unknown")
    return {"status": "ok", "api_version": API_VERSION, "backend": backend_name}


@app.post("/query")
async def handle_query(body: QueryRequest) -> StreamingResponse:
    backend: Backend = app.state.backend
    ha_client: HAClient = app.state.ha_client
    return StreamingResponse(
        backend.stream_query(body, ha_client),
        media_type="text/event-stream",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    _LOGGER.info("Starting HA Claude Agent add-on on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
