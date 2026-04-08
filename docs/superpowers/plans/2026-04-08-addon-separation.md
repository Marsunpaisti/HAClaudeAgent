# Add-on Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate the Claude Code CLI + SDK invocation into a dedicated HA add-on Docker container, keeping the HACS integration as a thin conversation agent that delegates to the add-on via HTTP.

**Architecture:** The HACS integration builds the system prompt (with exposed entities and HA context) and sends query requests to the add-on's HTTP API. The add-on runs the Claude Agent SDK with MCP tools that control HA via its REST API using the Supervisor-injected `SUPERVISOR_TOKEN`. The integration no longer depends on `claude-agent-sdk`, Node.js, or the Claude Code CLI.

**Tech Stack:** Python (aiohttp), claude-agent-sdk, Node.js 22 + Claude Code CLI, Docker (HA base-python Alpine image), s6-overlay

---

## API Contract

The add-on exposes a single HTTP endpoint for queries and a health check.

### `GET /health`

```json
{"status": "ok"}
```

### `POST /query`

**Request:**

```json
{
  "prompt": "turn on the living room lights",
  "model": "claude-sonnet-4-6",
  "system_prompt": "You are a voice assistant...\n\n## Exposed Entities\n...",
  "max_turns": 10,
  "effort": "medium",
  "session_id": null,
  "exposed_entities": ["light.living_room", "switch.kitchen"]
}
```

- `session_id`: null for new conversations, a previous session ID to resume
- `exposed_entities`: list of entity_ids the agent is allowed to control (computed by integration from `async_should_expose`)

**Response (always 200):**

```json
{
  "result_text": "I've turned on the living room lights.",
  "session_id": "sess_abc123",
  "cost_usd": 0.03,
  "num_turns": 2,
  "error_code": null
}
```

**Error codes:** `error_max_turns`, `error_max_budget_usd`, `error_during_execution`, `authentication_failed`, `billing_error`, `rate_limit`, `cli_not_found`, `process_error`, `parse_error`, `internal_error`

When `error_code` is set, `result_text` contains a human-readable error message. `session_id` may still be returned (useful for `error_max_turns` where the session is still valid).

---

## File Structure

### New files (Add-on)

| File | Purpose |
|------|---------|
| `repository.yaml` | Add-on repository metadata (allows users to add repo as an add-on store) |
| `ha_claude_agent_addon/config.yaml` | Add-on config: options schema, permissions, arch, ports |
| `ha_claude_agent_addon/Dockerfile` | Container: HA base-python + Node.js + Claude CLI + Python deps |
| `ha_claude_agent_addon/requirements.txt` | Python deps: `claude-agent-sdk`, `aiohttp` |
| `ha_claude_agent_addon/src/server.py` | aiohttp HTTP server: `/health`, `/query` endpoint, SDK query loop |
| `ha_claude_agent_addon/src/ha_client.py` | Async HA REST API client using `SUPERVISOR_TOKEN` |
| `ha_claude_agent_addon/src/tools.py` | MCP tool definitions proxying to HA REST API |
| `ha_claude_agent_addon/rootfs/etc/services.d/ha-claude-agent/run` | s6-overlay service: starts the Python server |
| `ha_claude_agent_addon/rootfs/etc/services.d/ha-claude-agent/finish` | s6-overlay cleanup: delay before restart |
| `ha_claude_agent_addon/translations/en.yaml` | Add-on UI strings |

### Modified files (Integration)

| File | Change |
|------|--------|
| `custom_components/ha_claude_agent/const.py` | Replace `CONF_API_KEY`/`CONF_CLI_PATH` with `CONF_ADDON_URL`, add `DEFAULT_ADDON_URL`, remove `MCP_SERVER_NAME` |
| `custom_components/ha_claude_agent/__init__.py` | Remove MCP server creation, update `HAClaudeAgentRuntimeData` |
| `custom_components/ha_claude_agent/conversation.py` | Replace SDK `query()` loop with HTTP POST to add-on |
| `custom_components/ha_claude_agent/config_flow.py` | Replace API key + CLI path form with add-on URL input + connectivity check |
| `custom_components/ha_claude_agent/manifest.json` | Remove `claude-agent-sdk` requirement, bump version to `0.4.0` |
| `custom_components/ha_claude_agent/strings.json` | Update config step strings for add-on URL |
| `custom_components/ha_claude_agent/translations/en.json` | Mirror strings.json changes |
| `custom_components/ha_claude_agent/helpers.py` | Remove `MCP_SERVER_NAME` import (get tool prefix from const or inline) |

### Deleted files

| File | Reason |
|------|--------|
| `custom_components/ha_claude_agent/tools.py` | MCP tools move to the add-on |

---

## Add-on Auth Design

The add-on needs two kinds of credentials:

1. **Claude auth** — `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`, 1-year validity) or `ANTHROPIC_API_KEY`. Configured in add-on options, stored in `/data/options.json`.

2. **HA REST API auth** — `SUPERVISOR_TOKEN`, automatically injected by the Supervisor into the container environment. Requires `homeassistant_api: true` in `config.yaml`. No user configuration needed.

The integration sends **no tokens** to the add-on. The add-on is self-sufficient.

---

## Integration Patterns

### Supervisor Discovery

The add-on publishes its host/port to the Supervisor discovery service on startup. The integration receives this automatically via `HassioServiceInfo` in its config flow — no manual URL entry needed. This is the same pattern Z-Wave JS uses.

