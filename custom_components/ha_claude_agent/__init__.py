"""The HA Claude Agent integration."""

from __future__ import annotations

import asyncio
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
from .helpers import async_reverse_geocode

# SENSOR is first so its async_setup_entry runs before CONVERSATION
# attempts to dispatch usage signals — minimises the startup-race window
# where a turn could complete before sensors are subscribed.
PLATFORMS = [Platform.SENSOR, Platform.CONVERSATION]

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


class BoundedConversationLockMap(OrderedDict):
    """Conversation locks capped to the same size as the session cache."""

    def _prune_unlocked(self) -> None:
        while len(self) > MAX_SESSIONS:
            for key, lock in list(self.items()):
                if not lock.locked():
                    self.pop(key)
                    break
            else:
                # All retained locks are still active. Allow temporary overflow
                # instead of evicting a held lock and breaking serialization.
                return

    def get_lock(self, key: str) -> asyncio.Lock:
        lock = self.get(key)
        if lock is None:
            lock = asyncio.Lock()
            super().__setitem__(key, lock)
        self.move_to_end(key)
        self._prune_unlocked()
        return lock


@dataclass
class HAClaudeAgentRuntimeData:
    """Runtime data for the HA Claude Agent integration."""

    addon_url: str
    location: str | None = None
    sessions: BoundedSessionMap = field(default_factory=BoundedSessionMap)
    conversation_locks: BoundedConversationLockMap = field(
        default_factory=BoundedConversationLockMap
    )


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

    # Reverse-geocode home location (best-effort, non-blocking)
    location: str | None = None
    lat, lon = hass.config.latitude, hass.config.longitude
    if lat or lon:
        location = await async_reverse_geocode(session, lat, lon)
        if location:
            _LOGGER.info("Resolved home location: %s", location)
        else:
            _LOGGER.debug("Reverse geocoding returned no result")

    entry.runtime_data = HAClaudeAgentRuntimeData(
        addon_url=addon_url, location=location
    )

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
