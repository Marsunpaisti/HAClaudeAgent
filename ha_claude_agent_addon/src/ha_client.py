"""Async client for the Home Assistant REST API via the Supervisor proxy."""

from __future__ import annotations

import asyncio
import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)


class HAClient:
    """Thin async wrapper around the HA REST API.

    Inside an HAOS add-on the Supervisor injects SUPERVISOR_TOKEN and
    proxies requests sent to http://supervisor/core/api/*.
    """

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(headers=self._headers)
            return self._session

    async def call_service(
        self, domain: str, service: str, data: dict
    ) -> list[dict]:
        """POST /api/services/{domain}/{service}."""
        session = await self._get_session()
        url = f"{self._base_url}/api/services/{domain}/{service}"
        _LOGGER.info("call_service: %s.%s -> %s", domain, service, url)
        async with session.post(url, json=data) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_state(self, entity_id: str) -> dict | None:
        """GET /api/states/{entity_id}.  Returns None on 404."""
        session = await self._get_session()
        url = f"{self._base_url}/api/states/{entity_id}"
        async with session.get(url) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            return await resp.json()

    async def get_states(self) -> list[dict]:
        """GET /api/states — all entity states."""
        session = await self._get_session()
        url = f"{self._base_url}/api/states"
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