**Flow:**
1. Add-on starts, publishes discovery: `{"service": "ha_claude_agent", "config": {"host": "...", "port": 8099}}`
2. Integration's config flow receives it in `async_step_hassio(discovery_info: HassioServiceInfo)`
3. Config entry is created with the discovered host/port
4. On each conversation turn, integration POSTs to `http://{host}:{port}/query`

The manual URL entry (`async_step_user`) is kept as a fallback for non-HAOS installations or if discovery fails.

### ConfigEntryNotReady

If the add-on isn't running when the integration loads, `async_setup_entry` raises `ConfigEntryNotReady`. HA retries with exponential backoff automatically. Entities show as "unavailable" in the meantime.

### Logging

No extra work needed. The Supervisor proxies add-on logs via `GET /api/hassio/addons/{slug}/logs`. Users see them in **Settings → Add-ons → HA Claude Agent → Log tab**. Integration logs go to HA's normal log. Both visible in the UI without any extra work.

---

## Tasks

### Task 1: Add-on repository skeleton

**Files:**
- Create: `repository.yaml`
- Create: `ha_claude_agent_addon/config.yaml`
- Create: `ha_claude_agent_addon/translations/en.yaml`

- [ ] **Step 1: Create `repository.yaml`**

```yaml
name: HA Claude Agent Add-on
url: https://github.com/Marsunpaisti/HAClaudeAgent
maintainer: Paisti
```

- [ ] **Step 2: Create `ha_claude_agent_addon/config.yaml`**

```yaml
name: "HA Claude Agent"
version: "0.1.0"
slug: "ha_claude_agent"
description: "Claude Code CLI runner for the HA Claude Agent integration"
url: "https://github.com/Marsunpaisti/HAClaudeAgent"
arch:
  - aarch64
  - amd64
homeassistant_api: true
ports:
  8099/tcp: null
ports_description:
  8099/tcp: "Internal API (not exposed to host)"
options:
  auth_token: ""
schema:
  auth_token: password
startup: application
boot: auto
map:
  - addon_config:rw
```

- [ ] **Step 3: Create `ha_claude_agent_addon/translations/en.yaml`**

```yaml
configuration:
  auth_token:
    name: Claude auth token
    description: >-
      Your Claude Code OAuth token (from 'claude setup-token') or Anthropic API key
      (from console.anthropic.com). The OAuth token works with Claude Pro/Max subscriptions.
```

- [ ] **Step 4: Commit**

```bash
git add repository.yaml ha_claude_agent_addon/config.yaml ha_claude_agent_addon/translations/en.yaml
git commit -m "feat(addon): add repository skeleton and config"
```

---

### Task 2: Add-on Dockerfile and requirements

**Files:**
- Create: `ha_claude_agent_addon/Dockerfile`
- Create: `ha_claude_agent_addon/requirements.txt`

- [ ] **Step 1: Create `ha_claude_agent_addon/requirements.txt`**

```
claude-agent-sdk>=0.1.0
aiohttp>=3.9.0
```

- [ ] **Step 2: Create `ha_claude_agent_addon/Dockerfile`**

```dockerfile
ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.13-alpine3.23
FROM ${BUILD_FROM}

# Install Node.js (required by Claude Code CLI)
RUN apk add --no-cache nodejs npm

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Install Python dependencies
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Copy application source
COPY src/ /app/

# Copy rootfs overlay (s6 service definitions)
COPY rootfs /
```

- [ ] **Step 3: Commit**

```bash
git add ha_claude_agent_addon/Dockerfile ha_claude_agent_addon/requirements.txt
git commit -m "feat(addon): add Dockerfile and Python requirements"
```

---

### Task 3: Add-on HA REST API client

**Files:**
- Create: `ha_claude_agent_addon/src/ha_client.py`

- [ ] **Step 1: Create `ha_claude_agent_addon/src/ha_client.py`**

```python
"""Async client for the Home Assistant REST API via the Supervisor proxy."""

from __future__ import annotations

import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)


class HAClient:
    """Thin async wrapper around the HA REST API.

    Inside an HAOS add-on the Supervisor injects SUPERVISOR_TOKEN and
    proxies requests sent to http://supervisor/core/api/*.
    """

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self._headers)
        return self._session

    async def call_service(
        self, domain: str, service: str, data: dict
    ) -> list[dict]:
        """POST /api/services/{domain}/{service}."""
        session = await self._get_session()
        url = f"{self._base_url}/api/services/{domain}/{service}"
        _LOGGER.info("call_service: %s.%s -> %s", domain, service, url)
        async with session.post(url, json=data) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_state(self, entity_id: str) -> dict | None:
        """GET /api/states/{entity_id}.  Returns None on 404."""
        session = await self._get_session()
        url = f"{self._base_url}/api/states/{entity_id}"
        async with session.get(url) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            return await resp.json()

    async def get_states(self) -> list[dict]:
        """GET /api/states — all entity states."""
        session = await self._get_session()
        url = f"{self._base_url}/api/states"
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
```

- [ ] **Step 2: Commit**

```bash
git add ha_claude_agent_addon/src/ha_client.py
git commit -m "feat(addon): add HA REST API client"
```

---

### Task 4: Add-on MCP tools

**Files:**
- Create: `ha_claude_agent_addon/src/tools.py`

These are the same three tools that currently live in `custom_components/ha_claude_agent/tools.py`, but they call the HA REST API via `HAClient` instead of accessing `hass` directly.

- [ ] **Step 1: Create `ha_claude_agent_addon/src/tools.py`**

