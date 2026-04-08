# Repository Structure: Add-on + Custom Component in One Repo

HACS and the HA add-on store look for different things and don't conflict, so both can live in the same repository.

- **HACS** looks for `custom_components/<domain>/`
- **Add-on store** looks for directories containing `config.yaml` (or `config.json`)

## Directory Layout

```
HAClaudeAgent/
├── custom_components/
│   └── ha_claude_agent/          # ← HACS installs this
│       ├── __init__.py
│       ├── conversation.py
│       ├── config_flow.py
│       ├── ...
│       └── manifest.json
├── claude_agent_addon/           # ← Add-on store installs this
│   ├── config.yaml               #    (this is what makes it an add-on)
│   ├── Dockerfile
│   ├── run.sh                    #    (or a Python entrypoint)
│   └── requirements.txt
├── repository.yaml               # Add-on repo metadata
├── hacs.json                     # HACS metadata
└── README.md
```

## How Users Install

Users add the **same repo URL** twice:

1. In **HACS** → Custom repositories → category "Integration" → installs the custom component
2. In **Settings → Add-ons → Add-on Store → Repositories** → installs the add-on

No submodules needed. Same repo, same branch, same commits.

## Add-on Repo Metadata

The optional `repository.yaml` at the root provides metadata for the add-on store:

```yaml
name: HA Claude Agent
url: https://github.com/Marsunpaisti/HAClaudeAgent
maintainer: Paisti
```

## Architecture between the integration and Add-on

The add-on runs the Claude Agent SDK (which needs Node.js + Claude CLI). The custom component handles all HA integration and communicates with the add-on over HTTP.

```
Integration (HACS)                       Add-on (Docker)
+-------------------------+              +--------------------------+
| ConversationEntity      |    HTTP      | Node.js + Claude CLI     |
| builds system prompt    |---prompt---->| Python + agent SDK       |
| with exposed entities   |   + config  | MCP tools call HA        |
| returns response        |<--result----| REST API directly        |
+-------------------------+              +--------------------------+
```

- **Integration**: builds system prompt (exposed entities, context), sends prompt + model + effort + session_id + HA token to add-on
- **Add-on**: runs `query()`, MCP tools use `POST /api/services/...` with the token
- Clean separation — integration evolves with HA APIs, add-on rarely changes

## Changes Needed to Existing Files

### README.md

The prerequisites and installation sections need to reflect the two-part install. Replace those sections with something like:

```markdown
## Prerequisites

- Home Assistant OS 2025.4+ (or any HA install with add-on support)
- An Anthropic API key ([console.anthropic.com](https://console.anthropic.com)) — or a Claude CLI already authenticated on the host

## Installation

This project has two parts: a **custom component** (the HA integration) and an **add-on** (runs the Claude Agent SDK). Both install from the same repository URL.

### Step 1: Install the Add-on

1. Go to **Settings → Add-ons → Add-on Store**
2. Click the **⋮** menu (top right) → **Repositories**
3. Add: `https://github.com/Marsunpaisti/HAClaudeAgent`
4. Find **HA Claude Agent** in the add-on list and install it
5. Start the add-on

### Step 2: Install the Integration

1. Open **HACS → Integrations**
2. Click **⋮** menu → **Custom repositories**
3. Add: `https://github.com/Marsunpaisti/HAClaudeAgent` — Category: **Integration**
4. Install **HA Claude Agent**
5. Restart Home Assistant

### Step 3: Configure

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **HA Claude Agent**
3. Enter your Anthropic API key (or leave empty if the CLI is already authenticated)
4. A default "Claude Agent" is created automatically
```

The "How It Works" section should mention the add-on:

```markdown
## How It Works

The integration consists of two parts:

- **Custom component** (runs inside HA) — registers the conversation agent, builds the system prompt with exposed entities and HA context, handles config UI
- **Add-on** (runs in a separate Docker container) — runs the Claude Agent SDK with Node.js and the Claude Code CLI, executes tool calls against HA's REST API

When you send a message, the integration builds the context and forwards it to the add-on over HTTP. The add-on runs Claude's agentic loop (reasoning + tool calls) and returns the final response.

MCP tools available to Claude:

- **`call_service`** — calls any HA service on an exposed entity
- **`get_entity_state`** — reads the current state and attributes of an entity
- **`list_entities`** — lists available entities, optionally filtered by domain
- **`Read`** — read files on the system
- **`WebFetch`** — fetch content from a URL
- **`WebSearch`** — search the web
```

### CLAUDE.md

The architecture and file layout sections need to reflect the two-part structure. Replace the current content with something like:
