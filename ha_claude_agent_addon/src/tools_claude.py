"""Claude-flavored HA tool wrappers.

Thin ``@tool``-decorated closures that call ``tool_logic`` and wrap the
result in the ``{"content": [...], "is_error": bool}`` envelope the
claude-agent-sdk expects.
"""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_sdk import tool
from ha_client import HAClient
from tool_logic import (
    ToolBlocked,
    ToolInvalidArgs,
    ToolNotFound,
    call_service_logic,
    get_entity_state_logic,
    list_entities_logic,
)

_LOGGER = logging.getLogger(__name__)


def create_ha_tools_claude(
    ha_client: HAClient,
    exposed_entities: list[str],
) -> list:
    """Create Claude SDK tool instances that proxy to HA via ha_client."""
    exposed_set = set(exposed_entities)

    def _ok(text: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": text}]}

    def _err(text: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": text}], "is_error": True}

    @tool(
        "call_service",
        "Call a Home Assistant service to control a device. "
        "Examples: domain='light', service='turn_on', "
        "entity_id='light.living_room'. "
        "service_data is a JSON object string for additional "
        "parameters; pass '{}' if none needed.",
        {
            "domain": str,
            "service": str,
            "entity_id": str,
            "service_data": str,
        },
    )
    async def call_service(args: dict[str, Any]) -> dict[str, Any]:
        try:
            text = await call_service_logic(
                ha_client,
                exposed_set,
                domain=args["domain"],
                service=args["service"],
                entity_id=args["entity_id"],
                service_data=args.get("service_data", "{}"),
            )
            return _ok(text)
        except (ToolBlocked, ToolInvalidArgs) as err:
            return _err(str(err))
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("call_service failed: %s", err)
            return _err(f"Error calling service: {err}")

    @tool(
        "get_entity_state",
        "Get the current state and attributes of a Home Assistant entity.",
        {"entity_id": str},
    )
    async def get_entity_state(args: dict[str, Any]) -> dict[str, Any]:
        try:
            text = await get_entity_state_logic(
                ha_client, exposed_set, entity_id=args["entity_id"]
            )
            return _ok(text)
        except (ToolBlocked, ToolNotFound) as err:
            return _err(str(err))

    @tool(
        "list_entities",
        "List Home Assistant entities filtered by domain "
        "(e.g., 'light', 'switch', 'sensor'). Pass empty string "
        "to list all. Returns entity IDs, names, and states.",
        {"domain": str},
    )
    async def list_entities(args: dict[str, Any]) -> dict[str, Any]:
        text = await list_entities_logic(
            ha_client, exposed_set, domain_filter=args.get("domain", "")
        )
        return _ok(text)

    return [call_service, get_entity_state, list_entities]
