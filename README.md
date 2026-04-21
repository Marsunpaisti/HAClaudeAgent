# HA Claude Agent

A Home Assistant custom component that adds an AI conversation agent, with a companion add-on that runs either the Claude Code CLI or any OpenAI-compatible endpoint (OpenAI, Google AI Studio, OpenRouter, LiteLLM, Ollama, etc.).

## Features

- Registers as a native HA conversation agent (appears in the voice assistant picker)
- Controls exposed devices via tools (`call_service`, `get_entity_state`, `list_entities`)
- Multi-turn conversation with session persistence
- Dual backend: Claude Code CLI (`claude-agent-sdk`) or OpenAI-compatible endpoints (`openai-agents`)
- Configurable model, system prompt, thinking effort, temperature, and max tokens
- Multiple agents per integration via ConfigSubentry
- Security: only entities explicitly exposed to conversation agents can be controlled

## Prerequisites

- Home Assistant OS (HAOS) or Supervised installation
- One of:
  - An Anthropic API key or Claude Code OAuth token (for the Claude backend)
  - An API key for any OpenAI-compatible provider (for the OpenAI backend)

## Installation

Two components need to be installed: the **integration** (HACS) and the **add-on**.

### 1. Add-on

1. Go to **Settings > Add-ons > Add-on Store**
2. Click **⋮ > Repositories**
3. Paste `https://github.com/Marsunpaisti/HAClaudeAgent` and click **Add**
4. Find "HA Claude Agent" in the store and click **Install**
5. In the add-on **Configuration** tab:
   - Pick `backend`: `claude` or `openai`
   - Fill in the corresponding credentials (`claude_auth_token` for Claude, or `openai_api_key` + `openai_base_url` for OpenAI-compatible endpoints)
6. Start the add-on

See the [Choosing a backend](#choosing-a-backend) section for full details and examples.

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

## Choosing a backend

The add-on supports two backends, selected via the `backend` option in the add-on **Configuration** tab.

- `claude` — uses the Claude Code CLI via `claude-agent-sdk`. Requires `claude_auth_token`. Built-in web search and web fetch are available.
- `openai` — uses `openai-agents` against any OpenAI-compatible endpoint. Requires **both** `openai_api_key` and `openai_base_url`. No built-in web search yet.

### Example: Google AI Studio (Gemini)

1. Get an API key from <https://aistudio.google.com/apikey>.
2. Add-on options:
   - `backend: openai`
   - `openai_api_key: <your key>`
   - `openai_base_url: https://generativelanguage.googleapis.com/v1beta/openai/`
3. In the HA conversation agent settings, select "Custom..." in the model dropdown and type `gemini-2.0-flash` (or another Gemini model).

### Example: OpenAI

- `openai_base_url: https://api.openai.com/v1`
- Model: any OpenAI model, e.g. `gpt-4.1`, `gpt-5`, typed via Custom...

### Example: OpenRouter

- `openai_base_url: https://openrouter.ai/api/v1`
- Model: `openai/gpt-4.1` or any other OpenRouter model string.

### Conversation history

The OpenAI backend stores conversation history in a SQLite database at `/data/sessions.db` inside the add-on. Wiping the add-on's `/data` volume resets all conversations. The Claude backend uses Anthropic's server-side session resume (no local storage required).

### Migration from <=0.6.x

The single `auth_token` option has been split into four fields. On upgrade, set `backend: claude` and paste your existing token into `claude_auth_token`. The legacy `auth_token` field is accepted as a fallback for one minor version (warning logged) but should be migrated.

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
