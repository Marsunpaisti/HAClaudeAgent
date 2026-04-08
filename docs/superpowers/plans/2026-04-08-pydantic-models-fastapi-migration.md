# Pydantic Shared Models + FastAPI Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-built dict API contract between the add-on server and integration with Pydantic models, and migrate the add-on from aiohttp to FastAPI.

**Architecture:** Two identical `models.py` files define `QueryRequest` and `QueryResponse` — one in the integration package (for HACS), one in the add-on src (for Docker). The add-on server is rewritten from aiohttp to FastAPI, gaining automatic request validation. The integration switches from raw dicts to model instances. A sync test ensures the two copies never diverge.

**Tech Stack:** Pydantic v2 (ships with HA), FastAPI, uvicorn, pytest

**Spec:** `docs/superpowers/specs/2026-04-08-pydantic-models-fastapi-migration-design.md`

---

### Task 1: Create shared models.py (both copies)

**Files:**
- Create: `custom_components/ha_claude_agent/models.py`
- Create: `ha_claude_agent_addon/src/models.py`

- [ ] **Step 1: Create the integration copy**

Create `custom_components/ha_claude_agent/models.py`:

```python
"""Shared Pydantic models for the add-on HTTP API contract.

This file is duplicated in ha_claude_agent_addon/src/models.py.
The two copies MUST stay identical — see tests/test_models_sync.py.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Request body for POST /query."""

    prompt: str
    model: str
    system_prompt: str
    max_turns: int = 10
    effort: str = "medium"
    session_id: str | None = None
    exposed_entities: list[str] = Field(default_factory=list)


class QueryResponse(BaseModel):
    """Response body from POST /query."""

    result_text: str | None = None
    session_id: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    error_code: str | None = None
```

- [ ] **Step 2: Copy to add-on**

Create `ha_claude_agent_addon/src/models.py` with **byte-identical** content — copy the file exactly.

- [ ] **Step 3: Commit**

```bash
git add custom_components/ha_claude_agent/models.py ha_claude_agent_addon/src/models.py
git commit -m "feat: add shared Pydantic models for add-on API contract"
```

---

### Task 2: Add sync test

**Files:**
- Create: `tests/test_models_sync.py`

- [ ] **Step 1: Create tests directory and sync test**

Create `tests/test_models_sync.py`:

```python
"""Verify the two models.py copies stay in sync."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_models_files_are_identical():
    """The integration and add-on models.py must be byte-identical."""
    integration = REPO_ROOT / "custom_components" / "ha_claude_agent" / "models.py"
    addon = REPO_ROOT / "ha_claude_agent_addon" / "src" / "models.py"

    assert integration.exists(), f"Missing: {integration}"
    assert addon.exists(), f"Missing: {addon}"
    assert integration.read_text(encoding="utf-8") == addon.read_text(encoding="utf-8"), (
        "models.py files have diverged — edit one, copy to the other"
    )
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_models_sync.py -v`
Expected: PASS — both files were copied identically in Task 1.

- [ ] **Step 3: Commit**

```bash
git add tests/test_models_sync.py
git commit -m "test: add sync check for shared models.py copies"
```

---

### Task 3: Update add-on dependencies

**Files:**
- Modify: `ha_claude_agent_addon/requirements.txt`

- [ ] **Step 1: Update requirements.txt**

Replace the contents of `ha_claude_agent_addon/requirements.txt` with:

```
claude-agent-sdk>=0.1.0
aiohttp>=3.9.0
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
```

`aiohttp` stays — it's used by `ha_client.py` for outbound HA REST API calls.

- [ ] **Step 2: Update requirements_dev.txt**

Replace the contents of `requirements_dev.txt` with:

```
homeassistant
voluptuous
claude_agent_sdk
pydantic
fastapi
pytest
```

- [ ] **Step 3: Commit**

```bash
git add ha_claude_agent_addon/requirements.txt requirements_dev.txt
git commit -m "build: add fastapi, uvicorn, pytest to dependencies"
```

---

### Task 4: Rewrite add-on server to FastAPI

**Files:**
- Modify: `ha_claude_agent_addon/src/server.py`

This is the largest task. The Claude SDK query loop logic is preserved exactly — only the web framework wrapper changes.

- [ ] **Step 1: Rewrite server.py**

Replace `ha_claude_agent_addon/src/server.py` with:

