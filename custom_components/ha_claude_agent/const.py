"""Constants for the HA Claude Agent integration."""

DOMAIN = "ha_claude_agent"

CONF_API_KEY = "api_key"
CONF_CHAT_MODEL = "chat_model"
CONF_MAX_TOKENS = "max_tokens"
CONF_TEMPERATURE = "temperature"
CONF_PROMPT = "prompt"
CONF_CLI_PATH = "cli_path"
CONF_THINKING_EFFORT = "thinking_effort"
CONF_MAX_TURNS = "max_turns"

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

MCP_SERVER_NAME = "homeassistant"
