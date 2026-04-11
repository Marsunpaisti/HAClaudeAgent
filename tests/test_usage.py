"""Unit tests for usage.py — pure-Python, no hass fixture required."""

from __future__ import annotations

from claude_agent_sdk import ResultMessage

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