```python
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
from fastapi import FastAPI

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
async def handle_query(request: QueryRequest) -> QueryResponse:
    """Run a Claude Agent SDK query and return the result."""
    _LOGGER.info(
        "Query: model=%s, effort=%s, max_turns=%d, resume=%s",
        request.model, request.effort, request.max_turns,
        request.session_id is not None,
    )

    auth_env: dict[str, str] = app.state.auth_env
    ha_client: HAClient = app.state.ha_client

    try:
        mcp_tools = create_ha_tools(ha_client, request.exposed_entities)
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
            model=request.model,
            system_prompt=request.system_prompt,
            mcp_servers={MCP_SERVER_NAME: mcp_server},
            tools=allowed_tools,
            allowed_tools=allowed_tools,
            max_turns=request.max_turns,
            env=auth_env,
            permission_mode="dontAsk",
            effort=request.effort,
        )

        if request.session_id:
            options.resume = request.session_id

        new_session_id: str | None = None
        text_parts: list[str] = []
        result_text = ""
        error_code: str | None = None
        cost_usd: float | None = None
        num_turns: int | None = None

        async for message in query(prompt=request.prompt, options=options):
            if (
                isinstance(message, SystemMessage)
                and message.subtype == "init"
            ):
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

    except CLIJSONDecodeError as err:
        _LOGGER.error("Failed to parse CLI response: %s", err)
        return QueryResponse(
            error_code="parse_error",
            result_text="Received an invalid response from Claude Code.",
        )

    except Exception:
        _LOGGER.exception("Unexpected error during query")
        return QueryResponse(
            error_code="internal_error",
            result_text="An unexpected error occurred in the add-on.",
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
```

- [ ] **Step 2: Verify the rewrite preserves all behavior**

Read through the new server and check:
- Same two endpoints (`/health`, `/query`)
- Same auth env logic (`_read_addon_options`, `_build_auth_env`)
- Same SDK query loop (message iteration, session ID capture, text accumulation, error handling)
- Same error catch hierarchy (`CLINotFoundError`, `ProcessError`, `CLIJSONDecodeError`, generic `Exception`)
- Lifecycle creates `HAClient` on startup, closes on shutdown
- Entry point uses `uvicorn.run()` — s6 run script (`exec python3 /app/server.py`) still works

- [ ] **Step 3: Commit**

```bash
git add ha_claude_agent_addon/src/server.py
git commit -m "refactor: migrate add-on server from aiohttp to FastAPI"
```

---

### Task 5: Update integration to use Pydantic models

**Files:**
- Modify: `custom_components/ha_claude_agent/conversation.py`

- [ ] **Step 1: Add models import**

In `custom_components/ha_claude_agent/conversation.py`, add the import after the existing local imports (after line 38):

```python
from .models import QueryRequest, QueryResponse
```

- [ ] **Step 2: Replace request dict with QueryRequest**

Replace the `payload` dict construction and HTTP call (lines 188–213) in `_async_handle_message`. Find this block:

```python
        payload = {
            "prompt": user_input.text,
            "model": model,
            "system_prompt": system_prompt,
            "max_turns": max_turns,
            "effort": effort,
            "session_id": session_id,
            "exposed_entities": self._get_exposed_entity_ids(),
        }

        # ── Call the add-on ──
        addon_url = runtime_data.addon_url
        http_session = async_get_clientsession(self.hass)

        try:
            async with http_session.post(
                f"{addon_url}/query",
                json=payload,
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
```

Replace with:

```python
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
```

- [ ] **Step 3: Replace response dict parsing with QueryResponse**

Find this block (the response processing section):

```python
        # ── Process response ──
        error_code = data.get("error_code")
        result_text = data.get("result_text") or ""
        new_session_id = data.get("session_id")

        _LOGGER.info(
            "Add-on response: error=%s, session=%s, cost=$%s, turns=%s",
            error_code,
            new_session_id,
            data.get("cost_usd"),
            data.get("num_turns"),
        )
```

Replace with:

```python
        # ── Process response ──
        response = QueryResponse.model_validate(data)

        _LOGGER.info(
            "Add-on response: error=%s, session=%s, cost=$%s, turns=%s",
            response.error_code,
            response.session_id,
            response.cost_usd,
            response.num_turns,
        )
```

Then update the remaining references from dict access to model attributes. Find:

```python
        # If error with no result text, show a user-friendly message
        if error_code and not result_text:
            msg = _ERROR_MESSAGES.get(
                error_code, f"Add-on error: {error_code}"
            )
            return self._error_response(
                msg, chat_log, user_input.language
            )

        # ── Store session mapping ──
        if new_session_id:
            runtime_data.sessions[chat_log.conversation_id] = (
                new_session_id
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
```

Replace with:

```python
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
```

- [ ] **Step 4: Commit**

```bash
git add custom_components/ha_claude_agent/conversation.py
git commit -m "refactor: use Pydantic models for add-on HTTP calls"
```

---

### Task 6: Final validation

- [ ] **Step 1: Run sync test**

Run: `pytest tests/test_models_sync.py -v`
Expected: PASS

- [ ] **Step 2: Verify no regressions in file structure**

Check that all expected files exist and no unexpected files were created:

```bash
ls custom_components/ha_claude_agent/models.py
ls ha_claude_agent_addon/src/models.py
ls ha_claude_agent_addon/src/server.py
ls tests/test_models_sync.py
```

- [ ] **Step 3: Verify imports resolve**

```bash
cd custom_components/ha_claude_agent && python -c "from models import QueryRequest, QueryResponse; print('OK')"
cd ha_claude_agent_addon/src && python -c "from models import QueryRequest, QueryResponse; print('OK')"
```

- [ ] **Step 4: Commit any remaining changes**

If requirements_dev.txt or other files have uncommitted changes:

```bash
git status
git add -A
git commit -m "chore: final cleanup for Pydantic + FastAPI migration"
```
