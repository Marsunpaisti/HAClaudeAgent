"""The HA Claude Agent integration."""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ADDON_HOST,
    CONF_ADDON_PORT,
    DEFAULT_ADDON_HOST,
    DEFAULT_ADDON_PORT,
    DOMAIN,
)

PLATFORMS = [Platform.CONVERSATION]

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

MAX_SESSIONS = 50


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

    addon_url: str
    sessions: BoundedSessionMap = field(default_factory=BoundedSessionMap)


type HAClaudeAgentConfigEntry = ConfigEntry[HAClaudeAgentRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: HAClaudeAgentConfigEntry
) -> bool:
    """Set up HA Claude Agent from a config entry."""
    _LOGGER.debug("Setting up HA Claude Agent entry %s", entry.entry_id)

    host = entry.data.get(CONF_ADDON_HOST, DEFAULT_ADDON_HOST)
    port = entry.data.get(CONF_ADDON_PORT, DEFAULT_ADDON_PORT)
    addon_url = f"http://{host}:{port}"

    # Check add-on connectivity — raises ConfigEntryNotReady if unreachable.
    # HA retries with exponential backoff automatically.
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            f"{addon_url}/health",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                raise ConfigEntryNotReady(f"Add-on returned status {resp.status}")
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ConfigEntryNotReady(f"Cannot reach add-on at {addon_url}: {err}") from err

    entry.runtime_data = HAClaudeAgentRuntimeData(addon_url=addon_url)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.info(
        "HA Claude Agent set up with %d conversation subentries",
        sum(1 for s in entry.subentries.values() if s.subentry_type == "conversation"),
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HAClaudeAgentConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
