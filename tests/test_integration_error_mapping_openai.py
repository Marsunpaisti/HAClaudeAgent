"""Tests for OpenAI exception -> error message mapping."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.ha_claude_agent.conversation import (  # noqa: E402
    _ERROR_MESSAGES,
    _map_openai_exception_to_key,
)


def test_openai_error_keys_present():
    for key in (
        "openai_auth_failed",
        "openai_rate_limit",
        "openai_server_error",
        "openai_invalid_model",
        "openai_connection_error",
    ):
        assert key in _ERROR_MESSAGES, f"missing key: {key}"


def test_auth_error_maps_to_auth_failed():
    import openai

    mock_response = Mock()
    mock_response.request = Mock()
    err = openai.AuthenticationError(
        message="bad key", response=mock_response, body=None
    )
    assert _map_openai_exception_to_key(err) == "openai_auth_failed"


def test_rate_limit_maps():
    import openai

    mock_response = Mock()
    mock_response.request = Mock()
    err = openai.RateLimitError(message="too many", response=mock_response, body=None)
    assert _map_openai_exception_to_key(err) == "openai_rate_limit"


def test_not_found_maps_to_invalid_model():
    import openai

    mock_response = Mock()
    mock_response.request = Mock()
    err = openai.NotFoundError(message="no model", response=mock_response, body=None)
    assert _map_openai_exception_to_key(err) == "openai_invalid_model"


def test_connection_error_maps():
    import openai

    err = openai.APIConnectionError(request=None)
    assert _map_openai_exception_to_key(err) == "openai_connection_error"


def test_api_error_maps_to_server_error():
    import openai

    # openai.APIError is a base class — use a concrete subclass that the
    # SDK actually raises for 5xx responses.
    mock_response = Mock()
    mock_response.request = Mock()
    err = openai.InternalServerError(message="500", response=mock_response, body=None)
    assert _map_openai_exception_to_key(err) == "openai_server_error"


def test_unknown_exception_returns_none():
    assert _map_openai_exception_to_key(ValueError("x")) is None
