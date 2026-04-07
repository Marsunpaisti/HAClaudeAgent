"""MCP tools for HA Claude Agent — exposes HA services to the Claude Agent SDK."""

from __future__ import annotations

import json
import logging
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def create_ha_mcp_server(hass: HomeAssistant):
    """Create an MCP server with Home Assistant control tools.

    Tools use closures to capture the hass instance.
    """

    @tool(
        "call_service",
        "Call a Home Assistant service to control a device. "
        "Examples: domain='light', service='turn_on', entity_id='light.living_room'. "
        "Pass additional service data as a JSON object string if needed.",
        {
            "domain": str,
            "service": str,
            "entity_id": str,
            "service_data": str,
        },
    )
    async def call_service(args: dict[str, Any]) -> dict[str, Any]:
        domain = args["domain"]
        service = args["service"]
        entity_id = args["entity_id"]
        raw_data = args.get("service_data", "{}")

        try:
            extra_data = json.loads(raw_data) if raw_data else {}
        except json.JSONDecodeError:
            extra_data = {}

        service_data = {"entity_id": entity_id, **extra_data}

        try:
            await hass.services.async_call(
                domain, service, service_data, blocking=True
            )
            # Read back the entity state after the service call
            state = hass.states.get(entity_id)
            state_str = state.state if state else "unknown"
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Called {domain}.{service} on {entity_id}. "
                            f"Current state: {state_str}"
                        ),
                    }
                ]
            }
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Service call %s.%s failed for %s: %s",
                domain,
                service,
                entity_id,
                err,
            )
            return {
                "content": [
                    {"type": "text", "text": f"Error calling service: {err}"}
                ],
                "is_error": True,
            }

    @tool(
        "get_entity_state",
        "Get the current state and attributes of a Home Assistant entity.",
        {"entity_id": str},
    )
    async def get_entity_state(args: dict[str, Any]) -> dict[str, Any]:
        entity_id = args["entity_id"]
        state = hass.states.get(entity_id)

        if state is None:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Entity {entity_id} not found.",
                    }
                ],
                "is_error": True,
            }

        # Build a useful summary of state + key attributes
        attrs = dict(state.attributes)
        info = {
            "entity_id": entity_id,
            "state": state.state,
            "friendly_name": attrs.pop("friendly_name", entity_id),
            "attributes": attrs,
        }
        return {
            "content": [
                {"type": "text", "text": json.dumps(info, default=str)}
            ]
        }

    @tool(
        "list_entities",
        "List all available Home Assistant entities, optionally filtered by domain "
        "(e.g., 'light', 'switch', 'sensor'). Returns entity IDs, names, and states.",
        {"domain": str},
    )
    async def list_entities(args: dict[str, Any]) -> dict[str, Any]:
        domain_filter = args.get("domain", "")
        entities = []

        for state in hass.states.async_all():
            if domain_filter and not state.entity_id.startswith(
                f"{domain_filter}."
            ):
                continue
            entities.append(
                {
                    "entity_id": state.entity_id,
                    "name": state.attributes.get(
                        "friendly_name", state.entity_id
                    ),
                    "state": state.state,
                }
            )

        return {
            "content": [
                {"type": "text", "text": json.dumps(entities, default=str)}
            ]
        }

    return create_sdk_mcp_server(
        name="homeassistant",
        version="1.0.0",
        tools=[call_service, get_entity_state, list_entities],
    )
