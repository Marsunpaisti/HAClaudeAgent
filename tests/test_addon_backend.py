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
