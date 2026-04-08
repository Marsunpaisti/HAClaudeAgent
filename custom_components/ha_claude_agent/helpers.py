"""Helper utilities for HA Claude Agent."""

from __future__ import annotations

import logging

import aiohttp
from homeassistant.components.homeassistant.exposed_entities import (
    async_should_expose,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template

_LOGGER = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_USER_AGENT = "HomeAssistant-ClaudeAgent/1.0"


async def async_reverse_geocode(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
) -> str | None:
    """Reverse-geocode coordinates to a rough location via Nominatim.

    Returns a string like "Main St, Fishtown, Philadelphia, Pennsylvania, US"
    or None on failure.
    """
    try:
        async with session.get(
            NOMINATIM_URL,
            params={
                "lat": str(latitude),
                "lon": str(longitude),
                "format": "json",
                "zoom": "17",
            },
            headers={"User-Agent": NOMINATIM_USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except (aiohttp.ClientError, TimeoutError):
        _LOGGER.debug("Reverse geocoding failed for %s,%s", latitude, longitude)
        return None

    address = data.get("address", {})
    parts = [
        address.get("road"),
        address.get("suburb") or address.get("neighbourhood"),
        address.get("city") or address.get("town") or address.get("village"),
        address.get("state"),
        address.get("country"),
    ]
    return ", ".join(p for p in parts if p) or None


def build_system_prompt(
    hass: HomeAssistant,
    user_prompt: str,
    location: str | None = None,
) -> str:
    """Build the full system prompt with HA context and exposed entities.

    The user_prompt is rendered as a Jinja2 template with these variables:
      ha_name, time, location, currency, units
    plus all standard HA template functions (states, now, areas, etc.).

    The exposed entities and control instructions are always appended
    and are not user-editable.
    """
    ha_name = hass.config.location_name or "Home"
    units = hass.config.units._name  # noqa: SLF001

    template_vars = {
        "ha_name": ha_name,
        "location": location or "",
        "units": units,
    }

    # Render user prompt as Jinja2 template
    try:
        rendered_prompt = Template(user_prompt, hass).async_render(
            variables=template_vars, parse_result=False
        )
    except TemplateError as err:
        _LOGGER.warning("Failed to render user prompt template: %s", err)
        rendered_prompt = user_prompt

    # Gather exposed entities
    entity_lines: list[str] = []
    for state in hass.states.async_all():
        if not async_should_expose(hass, "conversation", state.entity_id):
            continue
        name = state.attributes.get("friendly_name", state.entity_id)
        entity_lines.append(f"- {state.entity_id}: {name} (state: {state.state})")

    entity_section = "\n".join(entity_lines) if entity_lines else "(none exposed)"

    return f"""\
{rendered_prompt}

## Exposed Entities
These are the entities you can monitor and control:
{entity_section}

## Entity Control Instructions
- Do not try to control home assistant entities that are not listed as exposed. The tool will block the request.
- If a user asks about an entity not listed, tell them it's not exposed to you.
"""
