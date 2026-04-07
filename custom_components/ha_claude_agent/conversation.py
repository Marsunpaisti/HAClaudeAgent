"""Conversation platform for HA Claude Agent."""

from __future__ import annotations

import logging

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    query,
)

from homeassistant.components import conversation
from homeassistant.components.conversation import (
    AssistantContent,
    ChatLog,
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
    ConversationResult,
)
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CHAT_MODEL,
    CONF_MAX_TURNS,
    CONF_PROMPT,
    CONF_THINKING_EFFORT,
    DEFAULT_CHAT_MODEL,
    DEFAULT_MAX_TURNS,
    DEFAULT_PROMPT,
    DEFAULT_THINKING_EFFORT,
    DOMAIN,
    MCP_SERVER_NAME,
)
from .helpers import build_system_prompt

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up conversation entities from config subentries."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue
        async_add_entities(
            [HAClaudeAgentConversationEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class HAClaudeAgentConversationEntity(ConversationEntity):
    """HA Claude Agent conversation entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = False
    _attr_supported_features = ConversationEntityFeature.CONTROL

    def __init__(
        self, config_entry: ConfigEntry, subentry: ConfigSubentry
    ) -> None:
        """Initialize the entity."""
        self.entry = config_entry
        self.subentry = subentry
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Anthropic",
            model=subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def supported_languages(self) -> list[str] | str:
        """Return MATCH_ALL — Claude supports all languages."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register as a conversation agent when added to HA."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister as a conversation agent when removed."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Handle a conversation turn via the Claude Agent SDK."""
        runtime_data = self.entry.runtime_data

        # --- Build options for this turn ---
        model = self.subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)
        user_prompt = self.subentry.data.get(CONF_PROMPT, DEFAULT_PROMPT)
        system_prompt = build_system_prompt(self.hass, user_prompt)

        # Tool name prefix: mcp__{server_name}__{tool_name}
        tool_prefix = f"mcp__{MCP_SERVER_NAME}__"
        allowed_tools = [
            f"{tool_prefix}call_service",
            f"{tool_prefix}get_entity_state",
            f"{tool_prefix}list_entities",
        ]

        # Check for existing session to resume
        session_id: str | None = None
        if user_input.conversation_id:
            session_id = runtime_data.sessions.get(
                user_input.conversation_id
            )

        effort = self.subentry.data.get(
            CONF_THINKING_EFFORT, DEFAULT_THINKING_EFFORT
        )
        max_turns = int(
            self.subentry.data.get(CONF_MAX_TURNS, DEFAULT_MAX_TURNS)
        )

        options = ClaudeAgentOptions(
            model=model,
            system_prompt=system_prompt,
            mcp_servers={MCP_SERVER_NAME: runtime_data.mcp_server},
            tools=allowed_tools,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
            env={"ANTHROPIC_API_KEY": runtime_data.api_key},
            permission_mode="dontAsk",
            effort=effort,
        )

        # If resuming, set the resume session_id
        if session_id:
            options.resume = session_id

        # If a CLI path was configured, set it
        if runtime_data.cli_path:
            options.cli_path = runtime_data.cli_path

        # --- Run the agent and stream results ---
        new_session_id: str | None = None
        text_parts: list[str] = []
        result_text = ""

        try:
            async for message in query(
                prompt=user_input.text,
                options=options,
            ):
                # Capture session ID for future turns
                if (
                    isinstance(message, SystemMessage)
                    and message.subtype == "init"
                ):
                    new_session_id = message.data.get("session_id")

                # Accumulate text from all assistant messages
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)

                # Final result takes priority over accumulated text
                elif isinstance(message, ResultMessage):
                    if hasattr(message, "session_id") and message.session_id:
                        new_session_id = message.session_id
                    if message.subtype == "success" and message.result:
                        result_text = message.result

            # Use ResultMessage if available, otherwise join all text blocks
            if not result_text and text_parts:
                result_text = "\n\n".join(text_parts)

        except Exception:
            _LOGGER.exception("Claude Agent SDK error")
            intent_response = intent.IntentResponse(
                language=user_input.language
            )
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                "Sorry, I encountered an error communicating with Claude.",
            )
            return ConversationResult(
                response=intent_response,
                conversation_id=chat_log.conversation_id,
            )

        # --- Store session mapping for conversation continuity ---
        if new_session_id:
            runtime_data.sessions[chat_log.conversation_id] = new_session_id

        # --- Add response to HA's ChatLog ---
        if result_text:
            chat_log.async_add_assistant_content_without_tools(
                AssistantContent(
                    agent_id=user_input.agent_id,
                    content=result_text,
                )
            )

        # --- Build HA response ---
        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(result_text or "I have no response.")
        return ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=False,
        )
