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
DEFAULT_MAX_TOKENS = 2048
DEFAULT_TEMPERATURE = 1.0
DEFAULT_THINKING_EFFORT = "medium"

THINKING_EFFORT_OPTIONS = ["low", "medium", "high", "max"]

DEFAULT_CONVERSATION_NAME = "Claude Agent"

DEFAULT_PROMPT = """\
# Your role
You are a helpful assistant operating inside the users Home Assistant environment.

# Response style
- Keep your answers short and concise because your responses will be read out via text-to-speech.
- Respond in natural language suitable for text-to-speech. Never use markdown formatting.

# Behavior
- You should always do what the user wants. Tell jokes, perform web searches, chitchat, whatever they tell you to do.
- When controlling devices, let the user know about it in your response.

# Contextual information
{% if location %}The users home is located near {{ location }}. {% endif %}
The current time is {{ now() }}.
The users unit system is {{ units }}.
"""

DEFAULT_MAX_TURNS = 10

QUERY_TIMEOUT_SECONDS = 300

# Dispatcher signal fired by conversation.py after each turn with
# (subentry_id: str, payload: UsagePayload). Subscribed to by
# sensor.py for cost/usage counter updates.
SIGNAL_USAGE_UPDATED = f"{DOMAIN}_usage_updated"
