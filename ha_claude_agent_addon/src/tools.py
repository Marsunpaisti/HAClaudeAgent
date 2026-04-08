"""MCP tools for controlling Home Assistant via its REST API.

These tools are registered with the Claude Agent SDK's MCP server
and proxy all operations through the HA REST API using SUPERVISOR_TOKEN.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from claude_agent_sdk import tool
from ha_client import HAClient

_LOGGER = logging.getLogger(__name__)


def create_ha_tools(
    ha_client: HAClient,
    exposed_entities: list[str],
) -> list:
    """Create MCP tool instances that proxy to HA via ha_client.

    Parameters
    ----------
    ha_client:
        Initialised HAClient pointing at the Supervisor HA proxy.
    exposed_entities:
        Entity IDs the conversation agent is allowed to control.
        Passed per-request by the integration.
    """

    exposed_set = set(exposed_entities)

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
        entity_id = args["entity_id"]
        domain = args["domain"]
        service = args["service"]

        _LOGGER.info("call_service: %s.%s on %s", domain, service, entity_id)

        # Security: only allow calls on exposed entities
        if entity_id not in exposed_set:
            _LOGGER.warning("Blocked service call on unexposed entity: %s", entity_id)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Entity {entity_id} is not exposed to conversation agents."
                        ),
                    }
                ],
                "is_error": True,
            }

        raw_data = args.get("service_data", "{}")
        try:
            extra_data = json.loads(raw_data) if raw_data else {}
        except json.JSONDecodeError:
            return {
                "content": [
                    {"type": "text", "text": "service_data must be valid JSON."}
                ],
                "is_error": True,
            }

        service_data = {"entity_id": entity_id, **extra_data}

        try:
            await ha_client.call_service(domain, service, service_data)
            # Read back state after the service call
            state = await ha_client.get_state(entity_id)
            state_str = state["state"] if state else "unknown"
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
                "content": [{"type": "text", "text": f"Error calling service: {err}"}],
                "is_error": True,
            }

    @tool(
        "get_entity_state",
        "Get the current state and attributes of a Home Assistant entity.",
        {"entity_id": str},
    )
    async def get_entity_state(args: dict[str, Any]) -> dict[str, Any]:
        entity_id = args["entity_id"]
        _LOGGER.debug("get_entity_state: %s", entity_id)

        state = await ha_client.get_state(entity_id)
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

        attrs = dict(state.get("attributes", {}))
        info = {
            "entity_id": entity_id,
            "state": state["state"],
            "friendly_name": attrs.pop("friendly_name", entity_id),
            "attributes": attrs,
        }
        return {"content": [{"type": "text", "text": json.dumps(info, default=str)}]}

    @tool(
        "list_entities",
        "List Home Assistant entities filtered by domain "
        "(e.g., 'light', 'switch', 'sensor'). Pass empty string "
        "to list all. Returns entity IDs, names, and states.",
        {"domain": str},
    )
    async def list_entities(args: dict[str, Any]) -> dict[str, Any]:
        domain_filter = args.get("domain", "")
        _LOGGER.debug("list_entities: domain_filter=%s", domain_filter or "(all)")

        all_states = await ha_client.get_states()
        entities = []
        for state in all_states:
            eid = state["entity_id"]
            if domain_filter and not eid.startswith(f"{domain_filter}."):
                continue
            entities.append(
                {
                    "entity_id": eid,
                    "name": state.get("attributes", {}).get("friendly_name", eid),
                    "state": state["state"],
                }
            )

        return {
            "content": [{"type": "text", "text": json.dumps(entities, default=str)}]
        }

    return [call_service, get_entity_state, list_entities]
