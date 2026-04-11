"""Unit tests for usage.py — pure-Python, no hass fixture required."""

from __future__ import annotations

from claude_agent_sdk import ResultMessage
from homeassistant.helpers.entity import DeviceInfo

from custom_components.ha_claude_agent.const import DOMAIN
from custom_components.ha_claude_agent.sensor import (
    HAClaudeAgentUsageCounterSensor,
)
from custom_components.ha_claude_agent.usage import (
    UsagePayload,
    _usage_from_result,
)


def _make_result(
    *,
    total_cost_usd: float | None = 0.012,
    usage: dict | None = None,
) -> ResultMessage:
    """Construct a minimal ResultMessage for tests.

    ResultMessage has many required fields; we pass reasonable defaults
    for the ones that aren't under test.
    """
    return ResultMessage(
        subtype="success",
        duration_ms=1234,
        duration_api_ms=1000,
        is_error=False,
        num_turns=1,
        session_id="sess_test",
        total_cost_usd=total_cost_usd,
        usage=usage,
    )


def test_usage_from_result_happy_path() -> None:
    result = _make_result(
        total_cost_usd=0.0123,
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 200,
            "cache_creation_input_tokens": 10,
        },
    )
    payload = _usage_from_result(result)
    assert payload == UsagePayload(
        cost_usd=0.0123,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=200,
        cache_write_tokens=10,
    )


def test_usage_from_result_none_cost_becomes_zero() -> None:
    result = _make_result(total_cost_usd=None, usage={"input_tokens": 5})
    payload = _usage_from_result(result)
    assert payload.cost_usd == 0.0
    assert payload.input_tokens == 5


def test_usage_from_result_none_usage_becomes_zero_tokens() -> None:
    result = _make_result(total_cost_usd=0.01, usage=None)
    payload = _usage_from_result(result)
    assert payload.cost_usd == 0.01
    assert payload.input_tokens == 0
    assert payload.output_tokens == 0
    assert payload.cache_read_tokens == 0
    assert payload.cache_write_tokens == 0


def test_usage_from_result_empty_usage_dict() -> None:
    result = _make_result(total_cost_usd=0.0, usage={})
    payload = _usage_from_result(result)
    assert payload.cost_usd == 0.0
    assert payload.input_tokens == 0
    assert payload.output_tokens == 0
    assert payload.cache_read_tokens == 0
    assert payload.cache_write_tokens == 0


def test_usage_from_result_missing_individual_keys() -> None:
    result = _make_result(
        total_cost_usd=0.02,
        usage={"input_tokens": 42},  # no output_tokens or cache fields
    )
    payload = _usage_from_result(result)
    assert payload.input_tokens == 42
    assert payload.output_tokens == 0
    assert payload.cache_read_tokens == 0
    assert payload.cache_write_tokens == 0


def test_usage_from_result_extra_unknown_keys_ignored() -> None:
    result = _make_result(
        total_cost_usd=0.01,
        usage={
            "input_tokens": 1,
            "output_tokens": 2,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 4,
            "some_future_token_type": 9999,
        },
    )
    payload = _usage_from_result(result)
    assert payload.input_tokens == 1
    assert payload.output_tokens == 2
    assert payload.cache_read_tokens == 3
    assert payload.cache_write_tokens == 4
    # Unknown keys are silently ignored — no error, no warning.


# --------------------------------------------------------------------
# Sensor callback logic tests
# --------------------------------------------------------------------
# These tests exercise HAClaudeAgentUsageCounterSensor._handle_usage
# without a hass fixture by:
#   1. Constructing the sensor directly
#   2. Monkey-patching async_write_ha_state to a no-op
#   3. Setting _attr_native_value manually
#   4. Calling _handle_usage and asserting on _attr_native_value
# This does not exercise async_added_to_hass / RestoreSensor / real
# dispatcher wiring — those are verified manually on a live HA.
# --------------------------------------------------------------------


def _make_sensor(
    *,
    metric: str,
    filter_subentry_id: str | None,
    initial_value: float | int = 0,
    unit: str = "USD",
) -> HAClaudeAgentUsageCounterSensor:
    sensor = HAClaudeAgentUsageCounterSensor(
        device_info=DeviceInfo(identifiers={(DOMAIN, "test")}),
        unique_id=f"test_{metric}_{filter_subentry_id or 'all'}",
        translation_key=f"total_{metric}",
        metric=metric,
        filter_subentry_id=filter_subentry_id,
        native_unit_of_measurement=unit,
        device_class=None,
    )
    sensor._attr_native_value = initial_value
    # Stub out the HA state write — we're testing logic, not HA wiring.
    sensor.async_write_ha_state = lambda: None  # type: ignore[method-assign]
    return sensor


def _make_payload(
    *,
    cost_usd: float = 0.01,
    input_tokens: int = 10,
    output_tokens: int = 20,
    cache_read_tokens: int = 30,
    cache_write_tokens: int = 40,
) -> UsagePayload:
    return UsagePayload(
        cost_usd=cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )


def test_sensor_per_agent_filter_accepts_matching_subentry() -> None:
    sensor = _make_sensor(metric="cost_usd", filter_subentry_id="A")
    sensor._handle_usage("A", _make_payload(cost_usd=0.05))
    assert sensor._attr_native_value == 0.05


def test_sensor_per_agent_filter_rejects_other_subentry() -> None:
    sensor = _make_sensor(metric="cost_usd", filter_subentry_id="A", initial_value=1.23)
    sensor._handle_usage("B", _make_payload(cost_usd=0.05))
    assert sensor._attr_native_value == 1.23  # unchanged


def test_sensor_integration_accepts_any_subentry() -> None:
    sensor = _make_sensor(metric="cost_usd", filter_subentry_id=None)
    sensor._handle_usage("A", _make_payload(cost_usd=0.01))
    sensor._handle_usage("B", _make_payload(cost_usd=0.02))
    assert sensor._attr_native_value == 0.03


def test_sensor_accumulates_across_calls() -> None:
    sensor = _make_sensor(metric="input_tokens", filter_subentry_id="A", unit="tokens")
    sensor._handle_usage("A", _make_payload(input_tokens=100))
    sensor._handle_usage("A", _make_payload(input_tokens=50))
    assert sensor._attr_native_value == 150


def test_sensor_reads_correct_metric_field() -> None:
    """Each metric-specific sensor only accumulates its own field."""
    payload = _make_payload(
        cost_usd=0.01,
        input_tokens=100,
        output_tokens=200,
        cache_read_tokens=300,
        cache_write_tokens=400,
    )
    for metric, expected, unit in [
        ("cost_usd", 0.01, "USD"),
        ("input_tokens", 100, "tokens"),
        ("output_tokens", 200, "tokens"),
        ("cache_read_tokens", 300, "tokens"),
        ("cache_write_tokens", 400, "tokens"),
    ]:
        sensor = _make_sensor(metric=metric, filter_subentry_id="A", unit=unit)
        sensor._handle_usage("A", payload)
        assert sensor._attr_native_value == expected, f"metric={metric}"
