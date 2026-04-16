"""Unit tests for the shared HA tool core logic."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from tool_logic import (  # noqa: E402
    ToolBlocked,
    ToolInvalidArgs,
    ToolNotFound,
    call_service_logic,
    get_entity_state_logic,
    list_entities_logic,
)


@pytest.fixture
def ha_client():
    client = AsyncMock()
    client.call_service = AsyncMock()
    client.get_state = AsyncMock()
    client.get_states = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_call_service_blocks_unexposed_entity(ha_client):
    with pytest.raises(ToolBlocked):
        await call_service_logic(
            ha_client,
            exposed_set={"light.kitchen"},
            domain="light",
            service="turn_on",
            entity_id="light.bedroom",
            service_data="{}",
        )
    ha_client.call_service.assert_not_called()


@pytest.mark.asyncio
async def test_call_service_rejects_invalid_json(ha_client):
    with pytest.raises(ToolInvalidArgs):
        await call_service_logic(
            ha_client,
            exposed_set={"light.kitchen"},
            domain="light",
            service="turn_on",
            entity_id="light.kitchen",
            service_data="{not json",
        )


@pytest.mark.asyncio
async def test_call_service_success_returns_state_string(ha_client):
    ha_client.get_state.return_value = {"state": "on", "attributes": {}}

    result = await call_service_logic(
        ha_client,
        exposed_set={"light.kitchen"},
        domain="light",
        service="turn_on",
        entity_id="light.kitchen",
        service_data="{}",
    )

    ha_client.call_service.assert_awaited_once_with(
        "light", "turn_on", {"entity_id": "light.kitchen"}
    )
    assert "light.kitchen" in result
    assert "on" in result


@pytest.mark.asyncio
async def test_get_entity_state_blocks_unexposed(ha_client):
    with pytest.raises(ToolBlocked):
        await get_entity_state_logic(
            ha_client,
            exposed_set={"light.kitchen"},
            entity_id="sensor.unexposed",
        )


@pytest.mark.asyncio
async def test_get_entity_state_not_found(ha_client):
    ha_client.get_state.return_value = None
    with pytest.raises(ToolNotFound):
        await get_entity_state_logic(
            ha_client,
            exposed_set={"light.kitchen"},
            entity_id="light.kitchen",
        )


@pytest.mark.asyncio
async def test_get_entity_state_returns_json_text(ha_client):
    ha_client.get_state.return_value = {
        "state": "on",
        "attributes": {"friendly_name": "Kitchen Light", "brightness": 255},
    }

    result = await get_entity_state_logic(
        ha_client,
        exposed_set={"light.kitchen"},
        entity_id="light.kitchen",
    )

    assert '"entity_id": "light.kitchen"' in result
    assert '"state": "on"' in result
    assert '"friendly_name": "Kitchen Light"' in result


@pytest.mark.asyncio
async def test_list_entities_filters_by_domain_and_exposed(ha_client):
    ha_client.get_states.return_value = [
        {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
        {"entity_id": "light.bedroom", "state": "off", "attributes": {}},
        {"entity_id": "switch.kitchen", "state": "on", "attributes": {}},
    ]

    result = await list_entities_logic(
        ha_client,
        exposed_set={"light.kitchen", "switch.kitchen"},
        domain_filter="light",
    )

    assert "light.kitchen" in result
    assert "light.bedroom" not in result  # not exposed
    assert "switch.kitchen" not in result  # wrong domain
