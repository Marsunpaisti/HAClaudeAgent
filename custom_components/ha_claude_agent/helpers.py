"""Helper utilities for HA Claude Agent."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.conversation import async_should_expose
from homeassistant.core import HomeAssistant

from .const import MCP_SERVER_NAME


def build_system_prompt(
    hass: HomeAssistant,
    user_prompt: str,
) -> str:
    """Build the full system prompt with HA context and exposed entities.

    Called on every turn so entity states are always current.
    """
    ha_name = hass.config.location_name or "Home"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Gather exposed entities
    entity_lines: list[str] = []
    for state in hass.states.async_all():
        if not async_should_expose(hass, "conversation", state.entity_id):
            continue
        name = state.attributes.get("friendly_name", state.entity_id)
        entity_lines.append(
            f"- {state.entity_id}: {name} (state: {state.state})"
        )

    entity_section = "\n".join(entity_lines) if entity_lines else "(none exposed)"

    tool_prefix = f"mcp__{MCP_SERVER_NAME}__"

    return f"""\
{user_prompt}

## Home Assistant Context
- Home name: {ha_name}
- Current time: {now}

## Exposed Entities
These are the entities you can monitor and control:
{entity_section}

## Available Tools
Use `{tool_prefix}call_service` to control devices (turn on/off, set values, etc.).
Use `{tool_prefix}get_entity_state` to check a device's current state and attributes.
Use `{tool_prefix}list_entities` to discover entities by domain.

Only control entities listed above. If a user asks about an entity not listed, tell them it's not exposed to you.
"""
