"""Tests for the Backend Protocol + ClaudeBackend refactor."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from backend import Backend, ClaudeBackend  # noqa: E402
from models import QueryRequest  # noqa: E402


def test_claude_backend_has_name():
    b = ClaudeBackend(auth_env={"ANTHROPIC_API_KEY": "sk-test"})
    assert b.name == "claude"
    assert isinstance(b, Backend)


@pytest.mark.asyncio
async def test_claude_backend_streams_query_events():
    """Smoke test: ClaudeBackend.stream_query yields SSE-formatted strings
    when the underlying SDK query yields a known message."""
    from claude_agent_sdk import SystemMessage

    req = QueryRequest(
        prompt="hello",
        model="claude-sonnet-4-6",
        system_prompt="be helpful",
        max_turns=1,
        exposed_entities=[],
    )
    ha_client = AsyncMock()

    async def fake_query(**kwargs):
        yield SystemMessage(subtype="init", data={"session_id": "sess-1"})

    with patch("backend.query", side_effect=lambda **kw: fake_query(**kw)):
        events = []
        async for event_str in ClaudeBackend(
            auth_env={"ANTHROPIC_API_KEY": "sk-test"}
        ).stream_query(req, ha_client):
            events.append(event_str)

    assert any("SystemMessage" in e for e in events)
    assert any("sess-1" in e for e in events)


@patch("server._read_addon_options")
def test_server_picks_claude_backend_when_configured(mock_options):
    import server

    mock_options.return_value = {
        "backend": "claude",
        "claude_auth_token": "sk-ant-api-test",
        "openai_api_key": "",
        "openai_base_url": "",
    }
    backend = server._select_backend(mock_options.return_value)
    assert backend.name == "claude"


@patch("server._read_addon_options")
def test_server_picks_openai_backend_when_configured(mock_options):
    import server

    mock_options.return_value = {
        "backend": "openai",
        "claude_auth_token": "",
        "openai_api_key": "sk-test",
        "openai_base_url": "https://example/v1",
    }
    backend = server._select_backend(mock_options.return_value)
    assert backend.name == "openai"


@patch("server._read_addon_options")
def test_server_raises_when_claude_token_missing(mock_options):
    import server

    mock_options.return_value = {
        "backend": "claude",
        "claude_auth_token": "",
    }
    with pytest.raises(RuntimeError, match="claude_auth_token"):
        server._select_backend(mock_options.return_value)


@patch("server._read_addon_options")
def test_server_raises_when_openai_key_missing(mock_options):
    import server

    mock_options.return_value = {
        "backend": "openai",
        "openai_api_key": "",
        "openai_base_url": "https://example/v1",
    }
    with pytest.raises(RuntimeError, match="openai_api_key"):
        server._select_backend(mock_options.return_value)


@patch("server._read_addon_options")
def test_server_raises_when_openai_base_url_missing(mock_options):
    import server

    mock_options.return_value = {
        "backend": "openai",
        "openai_api_key": "sk-test",
        "openai_base_url": "",
    }
    with pytest.raises(RuntimeError, match="openai_base_url"):
        server._select_backend(mock_options.return_value)


@patch("server._read_addon_options")
def test_server_accepts_legacy_auth_token_for_claude(mock_options):
    import server

    mock_options.return_value = {
        "backend": "claude",
        "auth_token": "sk-ant-legacy",  # old field, no claude_auth_token
        "claude_auth_token": "",
    }
    backend = server._select_backend(mock_options.return_value)
    assert backend.name == "claude"
