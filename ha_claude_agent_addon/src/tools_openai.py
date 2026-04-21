"""OpenAI-flavored HA tool wrappers.

Thin ``@function_tool``-decorated closures that call ``tool_logic`` and
return plain strings (success) or ``"Error: ..."`` (sentinel), which is
the format openai-agents feeds back to the model.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from agents import function_tool
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


def _build_raw(
    ha_client: HAClient, exposed_set: set[str]
) -> dict[str, Callable[..., Awaitable[str]]]:
    """Return undecorated async callables — used by the factory to build
    both the ``@function_tool`` list and a test-visible raw map."""

    async def call_service(
        domain: str,
        service: str,
        entity_id: str,
        service_data: str,
    ) -> str:
        """Call a Home Assistant service to control a device.

        domain: HA service domain (e.g. 'light', 'switch').
        service: Service name (e.g. 'turn_on').
        entity_id: Target entity ID (e.g. 'light.living_room').
        service_data: JSON object string with extra parameters; pass '{}' if none.
        """
        try:
            return await call_service_logic(
                ha_client,
                exposed_set,
                domain=domain,
                service=service,
                entity_id=entity_id,
                service_data=service_data,
            )
        except (ToolBlocked, ToolInvalidArgs) as err:
            return f"Error: {err}"
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("call_service failed: %s", err)
            return f"Error: {err}"

    async def get_entity_state(entity_id: str) -> str:
        """Get the current state and attributes of a Home Assistant entity.

        entity_id: Target entity ID (e.g. 'sensor.temperature').
        """
        try:
            return await get_entity_state_logic(
                ha_client, exposed_set, entity_id=entity_id
            )
        except (ToolBlocked, ToolNotFound) as err:
            return f"Error: {err}"

    async def list_entities(domain: str) -> str:
        """List Home Assistant entities filtered by domain.

        domain: Domain filter (e.g. 'light'). Pass empty string to list all.
        Returns entity IDs, names, and states as a JSON string.
        """
        return await list_entities_logic(ha_client, exposed_set, domain_filter=domain)

    return {
        "call_service": call_service,
        "get_entity_state": get_entity_state,
        "list_entities": list_entities,
    }


def create_ha_tools_openai(
    ha_client: HAClient,
    exposed_entities: list[str],
) -> list:
    """Create openai-agents FunctionTool instances that proxy to HA."""
    exposed_set = set(exposed_entities)
    raw = _build_raw(ha_client, exposed_set)
    return [function_tool(fn) for fn in raw.values()]


# Test reach-through: returns the undecorated async callables so unit tests
# can invoke the wrappers without the openai-agents runtime harness.
def __ha_raw__(
    ha_client: HAClient, exposed_entities: list[str]
) -> dict[str, Callable[..., Awaitable[str]]]:
    return _build_raw(ha_client, set(exposed_entities))


create_ha_tools_openai.__ha_raw__ = __ha_raw__  # type: ignore[attr-defined]
