"""Conversation platform for HA Claude Agent."""

from __future__ import annotations

from homeassistant.components.conversation import (
    AssistantContent,
    ChatLog,
    ConversationEntity,
    ConversationInput,
    ConversationResult,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import intent

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up conversation entities."""
    async_add_entities([HAClaudeAgentConversationEntity(config_entry)])


class HAClaudeAgentConversationEntity(ConversationEntity):
    """HA Claude Agent conversation entity."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the entity."""
        self.entry = config_entry
        self._attr_unique_id = config_entry.entry_id

    @property
    def supported_languages(self) -> list[str]:
        """Return a list of supported languages."""
        return ["en"]

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Handle an incoming message."""
        # TODO: Implement Anthropic Agent SDK call here
        chat_log.async_add_assistant_content_without_tools(
            AssistantContent(
                agent_id=user_input.agent_id,
                content="Hello! HA Claude Agent is not yet implemented.",
            )
        )
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(
            "Hello! HA Claude Agent is not yet implemented."
        )
        return ConversationResult(
            conversation_id=None,
            response=response,
            continue_conversation=False,
        )
