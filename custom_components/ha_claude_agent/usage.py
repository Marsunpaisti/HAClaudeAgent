"""Per-turn Claude usage extraction.

Pure module with no Home Assistant imports — allows unit testing
without a hass fixture, and keeps the `_usage_from_result` helper
decoupled from the conversation entity.
"""

from __future__ import annotations

from dataclasses import dataclass

from claude_agent_sdk import ResultMessage


@dataclass(frozen=True, slots=True)
class UsagePayload:
    """Per-turn Claude usage, normalised from ResultMessage.

    All fields are non-optional and default to zero when the SDK
    returns None or missing data. This keeps downstream code
    (sensors, dispatcher callbacks) from having to null-check.
    """

    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


def _usage_from_result(result: ResultMessage) -> UsagePayload:
    """Extract a UsagePayload from a ResultMessage.

    Handles None values and missing usage-dict keys by defaulting
    to zero. Forward-compatible: unknown keys in ``result.usage``
    are silently ignored.
    """
    cost = result.total_cost_usd if result.total_cost_usd is not None else 0.0
    usage = result.usage or {}
    return UsagePayload(
        cost_usd=float(cost),
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
    )
