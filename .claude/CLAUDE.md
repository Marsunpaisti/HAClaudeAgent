# HA Claude Agent

Home Assistant custom component that adds a conversation agent backed by Claude, with a companion add-on that runs the Claude Code CLI.

## Architecture

Two components work together:

### Integration (HACS custom component)
- Registers as a HA conversation agent via `ConversationEntity`
- Builds system prompts with exposed entities and HA context
- Delegates query execution to the add-on via HTTP POST
- Manages session mapping (conversation_id -> session_id) in a bounded LRU cache

### Add-on (Docker container)
- Runs Node.js + Claude Code CLI + Python claude-agent-sdk
- Exposes HTTP API (`POST /query`, `GET /health`)
- MCP tools proxy HA service calls via the REST API using `SUPERVISOR_TOKEN`
- Handles Claude auth (OAuth token or API key) from add-on configuration

## File Layout

```
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
```

## Key Patterns

- `conversation.async_set_agent()` / `async_unset_agent()` in entity lifecycle — required for agent to appear in HA's conversation agent picker
- Exposed entity list computed per-turn by integration, sent to add-on in request payload — add-on enforces the security boundary in its `call_service` tool
- `SUPERVISOR_TOKEN` (auto-injected by Supervisor) authenticates add-on -> HA REST API calls
- `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` in add-on options authenticates SDK -> Anthropic API calls
- `BoundedSessionMap` (LRU, max 50) in the integration prevents unbounded memory growth from session IDs
- `ConfigEntryNotReady` raised if add-on is unreachable — HA retries with exponential backoff

## Development

```bash
pip install -r requirements_dev.txt
```

Requires:
- Python 3.12+
- Home Assistant 2025.4+
- The HA Claude Agent add-on running (for end-to-end testing)
