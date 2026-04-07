# HA Claude Agent

A Home Assistant custom component that adds a conversation agent powered by the [Claude Agent SDK](https://docs.anthropic.com/en/docs/agents-and-tools/claude-agent-sdk). Talk to Claude from your HA dashboard and let it control your smart home devices.

## Features

- Registers as a native HA conversation agent (appears in the voice assistant picker)
- Controls exposed devices via MCP tools (`call_service`, `get_entity_state`, `list_entities`)
- Multi-turn conversation with session persistence
- Configurable model, system prompt, thinking effort, temperature, and max tokens
- Multiple agents per API key via ConfigSubentry
- Security: only entities explicitly exposed to conversation agents can be controlled

## Prerequisites

- Home Assistant 2025.4+
- An Anthropic API key ([console.anthropic.com](https://console.anthropic.com))
- Node.js installed in the HA environment
- Claude Code CLI: `npm install -g @anthropic-ai/claude-code`

## Installation

### HACS (recommended)

1. Add this repository as a custom repository in HACS
2. Install "HA Claude Agent"
3. Restart Home Assistant

### Manual

1. Copy `custom_components/ha_claude_agent` to your HA `config/custom_components/` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for **HA Claude Agent**
3. Enter your Anthropic API key
4. Optionally set the path to the `claude` CLI binary (leave empty for default)
5. A default "Claude Agent" is created automatically

## Configuration

Each conversation agent (subentry) has its own settings:

| Option | Default | Description |
|--------|---------|-------------|
| **Model** | `claude-sonnet-4-6` | Claude model to use. Options: `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`, `claude-opus-4-6` |
| **Max output tokens** | `1024` | Maximum tokens in the response |
| **Temperature** | `1.0` | Controls randomness (0.0 = focused, 2.0 = creative) |
| **Thinking effort** | `low` | Reasoning depth: `low`, `medium`, `high`, `max` |
| **System prompt** | *(default)* | Custom instructions appended to the HA context |

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

The integration uses the Claude Agent SDK to run Claude as an agent with custom MCP tools:

- **`call_service`** — calls any HA service on an exposed entity
- **`get_entity_state`** — reads the current state and attributes of an entity
- **`list_entities`** — lists available entities, optionally filtered by domain

The system prompt is rebuilt on every turn with current entity states, so Claude always has up-to-date information. Session IDs from the SDK are mapped to HA conversation IDs for multi-turn context.

## License

MIT