```python
"""MCP tools for controlling Home Assistant via its REST API.

These tools are registered with the Claude Agent SDK's MCP server
and proxy all operations through the HA REST API using SUPERVISOR_TOKEN.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from claude_agent_sdk import tool

from ha_client import HAClient

_LOGGER = logging.getLogger(__name__)


def create_ha_tools(
    ha_client: HAClient,
    exposed_entities: list[str],
) -> list:
    """Create MCP tool instances that proxy to HA via ha_client.

    Parameters
    ----------
    ha_client:
        Initialised HAClient pointing at the Supervisor HA proxy.
    exposed_entities:
        Entity IDs the conversation agent is allowed to control.
        Passed per-request by the integration.
    """

    exposed_set = set(exposed_entities)

    @tool(
        "call_service",
        "Call a Home Assistant service to control a device. "
        "Examples: domain='light', service='turn_on', "
        "entity_id='light.living_room'. "
        "service_data is a JSON object string for additional "
        "parameters; pass '{}' if none needed.",
        {
            "domain": str,
            "service": str,
            "entity_id": str,
            "service_data": str,
        },
    )
    async def call_service(args: dict[str, Any]) -> dict[str, Any]:
        entity_id = args["entity_id"]
        domain = args["domain"]
        service = args["service"]

        _LOGGER.info("call_service: %s.%s on %s", domain, service, entity_id)

        # Security: only allow calls on exposed entities
        if entity_id not in exposed_set:
            _LOGGER.warning(
                "Blocked service call on unexposed entity: %s", entity_id
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Entity {entity_id} is not exposed "
                            "to conversation agents."
                        ),
                    }
                ],
                "is_error": True,
            }

        raw_data = args.get("service_data", "{}")
        try:
            extra_data = json.loads(raw_data) if raw_data else {}
        except json.JSONDecodeError:
            extra_data = {}

        service_data = {"entity_id": entity_id, **extra_data}

        try:
            await ha_client.call_service(domain, service, service_data)
            # Read back state after the service call
            state = await ha_client.get_state(entity_id)
            state_str = state["state"] if state else "unknown"
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Called {domain}.{service} on {entity_id}. "
                            f"Current state: {state_str}"
                        ),
                    }
                ]
            }
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Service call %s.%s failed for %s: %s",
                domain, service, entity_id, err,
            )
            return {
                "content": [
                    {"type": "text", "text": f"Error calling service: {err}"}
                ],
                "is_error": True,
            }

    @tool(
        "get_entity_state",
        "Get the current state and attributes of a "
        "Home Assistant entity.",
        {"entity_id": str},
    )
    async def get_entity_state(args: dict[str, Any]) -> dict[str, Any]:
        entity_id = args["entity_id"]
        _LOGGER.debug("get_entity_state: %s", entity_id)

        state = await ha_client.get_state(entity_id)
        if state is None:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Entity {entity_id} not found.",
                    }
                ],
                "is_error": True,
            }

        attrs = dict(state.get("attributes", {}))
        info = {
            "entity_id": entity_id,
            "state": state["state"],
            "friendly_name": attrs.pop("friendly_name", entity_id),
            "attributes": attrs,
        }
        return {
            "content": [
                {"type": "text", "text": json.dumps(info, default=str)}
            ]
        }

    @tool(
        "list_entities",
        "List Home Assistant entities filtered by domain "
        "(e.g., 'light', 'switch', 'sensor'). Pass empty string "
        "to list all. Returns entity IDs, names, and states.",
        {"domain": str},
    )
    async def list_entities(args: dict[str, Any]) -> dict[str, Any]:
        domain_filter = args.get("domain", "")
        _LOGGER.debug(
            "list_entities: domain_filter=%s", domain_filter or "(all)"
        )

        all_states = await ha_client.get_states()
        entities = []
        for state in all_states:
            eid = state["entity_id"]
            if domain_filter and not eid.startswith(f"{domain_filter}."):
                continue
            entities.append(
                {
                    "entity_id": eid,
                    "name": state.get("attributes", {}).get(
                        "friendly_name", eid
                    ),
                    "state": state["state"],
                }
            )

        return {
            "content": [
                {"type": "text", "text": json.dumps(entities, default=str)}
            ]
        }

    return [call_service, get_entity_state, list_entities]
```

- [ ] **Step 2: Commit**

```bash
git add ha_claude_agent_addon/src/tools.py
git commit -m "feat(addon): add MCP tools proxying to HA REST API"
```

---

### Task 5: Add-on HTTP server

**Files:**
- Create: `ha_claude_agent_addon/src/server.py`

This is the main entry point. It reads add-on options, creates the HA client and MCP server per request, runs the SDK `query()` loop, and returns a JSON result.

- [ ] **Step 1: Create `ha_claude_agent_addon/src/server.py`**

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


def _read_addon_options() -> dict:
    """Read add-on options from /data/options.json."""
    with open(ADDON_OPTIONS_PATH) as f:
        return json.load(f)


def _build_auth_env(auth_token: str) -> dict[str, str]:
    """Return the env dict for the SDK based on the token format."""
    if not auth_token:
        return {}
    # Anthropic API keys start with sk-ant-
    if auth_token.startswith("sk-ant-"):
        return {"ANTHROPIC_API_KEY": auth_token}
    return {"CLAUDE_CODE_OAUTH_TOKEN": auth_token}


# ── Assistant error mapping (mirrors AssistantMessage.error values) ──

_ASSISTANT_ERROR_CODES = {
    "authentication_failed",
    "billing_error",
    "rate_limit",
    "invalid_request",
    "server_error",
    "unknown",
}


async def handle_health(request: web.Request) -> web.Response:
    """GET /health — simple liveness check."""
    return web.json_response({"status": "ok"})


