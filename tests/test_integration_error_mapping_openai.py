"""Integration-side guards for OpenAI error-message handling."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.ha_claude_agent.conversation import _ERROR_MESSAGES  # noqa: E402


def test_openai_error_keys_present() -> None:
    for key in (
        "openai_auth_failed",
        "openai_rate_limit",
        "openai_server_error",
        "openai_invalid_model",
        "openai_connection_error",
    ):
        assert key in _ERROR_MESSAGES, f"missing key: {key}"


def test_openai_error_messages_are_user_facing() -> None:
    assert "API key" in _ERROR_MESSAGES["openai_auth_failed"]
    assert "rate limit" in _ERROR_MESSAGES["openai_rate_limit"].lower()
    assert "server error" in _ERROR_MESSAGES["openai_server_error"].lower()
    assert "model name" in _ERROR_MESSAGES["openai_invalid_model"].lower()
    assert "base url" in _ERROR_MESSAGES["openai_connection_error"].lower()
