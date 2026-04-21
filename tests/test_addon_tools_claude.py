"""Smoke tests for the Claude-flavored HA tool wrappers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from tools_claude import create_ha_tools_claude  # noqa: E402


@pytest.fixture
def ha_client():
    client = AsyncMock()
    client.call_service = AsyncMock()
    client.get_state = AsyncMock(return_value={"state": "on", "attributes": {}})
    client.get_states = AsyncMock(return_value=[])
    return client


def _find(tools, name):
    for t in tools:
        if getattr(t, "name", None) == name:
            return t
    raise AssertionError(f"tool {name!r} not found; have {[t.name for t in tools]}")


@pytest.mark.asyncio
async def test_factory_returns_three_tools(ha_client):
    tools = create_ha_tools_claude(ha_client, exposed_entities=["light.kitchen"])
    names = {t.name for t in tools}
    assert names == {"call_service", "get_entity_state", "list_entities"}


@pytest.mark.asyncio
async def test_call_service_success_envelope(ha_client):
    tools = create_ha_tools_claude(ha_client, exposed_entities=["light.kitchen"])
    call = _find(tools, "call_service")
    result = await call.handler(
        {
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.kitchen",
            "service_data": "{}",
        }
    )
    assert result["content"][0]["type"] == "text"
    assert "Called light.turn_on" in result["content"][0]["text"]
    assert "is_error" not in result


@pytest.mark.asyncio
async def test_call_service_blocks_unexposed_returns_error_envelope(ha_client):
    tools = create_ha_tools_claude(ha_client, exposed_entities=["light.kitchen"])
    call = _find(tools, "call_service")
    result = await call.handler(
        {
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.bedroom",
            "service_data": "{}",
        }
    )
    assert result.get("is_error") is True
    assert "not exposed" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_call_service_invalid_json_returns_error_envelope(ha_client):
    tools = create_ha_tools_claude(ha_client, exposed_entities=["light.kitchen"])
    call = _find(tools, "call_service")
    result = await call.handler(
        {
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.kitchen",
            "service_data": "{not json",
        }
    )
    assert result.get("is_error") is True
    assert "valid JSON" in result["content"][0]["text"]