async def handle_query(request: web.Request) -> web.Response:
    """POST /query — run a Claude Agent SDK query and return the result."""
    data = await request.json()

    prompt: str = data["prompt"]
    model: str = data["model"]
    system_prompt: str = data["system_prompt"]
    max_turns: int = data.get("max_turns", 10)
    effort: str = data.get("effort", "medium")
    session_id: str | None = data.get("session_id")
    exposed_entities: list[str] = data.get("exposed_entities", [])

    _LOGGER.info(
        "Query: model=%s, effort=%s, max_turns=%d, resume=%s",
        model, effort, max_turns, session_id is not None,
    )

    # ── Auth ──
    addon_options = _read_addon_options()
    auth_token = addon_options.get("auth_token", "")
    env = _build_auth_env(auth_token)

    # ── HA client + MCP tools ──
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
    supervisor_url = "http://supervisor/core"
    ha_client = HAClient(base_url=supervisor_url, token=supervisor_token)

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
            env=env,
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

    finally:
        await ha_client.close()

    return web.json_response(
        {
            "result_text": result_text or None,
            "session_id": new_session_id,
            "cost_usd": cost_usd,
            "num_turns": num_turns,
            "error_code": error_code,
        }
    )


def create_app() -> web.Application:
    """Create and return the aiohttp application."""
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/query", handle_query)
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    _LOGGER.info("Starting HA Claude Agent add-on on port %d", port)
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=port)
```

- [ ] **Step 2: Commit**

```bash
git add ha_claude_agent_addon/src/server.py
git commit -m "feat(addon): add HTTP server with /query endpoint"
```

---

### Task 6: Add-on s6-overlay service scripts

**Files:**
- Create: `ha_claude_agent_addon/rootfs/etc/services.d/ha-claude-agent/run`
- Create: `ha_claude_agent_addon/rootfs/etc/services.d/ha-claude-agent/finish`

- [ ] **Step 1: Create the run script**

Create `ha_claude_agent_addon/rootfs/etc/services.d/ha-claude-agent/run`:

```bash
#!/usr/bin/with-contenv bashio

PORT=8099

# Publish discovery so the integration can auto-detect us
bashio::log.info "Publishing discovery for ha_claude_agent..."
curl -s -X POST \
  -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"service\": \"ha_claude_agent\", \"config\": {\"host\": \"$(hostname)\", \"port\": ${PORT}}}" \
  http://supervisor/discovery || bashio::log.warning "Discovery publish failed (non-fatal)"

bashio::log.info "Starting HA Claude Agent server on port ${PORT}..."
exec python3 /app/server.py
```

- [ ] **Step 2: Create the finish script**

Create `ha_claude_agent_addon/rootfs/etc/services.d/ha-claude-agent/finish`:

```bash
#!/usr/bin/with-contenv bashio

bashio::log.warning "HA Claude Agent server stopped. Restarting in 5s..."
sleep 5
```

- [ ] **Step 3: Set executable permissions**

```bash
git update-index --chmod=+x ha_claude_agent_addon/rootfs/etc/services.d/ha-claude-agent/run
git update-index --chmod=+x ha_claude_agent_addon/rootfs/etc/services.d/ha-claude-agent/finish
```

- [ ] **Step 4: Commit**

```bash
git add ha_claude_agent_addon/rootfs/
git commit -m "feat(addon): add s6-overlay service scripts"
```

---

### Task 7: Update integration constants

**Files:**
- Modify: `custom_components/ha_claude_agent/const.py`

- [ ] **Step 1: Replace `const.py` contents**

```python
"""Constants for the HA Claude Agent integration."""

DOMAIN = "ha_claude_agent"

CONF_ADDON_HOST = "addon_host"
CONF_ADDON_PORT = "addon_port"
CONF_CHAT_MODEL = "chat_model"
CONF_MAX_TOKENS = "max_tokens"
CONF_TEMPERATURE = "temperature"
CONF_PROMPT = "prompt"
CONF_THINKING_EFFORT = "thinking_effort"
CONF_MAX_TURNS = "max_turns"

DEFAULT_ADDON_HOST = "local-ha-claude-agent"
DEFAULT_ADDON_PORT = 8099
DEFAULT_CHAT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 1.0
DEFAULT_THINKING_EFFORT = "medium"

THINKING_EFFORT_OPTIONS = ["low", "medium", "high", "max"]

DEFAULT_CONVERSATION_NAME = "Claude Agent"

DEFAULT_PROMPT = """\
You are a voice assistant for Home Assistant.
Answer in plain, concise language.
When controlling devices, confirm what you did.
"""

DEFAULT_MAX_TURNS = 10

QUERY_TIMEOUT_SECONDS = 300
```

**Changes from current:**
- Removed: `CONF_API_KEY`, `CONF_CLI_PATH`, `MCP_SERVER_NAME`
- Added: `CONF_ADDON_HOST`, `CONF_ADDON_PORT`, `DEFAULT_ADDON_HOST`, `DEFAULT_ADDON_PORT`, `QUERY_TIMEOUT_SECONDS`

- [ ] **Step 2: Commit**

```bash
git add custom_components/ha_claude_agent/const.py
git commit -m "refactor: update constants for add-on architecture"
```

---

### Task 8: Update integration helpers

**Files:**
- Modify: `custom_components/ha_claude_agent/helpers.py`

The system prompt builder references `MCP_SERVER_NAME` which now lives in the add-on. Since the MCP server name is a fixed string (`homeassistant`), inline it here rather than importing from the add-on.

- [ ] **Step 1: Update `helpers.py`**

Replace the `MCP_SERVER_NAME` import and usage:

```python
"""Helper utilities for HA Claude Agent."""

from __future__ import annotations

