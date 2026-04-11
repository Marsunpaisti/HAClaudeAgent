# HA Claude Agent

A Home Assistant custom component that adds a conversation agent powered by Claude, with a companion add-on that runs the Claude Code CLI.

## Features

- Registers as a native HA conversation agent (appears in the voice assistant picker)
- Controls exposed devices via MCP tools (`call_service`, `get_entity_state`, `list_entities`)
- Multi-turn conversation with session persistence
- Configurable model, system prompt, thinking effort, temperature, and max tokens
- Multiple agents per integration via ConfigSubentry
- Security: only entities explicitly exposed to conversation agents can be controlled

## Prerequisites

- Home Assistant OS (HAOS) or Supervised installation
- An Anthropic API key or Claude Code OAuth token

## Installation

Two components need to be installed: the **integration** (HACS) and the **add-on**.

### 1. Add-on

1. Go to **Settings > Add-ons > Add-on Store**
2. Click **⋮ > Repositories**
3. Paste `https://github.com/Marsunpaisti/HAClaudeAgent` and click **Add**
4. Find "HA Claude Agent" in the store and click **Install**
5. In the add-on **Configuration** tab, enter your `auth_token` (Anthropic API key or OAuth token)
6. Start the add-on

### 2. Integration (HACS)

1. Add this repository as a custom repository in HACS
2. Install "HA Claude Agent"
3. Restart Home Assistant
4. The integration should auto-discover the add-on. If not, go to **Settings > Devices & Services > Add Integration** and search for **HA Claude Agent**

## Configuration

Each conversation agent (subentry) has its own settings:

| Option                | Default                     | Description                                                                                       |
| --------------------- | --------------------------- | ------------------------------------------------------------------------------------------------- |
| **Model**             | `claude-haiku-4-5-20251001` | Claude model to use. Options: `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`, `claude-opus-4-6` |
| **Max output tokens** | `2048`                      | Maximum tokens in the response                                                                    |
| **Temperature**       | `1.0`                       | Controls randomness (0.0 = focused, 2.0 = creative)                                               |
| **Thinking effort**   | `medium`                    | Reasoning depth: `low`, `medium`, `high`, `max`                                                   |
| **Max tool turns**    | `10`                        | Maximum tool-use round trips per conversation turn                                                |
| **System prompt**     | _(default)_                 | Custom instructions appended to the HA context                                                    |

To add another agent: go to the integration page and click "Add conversation agent". Each agent can have different models and settings.

## Exposing Entities

The agent can only see and control entities you explicitly expose:

1. Go to **Settings > Voice Assistants**
2. Click **Expose** tab
3. Toggle on the entities you want the agent to access

## Usage

1. Go to **Settings > Voice Assistants**
2. Create or edit an assistant and select **Claude Agent** as the conversation agent
3. Use the conversation panel (chat bubble) to talk to it

Example commands:

- "Turn on the living room lights"
- "What's the temperature in the bedroom?"
- "Set the thermostat to 72 degrees"
- "What devices are on right now?"

## Architecture

Two components that work together. The add-on exists because claude-agent-sdk can't be installed on the HAOS host.

### Integration (HACS custom component)

- Registers as a HA conversation agent via `ConversationEntity`
- Builds system prompts with exposed entities and HA context
- Manages session mapping (conversation_id -> session_id) in a bounded LRU cache
- Delegates query execution to the add-on

### Add-on (Docker container)

- The add-ons role is to simply wrap claude-agent-sdk in a HTTP api.
- MCP tools proxy HA service calls via the REST API using `SUPERVISOR_TOKEN`

## Development

Uses `uv` for dependency management. Two lockfiles:

- `uv.lock` (repo root) — dev environment (HA, linters, type checker)
- `ha_claude_agent_addon/uv.lock` — add-on runtime (SDK, FastAPI, etc.)

```bash
uv sync          # install dev dependencies
```

### Verification

```bash
uv run ruff check custom_components/ ha_claude_agent_addon/src/ tests/
uv run ruff format --check custom_components/ ha_claude_agent_addon/src/ tests/
uv run mypy custom_components/ha_claude_agent/ ha_claude_agent_addon/src/
uv run pytest tests/ -v
```

Auto-fix lint and formatting:

```bash
uv run ruff check --fix custom_components/ ha_claude_agent_addon/src/ tests/
uv run ruff format custom_components/ ha_claude_agent_addon/src/ tests/
```

Requires [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync
```

Optionally, install the pre-commit hooks so ruff and basic hygiene checks run on every commit:

```bash
uv run pre-commit install
```

### Verification

```bash
uv run ruff check custom_components/ ha_claude_agent_addon/src/ tests/
uv run ruff format --check custom_components/ ha_claude_agent_addon/src/ tests/
uv run mypy custom_components/ha_claude_agent/ ha_claude_agent_addon/src/
uv run pytest tests/ -v
```

These checks run automatically in CI on every push and PR.

## License

MIT
