# HA Claude Agent

Home Assistant custom component that adds a conversation agent backed by the Claude Agent SDK.

## Architecture

- **Backend:** `claude-agent-sdk` Python package (spawns Claude Code CLI as subprocess)
- **Tools:** Custom MCP server (`tools.py`) exposes HA services to Claude via `@tool` decorators
- **Config:** `ConfigEntry` holds API key + CLI path; `ConfigSubentry` per conversation agent (model, prompt, thinking effort, etc.)
- **Session management:** SDK session IDs mapped to HA conversation IDs in a bounded LRU cache (`__init__.py`)
- **System prompt:** Built per-turn from user config + exposed entities + HA context (`helpers.py`)

## File Layout

```
custom_components/ha_claude_agent/
  __init__.py          — integration setup, runtime data, MCP server creation
  conversation.py      — ConversationEntity: agent registration, SDK query loop
  config_flow.py       — ConfigFlow (API key) + ConfigSubentryFlow (agent settings)
  const.py             — all constants and defaults
  helpers.py           — system prompt builder with exposed entities
  tools.py             — MCP server: call_service, get_entity_state, list_entities
  manifest.json        — integration manifest (claude-agent-sdk dependency)
  strings.json         — UI string keys
  translations/en.json — English translations
```

## Key Patterns

- `conversation.async_set_agent()` / `async_unset_agent()` in entity lifecycle — required for agent to appear in HA's conversation agent picker
- `async_should_expose()` enforced in `call_service` tool — security boundary preventing LLM from controlling unexposed entities
- `ClaudeAgentOptions.permission_mode = "bypassPermissions"` with explicit `allowed_tools` — only our MCP tools are accessible
- `BoundedSessionMap` (LRU, max 500) prevents unbounded memory growth from session IDs

## Development

```bash
pip install -r requirements_dev.txt
```

Requires:
- Python 3.12+
- Home Assistant 2025.4+
- Node.js + `@anthropic-ai/claude-code` CLI installed (the SDK spawns it)