from homeassistant.components.conversation import async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

# Tool prefix matches the MCP server name in the add-on
_TOOL_PREFIX = "mcp__homeassistant__"


def build_system_prompt(
    hass: HomeAssistant,
    user_prompt: str,
) -> str:
    """Build the full system prompt with HA context and exposed entities.

    Called on every turn so entity states are always current.
    """
    ha_name = hass.config.location_name or "Home"
    now = dt_util.now().strftime("%Y-%m-%d %H:%M:%S %Z")

    # Gather exposed entities
    entity_lines: list[str] = []
    for state in hass.states.async_all():
        if not async_should_expose(hass, "conversation", state.entity_id):
            continue
        name = state.attributes.get("friendly_name", state.entity_id)
        entity_lines.append(
            f"- {state.entity_id}: {name} (state: {state.state})"
        )

    entity_section = (
        "\n".join(entity_lines) if entity_lines else "(none exposed)"
    )

    return f"""\
{user_prompt}

## Home Assistant Context
- Home name: {ha_name}
- Current time: {now}

## Exposed Entities
These are the entities you can monitor and control:
{entity_section}

## Available Tools
Use `{_TOOL_PREFIX}call_service` to control devices (turn on/off, set values, etc.).
Use `{_TOOL_PREFIX}get_entity_state` to check a device's current state and attributes.
Use `{_TOOL_PREFIX}list_entities` to discover entities by domain.

Only control entities listed above. If a user asks about an entity not listed, tell them it's not exposed to you.
"""
```

- [ ] **Step 2: Commit**

```bash
git add custom_components/ha_claude_agent/helpers.py
git commit -m "refactor: inline tool prefix in helpers, remove MCP_SERVER_NAME import"
```

---

### Task 9: Update integration runtime data and setup

**Files:**
- Modify: `custom_components/ha_claude_agent/__init__.py`

- [ ] **Step 1: Replace `__init__.py` contents**

```python
"""The HA Claude Agent integration."""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ADDON_HOST,
    CONF_ADDON_PORT,
    DEFAULT_ADDON_HOST,
    DEFAULT_ADDON_PORT,
    DOMAIN,
)

PLATFORMS = [Platform.CONVERSATION]

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

MAX_SESSIONS = 50


class BoundedSessionMap(OrderedDict):
    """OrderedDict that evicts oldest entries when max size is exceeded."""

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > MAX_SESSIONS:
            self.popitem(last=False)


@dataclass
class HAClaudeAgentRuntimeData:
    """Runtime data for the HA Claude Agent integration."""

    addon_url: str
    sessions: BoundedSessionMap = field(default_factory=BoundedSessionMap)


