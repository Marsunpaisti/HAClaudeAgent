"""Pure async HA tool core logic shared between backends.

The two decorator wrappers (``tools_claude.py`` for ``@tool`` and
``tools_openai.py`` for ``@function_tool``) call these functions. Core
logic raises typed sentinels; each wrapper maps them into its SDK's
native error envelope.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ha_client import HAClient

_LOGGER = logging.getLogger(__name__)


class ToolBlocked(Exception):
    """Raised when an operation targets an entity not exposed to the agent."""


class ToolInvalidArgs(Exception):
    """Raised when tool arguments fail basic validation (e.g. bad JSON)."""


class ToolNotFound(Exception):
    """Raised when a referenced entity does not exist in HA."""


async def call_service_logic(
    ha_client: HAClient,
    exposed_set: set[str],
    domain: str,
    service: str,
    entity_id: str,
    service_data: str,
) -> str:
    """Call an HA service and return a human-readable confirmation string."""
    if entity_id not in exposed_set:
        _LOGGER.warning("Blocked service call on unexposed entity: %s", entity_id)
        raise ToolBlocked(
            f"Entity {entity_id} is not exposed to conversation agents."
        )

    try:
        extra_data = json.loads(service_data) if service_data else {}
    except json.JSONDecodeError as err:
        raise ToolInvalidArgs("service_data must be valid JSON.") from err

    payload = {"entity_id": entity_id, **extra_data}
    await ha_client.call_service(domain, service, payload)

    state = await ha_client.get_state(entity_id)
    state_str = state["state"] if state else "unknown"
    return (
        f"Called {domain}.{service} on {entity_id}. Current state: {state_str}"
    )


async def get_entity_state_logic(
    ha_client: HAClient,
    exposed_set: set[str],
    entity_id: str,
) -> str:
    """Return the current state of an entity as a JSON string."""
    if entity_id not in exposed_set:
        _LOGGER.warning("Blocked state read on unexposed entity: %s", entity_id)
        raise ToolBlocked(
            f"Entity {entity_id} is not exposed to conversation agents."
        )

    state = await ha_client.get_state(entity_id)
    if state is None:
        raise ToolNotFound(f"Entity {entity_id} not found.")

    attrs = dict(state.get("attributes", {}))
    info = {
        "entity_id": entity_id,
        "state": state["state"],
        "friendly_name": attrs.pop("friendly_name", entity_id),
        "attributes": attrs,
    }
    return json.dumps(info, default=str)


async def list_entities_logic(
    ha_client: HAClient,
    exposed_set: set[str],
    domain_filter: str,
) -> str:
    """Return a JSON list of exposed entities, optionally filtered by domain."""
    all_states = await ha_client.get_states()
    entities = []
    for state in all_states:
        eid = state["entity_id"]
        if eid not in exposed_set:
            continue
        if domain_filter and not eid.startswith(f"{domain_filter}."):
            continue
        entities.append(
            {
                "entity_id": eid,
                "name": state.get("attributes", {}).get("friendly_name", eid),
                "state": state["state"],
            }
        )
    return json.dumps(entities, default=str)
