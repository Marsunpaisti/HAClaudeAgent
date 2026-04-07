"""The HA Claude Agent integration."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import CONF_API_KEY, CONF_CLI_PATH, DOMAIN
from .tools import create_ha_mcp_server

PLATFORMS = [Platform.CONVERSATION]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


MAX_SESSIONS = 500


class BoundedSessionMap(OrderedDict):
    """OrderedDict that evicts oldest entries when max size is exceeded."""

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > MAX_SESSIONS:
            self.popitem(last=False)


@dataclass
class HAClaudeAgentRuntimeData:
    """Runtime data for the HA Claude Agent integration."""

    api_key: str
    cli_path: str
    mcp_server: Any  # The MCP server object from create_sdk_mcp_server
    sessions: BoundedSessionMap = field(default_factory=BoundedSessionMap)
    # Maps HA conversation_id -> SDK session_id (bounded LRU)


type HAClaudeAgentConfigEntry = ConfigEntry[HAClaudeAgentRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: HAClaudeAgentConfigEntry
) -> bool:
    """Set up HA Claude Agent from a config entry."""
    mcp_server = create_ha_mcp_server(hass)

    entry.runtime_data = HAClaudeAgentRuntimeData(
        api_key=entry.data[CONF_API_KEY],
        cli_path=entry.data.get(CONF_CLI_PATH, ""),
        mcp_server=mcp_server,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HAClaudeAgentConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
