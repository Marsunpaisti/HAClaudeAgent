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

## How It Works

The system has two parts:

- **Integration** (HACS custom component) — registers as a HA conversation agent, builds system prompts with exposed entities, and delegates queries to the add-on via HTTP
- **Add-on** (Docker container) — runs the Claude Code CLI + Claude Agent SDK, exposes an HTTP API, and proxies HA service calls via MCP tools

The system prompt is rebuilt on every turn with current entity states, so Claude always has up-to-date information. Session IDs from the SDK are mapped to HA conversation IDs for multi-turn context.

## Development

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