type HAClaudeAgentConfigEntry = ConfigEntry[HAClaudeAgentRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: HAClaudeAgentConfigEntry
) -> bool:
    """Set up HA Claude Agent from a config entry."""
    _LOGGER.debug("Setting up HA Claude Agent entry %s", entry.entry_id)

    host = entry.data.get(CONF_ADDON_HOST, DEFAULT_ADDON_HOST)
    port = entry.data.get(CONF_ADDON_PORT, DEFAULT_ADDON_PORT)
    addon_url = f"http://{host}:{port}"

    # Check add-on connectivity — raises ConfigEntryNotReady if unreachable.
    # HA retries with exponential backoff automatically.
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            f"{addon_url}/health",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                raise ConfigEntryNotReady(
                    f"Add-on returned status {resp.status}"
                )
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ConfigEntryNotReady(
            f"Cannot reach add-on at {addon_url}: {err}"
        ) from err

    entry.runtime_data = HAClaudeAgentRuntimeData(addon_url=addon_url)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.info(
        "HA Claude Agent set up with %d conversation subentries",
        sum(
            1
            for s in entry.subentries.values()
            if s.subentry_type == "conversation"
        ),
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HAClaudeAgentConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

**Changes from current:**
- Removed: `api_key`, `cli_path`, `mcp_server` from runtime data
- Removed: `create_ha_mcp_server` import and call
- Added: `addon_url` to runtime data (constructed from `CONF_ADDON_HOST` + `CONF_ADDON_PORT`)
- Added: `ConfigEntryNotReady` — checks add-on health on setup, HA retries with exponential backoff if unreachable

- [ ] **Step 2: Commit**

```bash
git add custom_components/ha_claude_agent/__init__.py
git commit -m "refactor: simplify runtime data for add-on architecture"
```

---

### Task 10: Rewrite integration conversation entity

**Files:**
- Modify: `custom_components/ha_claude_agent/conversation.py`

This is the largest change. The SDK `query()` loop is replaced by an HTTP POST to the add-on.

- [ ] **Step 1: Replace `conversation.py` contents**

```python
"""Conversation platform for HA Claude Agent."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.components import conversation
from homeassistant.components.conversation import (
    AssistantContent,
    ChatLog,
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
    ConversationResult,
    async_should_expose,
)
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, intent
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
        "Claude authentication failed. "
        "Check the auth token in the add-on settings."
    ),
    "billing_error": (
        "Billing issue — check your account at console.anthropic.com."
    ),
    "rate_limit": "Rate limited. Please wait a moment and try again.",
    "cli_not_found": (
        "Claude Code CLI not found in the add-on container. "
        "Try restarting the add-on."
    ),
    "process_error": (
        "Claude Code process crashed. Check the add-on logs."
    ),
    "parse_error": (
        "Received an invalid response from Claude. Try again."
    ),
    "internal_error": (
        "An unexpected error occurred in the add-on."
    ),
    "addon_unreachable": (
        "Cannot reach the HA Claude Agent add-on. "
        "Is the add-on installed and running?"
    ),
}


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
    _attr_supports_streaming = False
    _attr_supported_features = ConversationEntityFeature.CONTROL

    def __init__(
        self, config_entry: ConfigEntry, subentry: ConfigSubentry
    ) -> None:
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
        intent_response.async_set_error(
            intent.IntentResponseErrorCode.UNKNOWN, message
        )
        return ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
        )

    def _get_exposed_entity_ids(self) -> list[str]:
        """Return entity IDs exposed to the conversation agent."""
        return [
            state.entity_id
            for state in self.hass.states.async_all()
            if async_should_expose(
                self.hass, "conversation", state.entity_id
            )
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
        system_prompt = build_system_prompt(self.hass, user_prompt)

        session_id: str | None = None
        if user_input.conversation_id:
            session_id = runtime_data.sessions.get(
                user_input.conversation_id
            )

        effort = self.subentry.data.get(
            CONF_THINKING_EFFORT, DEFAULT_THINKING_EFFORT
        )
        max_turns = int(
            self.subentry.data.get(CONF_MAX_TURNS, DEFAULT_MAX_TURNS)
        )

        _LOGGER.info(
            "Handling message: model=%s, effort=%s, resume=%s",
            model, effort, session_id is not None,
        )

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
        return ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=False,
        )
```

- [ ] **Step 2: Commit**

```bash
git add custom_components/ha_claude_agent/conversation.py
git commit -m "refactor: delegate to add-on HTTP API instead of SDK"
```

---

### Task 11: Update integration config flow

**Files:**
- Modify: `custom_components/ha_claude_agent/config_flow.py`

Replace the API key + CLI path step with Supervisor Discovery (auto-detect) and a manual URL fallback.

- [ ] **Step 1: Replace `config_flow.py` contents**

```python
"""Config flow for HA Claude Agent integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TemplateSelectorConfig,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .const import (
    CONF_ADDON_HOST,
    CONF_ADDON_PORT,
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_MAX_TURNS,
    CONF_PROMPT,
    CONF_TEMPERATURE,
    CONF_THINKING_EFFORT,
    DEFAULT_ADDON_HOST,
    DEFAULT_ADDON_PORT,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MAX_TURNS,
    DEFAULT_PROMPT,
    DEFAULT_TEMPERATURE,
    DEFAULT_THINKING_EFFORT,
    DOMAIN,
    THINKING_EFFORT_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)

MODELS = [
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-opus-4-6",
]

DEFAULT_SUBENTRY_DATA: dict[str, Any] = {
    CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
    CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
    CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
    CONF_THINKING_EFFORT: DEFAULT_THINKING_EFFORT,
    CONF_MAX_TURNS: DEFAULT_MAX_TURNS,
    CONF_PROMPT: DEFAULT_PROMPT,
}


class HAClaudeAgentConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA Claude Agent."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_host: str | None = None
        self._discovered_port: int | None = None

    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> ConfigFlowResult:
        """Handle Supervisor add-on discovery (auto-detect).

        The add-on publishes its host/port to the Supervisor discovery
        service on startup. This step receives that info automatically.
        """
        self._discovered_host = discovery_info.config.get("host")
        self._discovered_port = discovery_info.config.get("port", DEFAULT_ADDON_PORT)

        _LOGGER.info(
            "Discovered add-on at %s:%s",
            self._discovered_host,
            self._discovered_port,
        )

        # Check we can actually reach it
        addon_url = f"http://{self._discovered_host}:{self._discovered_port}"
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                f"{addon_url}/health",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return self.async_abort(reason="cannot_connect")
        except (aiohttp.ClientError, TimeoutError):
            return self.async_abort(reason="cannot_connect")

        # Show confirmation step
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovered add-on setup."""
        if user_input is not None:
            return self.async_create_entry(
                title="HA Claude Agent",
                data={
                    CONF_ADDON_HOST: self._discovered_host,
                    CONF_ADDON_PORT: self._discovered_port,
                },
                subentries=[
                    {
                        "subentry_type": "conversation",
                        "data": dict(DEFAULT_SUBENTRY_DATA),
                        "title": DEFAULT_CONVERSATION_NAME,
                    }
                ],
            )

        return self.async_show_form(
            step_id="hassio_confirm",
            description_placeholders={
                "addon_url": (
                    f"http://{self._discovered_host}:{self._discovered_port}"
                ),
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup — fallback when discovery is not available."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_ADDON_HOST]
            port = int(user_input[CONF_ADDON_PORT])

            # Validate connectivity
            addon_url = f"http://{host}:{port}"
            session = async_get_clientsession(self.hass)
            try:
                async with session.get(
                    f"{addon_url}/health",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        errors["base"] = "cannot_connect"
            except (aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(
                    title="HA Claude Agent",
                    data={
                        CONF_ADDON_HOST: host,
                        CONF_ADDON_PORT: port,
                    },
                    subentries=[
                        {
                            "subentry_type": "conversation",
                            "data": dict(DEFAULT_SUBENTRY_DATA),
                            "title": DEFAULT_CONVERSATION_NAME,
                        }
                    ],
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ADDON_HOST, default=DEFAULT_ADDON_HOST
                    ): TextSelector(TextSelectorConfig()),
                    vol.Required(
                        CONF_ADDON_PORT, default=DEFAULT_ADDON_PORT
                    ): int,
                }
            ),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentry types supported by this integration."""
        return {"conversation": ConversationSubentryFlowHandler}


class ConversationSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for conversation agents."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle adding a new conversation agent."""
        if user_input is not None:
            return self.async_create_entry(
                title=user_input.pop("name", DEFAULT_CONVERSATION_NAME),
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=self._build_schema(DEFAULT_SUBENTRY_DATA),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle reconfiguring an existing conversation agent."""
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=user_input.pop("name", subentry.title),
                data=user_input,
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._build_schema(
                subentry.data, subentry.title
            ),
        )

    @staticmethod
    def _build_schema(
        defaults: dict[str, Any],
        default_name: str = DEFAULT_CONVERSATION_NAME,
    ) -> vol.Schema:
        """Build the subentry form schema with given defaults."""
        return vol.Schema(
            {
                vol.Required("name", default=default_name): str,
                vol.Optional(
                    CONF_CHAT_MODEL,
                    default=defaults.get(
                        CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=MODELS,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_MAX_TOKENS,
                    default=defaults.get(
                        CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=16384,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_TEMPERATURE,
                    default=defaults.get(
                        CONF_TEMPERATURE, DEFAULT_TEMPERATURE
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0,
                        max=2.0,
                        step=0.05,
                        mode=NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Optional(
                    CONF_THINKING_EFFORT,
                    default=defaults.get(
                        CONF_THINKING_EFFORT, DEFAULT_THINKING_EFFORT
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=THINKING_EFFORT_OPTIONS,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_MAX_TURNS,
                    default=defaults.get(
                        CONF_MAX_TURNS, DEFAULT_MAX_TURNS
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=50,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_PROMPT,
                    default=defaults.get(CONF_PROMPT, DEFAULT_PROMPT),
                ): TemplateSelector(TemplateSelectorConfig()),
            }
        )
```

**Changes from current:**
- `VERSION` bumped from 1 to 2 (schema changed)
- Added `async_step_hassio` — receives Supervisor Discovery from add-on, auto-creates entry
- Added `async_step_hassio_confirm` — confirmation step for discovered add-on
- `async_step_user` is now the manual fallback — collects `CONF_ADDON_HOST` + `CONF_ADDON_PORT`
- Both paths validate connectivity via `GET /health`
- Removed `CONF_API_KEY`, `CONF_CLI_PATH`, `TextSelectorType.PASSWORD`
- `ConversationSubentryFlowHandler` is unchanged

- [ ] **Step 2: Commit**

```bash
git add custom_components/ha_claude_agent/config_flow.py
git commit -m "refactor: config flow collects add-on URL instead of API key"
```

---

### Task 12: Update manifest, strings, translations, and delete old tools

**Files:**
- Modify: `custom_components/ha_claude_agent/manifest.json`
- Modify: `custom_components/ha_claude_agent/strings.json`
- Modify: `custom_components/ha_claude_agent/translations/en.json`
- Delete: `custom_components/ha_claude_agent/tools.py`

- [ ] **Step 1: Update `manifest.json`**

```json
{
  "domain": "ha_claude_agent",
  "name": "HA Claude Agent",
  "codeowners": ["@Marsunpaisti"],
  "config_flow": true,
  "dependencies": ["conversation"],
  "documentation": "https://github.com/Marsunpaisti/HAClaudeAgent",
  "integration_type": "service",
  "iot_class": "cloud_polling",
  "requirements": [],
  "version": "0.4.0"
}
```

**Changes:** Removed `claude-agent-sdk` from requirements (it's now in the add-on). Bumped version to `0.4.0`.

- [ ] **Step 2: Update `strings.json`**

```json
{
  "config": {
    "step": {
      "hassio_confirm": {
        "title": "Discovered HA Claude Agent Add-on",
        "description": "The HA Claude Agent add-on was found at {addon_url}. Click submit to set up the integration."
      },
      "user": {
        "title": "Set up HA Claude Agent",
        "description": "Enter the add-on connection details. The defaults work for HAOS installations.",
        "data": {
          "addon_host": "Add-on host",
          "addon_port": "Add-on port"
        },
        "data_description": {
          "addon_host": "Hostname or IP of the HA Claude Agent add-on container.",
          "addon_port": "Port the add-on HTTP server listens on."
        }
      }
    },
    "abort": {
      "cannot_connect": "Cannot reach the add-on. Is it installed and running?"
    },
    "error": {
      "cannot_connect": "Cannot reach the add-on at this address. Is it installed and running?",
      "unknown": "An unexpected error occurred."
    }
  },
  "config_subentries": {
    "conversation": {
      "initiate_flow": {
        "user": "Add conversation agent"
      },
      "step": {
        "user": {
          "title": "Add conversation agent",
          "data": {
            "name": "Name",
            "chat_model": "Model",
            "max_tokens": "Max output tokens",
            "temperature": "Temperature",
            "thinking_effort": "Thinking effort",
            "max_turns": "Max tool turns",
            "prompt": "System prompt"
          },
          "data_description": {
            "name": "Name of this conversation agent.",
            "chat_model": "Claude model to use for responses.",
            "max_tokens": "Maximum number of tokens in the response.",
            "temperature": "Controls randomness. Lower is more focused, higher is more creative.",
            "thinking_effort": "How much reasoning effort Claude uses. Higher means slower but more thorough.",
            "max_turns": "Maximum number of tool-use round trips per conversation turn.",
            "prompt": "Custom instructions for the AI agent. Appended to the HA context."
          }
        },
        "reconfigure": {
          "title": "Reconfigure conversation agent",
          "data": {
            "name": "Name",
            "chat_model": "Model",
            "max_tokens": "Max output tokens",
            "temperature": "Temperature",
            "thinking_effort": "Thinking effort",
            "max_turns": "Max tool turns",
            "prompt": "System prompt"
          },
          "data_description": {
            "name": "Name of this conversation agent.",
            "chat_model": "Claude model to use for responses.",
            "max_tokens": "Maximum number of tokens in the response.",
            "temperature": "Controls randomness. Lower is more focused, higher is more creative.",
            "thinking_effort": "How much reasoning effort Claude uses. Higher means slower but more thorough.",
            "max_turns": "Maximum number of tool-use round trips per conversation turn.",
            "prompt": "Custom instructions for the AI agent. Appended to the HA context."
          }
        }
      }
    }
  }
}
```

- [ ] **Step 3: Update `translations/en.json`**

Same content as `strings.json` above — copy the file.

- [ ] **Step 4: Delete `tools.py`**

```bash
git rm custom_components/ha_claude_agent/tools.py
```

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_claude_agent/manifest.json \
        custom_components/ha_claude_agent/strings.json \
        custom_components/ha_claude_agent/translations/en.json
git commit -m "refactor: remove SDK dependency, update strings for add-on URL, delete old tools"
```

---

### Task 13: Update project documentation

**Files:**
- Modify: `.claude/CLAUDE.md`

- [ ] **Step 1: Update `CLAUDE.md` architecture section**

Replace the Architecture and File Layout sections to reflect the new two-component structure:

```markdown
# HA Claude Agent

Home Assistant custom component that adds a conversation agent backed by Claude, with a companion add-on that runs the Claude Code CLI.

## Architecture

Two components work together:

### Integration (HACS custom component)
- Registers as a HA conversation agent via `ConversationEntity`
- Builds system prompts with exposed entities and HA context
- Delegates query execution to the add-on via HTTP POST
- Manages session mapping (conversation_id → session_id) in a bounded LRU cache

### Add-on (Docker container)
- Runs Node.js + Claude Code CLI + Python claude-agent-sdk
- Exposes HTTP API (`POST /query`, `GET /health`)
- MCP tools proxy HA service calls via the REST API using `SUPERVISOR_TOKEN`
- Handles Claude auth (OAuth token or API key) from add-on configuration

## File Layout

` ` `
# Integration (HACS)
custom_components/ha_claude_agent/
  __init__.py          — integration setup, runtime data
  conversation.py      — ConversationEntity: builds prompt, calls add-on
  config_flow.py       — ConfigFlow (add-on URL) + ConfigSubentryFlow (agent settings)
  const.py             — all constants and defaults
  helpers.py           — system prompt builder with exposed entities
  manifest.json        — integration manifest (no external requirements)
  strings.json         — UI string keys
  translations/en.json — English translations

# Add-on (Docker)
ha_claude_agent_addon/
  config.yaml          — add-on metadata and options schema
  Dockerfile           — Python + Node.js + Claude CLI container
  requirements.txt     — Python dependencies (claude-agent-sdk, aiohttp)
  src/
    server.py          — HTTP API server with /query endpoint
    ha_client.py       — HA REST API client using SUPERVISOR_TOKEN
    tools.py           — MCP tools proxying to HA REST API
  rootfs/              — s6-overlay service scripts
  translations/en.yaml — add-on UI strings
` ` `

## Key Patterns

- `conversation.async_set_agent()` / `async_unset_agent()` in entity lifecycle — required for agent to appear in HA's conversation agent picker
- Exposed entity list computed per-turn by integration, sent to add-on in request payload — add-on enforces the security boundary in its `call_service` tool
- `SUPERVISOR_TOKEN` (auto-injected by Supervisor) authenticates add-on → HA REST API calls
- `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` in add-on options authenticates SDK → Anthropic API calls
- `BoundedSessionMap` (LRU, max 50) in the integration prevents unbounded memory growth from session IDs

## Development

` ` `bash
pip install -r requirements_dev.txt
` ` `

Requires:
- Python 3.12+
- Home Assistant 2025.4+
- The HA Claude Agent add-on running (for end-to-end testing)
```

- [ ] **Step 2: Commit**

```bash
git add .claude/CLAUDE.md
git commit -m "docs: update CLAUDE.md for add-on architecture"
```

---

## Open Questions & Future Work

1. **Add-on hostname discovery**: Supervisor Discovery handles this automatically for HAOS/Supervised installs — the add-on publishes its hostname on startup and the integration's `async_step_hassio` receives it. Manual fallback (`async_step_user`) uses a default hostname of `local-ha-claude-agent` which may need adjustment depending on how the Supervisor names containers from external repositories. Test during initial deployment.

2. **Long-running queries**: The current design uses a simple HTTP POST with a 5-minute timeout. If queries routinely exceed this, switch to SSE streaming (aiohttp supports it natively on both sides). The add-on's `query()` loop already iterates messages — streaming would mean writing each message as an SSE event instead of collecting them.

3. **`Read` tool**: Currently excluded from `allowed_tools` since the container filesystem has nothing useful to read. If a use case emerges (e.g., reading config files mounted via `map`), add it back.

4. **Config migration**: Version bump from 1 → 2 means existing users must reconfigure. For a 0.x project this is acceptable. If needed later, add a `async_migrate_entry` to convert old `api_key` entries.

5. **Non-HAOS users**: Users on HA Container or HA Core (no Supervisor) cannot install add-ons. They could run the add-on container manually via `docker run`, but `SUPERVISOR_TOKEN` won't be available. They'd need to configure a long-lived access token instead. Consider adding a `ha_token` field to the add-on options as a fallback.

6. **Add-on auto-start on HA boot**: `boot: auto` in `config.yaml` ensures the add-on starts automatically. If the add-on starts before HA Core is ready, the first few tool calls may fail. The `HAClient` should tolerate transient connectivity errors.
