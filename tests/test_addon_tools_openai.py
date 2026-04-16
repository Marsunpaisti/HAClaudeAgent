"""Smoke tests for the OpenAI-flavored HA tool wrappers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from tools_openai import create_ha_tools_openai  # noqa: E402


@pytest.fixture
def ha_client():
    client = AsyncMock()
    client.call_service = AsyncMock()
    client.get_state = AsyncMock(return_value={"state": "on", "attributes": {}})
    client.get_states = AsyncMock(return_value=[])
    return client


def _tool_names(tools):
    # openai-agents FunctionTool exposes `.name` on the decorated wrapper.
    return {t.name for t in tools}


@pytest.mark.asyncio
async def test_factory_returns_three_named_tools(ha_client):
    tools = create_ha_tools_openai(ha_client, exposed_entities=["light.kitchen"])
    assert _tool_names(tools) == {
        "call_service",
        "get_entity_state",
        "list_entities",
    }


@pytest.mark.asyncio
async def test_call_service_returns_string_on_success(ha_client):
    raw = create_ha_tools_openai.__ha_raw__(ha_client, exposed_entities=["light.kitchen"])
    text = await raw["call_service"](
        domain="light",
        service="turn_on",
        entity_id="light.kitchen",
        service_data="{}",
    )
    assert "Called light.turn_on" in text
    assert not text.startswith("Error:")


@pytest.mark.asyncio
async def test_call_service_blocks_unexposed(ha_client):
    raw = create_ha_tools_openai.__ha_raw__(ha_client, exposed_entities=["light.kitchen"])
    text = await raw["call_service"](
        domain="light",
        service="turn_on",
        entity_id="light.bedroom",
        service_data="{}",
    )
    assert text.startswith("Error:")
    assert "not exposed" in text


@pytest.mark.asyncio
async def test_get_entity_state_not_found_returns_error_string(ha_client):
    ha_client.get_state.return_value = None
    raw = create_ha_tools_openai.__ha_raw__(ha_client, exposed_entities=["light.kitchen"])
    text = await raw["get_entity_state"](entity_id="light.kitchen")
    assert text.startswith("Error:")
    assert "not found" in text
