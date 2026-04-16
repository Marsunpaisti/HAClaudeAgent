"""Tests for OpenAIBackend."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from backend import OpenAIBackend  # noqa: E402
from models import QueryRequest  # noqa: E402


def _parse_sse_events(chunks: list[str]) -> list[tuple[str, dict]]:
    """Parse a list of SSE-string chunks into [(event_type, data_dict)]."""
    out = []
    for chunk in chunks:
        lines = chunk.strip().split("\n")
        event_type = lines[0].removeprefix("event: ").strip()
        data_str = lines[1].removeprefix("data: ").strip()
        out.append((event_type, json.loads(data_str)))
    return out


@pytest.fixture
def fake_run_streamed():
    """Patches agents.Runner.run_streamed to yield canned events."""

    class _FakeResult:
        def __init__(self, events):
            self._events = events
            self.final_output = "done"
            self.new_items = []
            self.raw_responses = []
            self.usage = SimpleNamespace(input_tokens=11, output_tokens=22)

        async def stream_events(self):
            for e in self._events:
                yield e

    def _make(events):
        return _FakeResult(events)

    return _make


@pytest.mark.asyncio
async def test_openai_backend_emits_init_and_result(fake_run_streamed, tmp_path):
    req = QueryRequest(
        prompt="hello",
        model="gemini-2.0-flash",
        system_prompt="be helpful",
        max_turns=1,
        session_id="sess-xyz",
        exposed_entities=[],
    )
    ha_client = AsyncMock()

    fake_result = fake_run_streamed([])

    with (
        patch("backend.Runner") as Runner,
        patch("backend.SQLiteSession") as SQLiteSession,
        patch("backend.AsyncOpenAI"),
        patch("backend.set_default_openai_client"),
    ):
        Runner.run_streamed = MagicMock(return_value=fake_result)
        SQLiteSession.return_value = MagicMock()

        chunks = []
        async for c in OpenAIBackend(
            api_key="k",
            base_url="https://example/v1",
            sessions_db_path=str(tmp_path / "sessions.db"),
        ).stream_query(req, ha_client):
            chunks.append(c)

    events = _parse_sse_events(chunks)
    # First event is init, last is result
    assert events[0][0] == "OpenAIInitEvent"
    assert events[0][1]["session_id"] == "sess-xyz"
    assert events[-1][0] == "OpenAIResultEvent"
    assert events[-1][1]["input_tokens"] == 11
    assert events[-1][1]["output_tokens"] == 22


@pytest.mark.asyncio
async def test_openai_backend_generates_uuid_when_no_session_id(
    fake_run_streamed, tmp_path
):
    req = QueryRequest(
        prompt="hello",
        model="gpt-4.1",
        system_prompt="be helpful",
        max_turns=1,
        session_id=None,
        exposed_entities=[],
    )
    ha_client = AsyncMock()

    fake_result = fake_run_streamed([])

    with (
        patch("backend.Runner") as Runner,
        patch("backend.SQLiteSession") as SQLiteSession,
        patch("backend.AsyncOpenAI"),
        patch("backend.set_default_openai_client"),
    ):
        Runner.run_streamed = MagicMock(return_value=fake_result)
        SQLiteSession.return_value = MagicMock()

        chunks = []
        async for c in OpenAIBackend(
            api_key="k",
            base_url="https://example/v1",
            sessions_db_path=str(tmp_path / "sessions.db"),
        ).stream_query(req, ha_client):
            chunks.append(c)

    events = _parse_sse_events(chunks)
    assert events[0][0] == "OpenAIInitEvent"
    sid = events[0][1]["session_id"]
    assert isinstance(sid, str)
    assert len(sid) >= 16  # uuid hex is 32 chars


@pytest.mark.asyncio
async def test_openai_backend_emits_exception_event_on_runner_failure(tmp_path):
    req = QueryRequest(
        prompt="hello",
        model="gpt-4.1",
        system_prompt="be helpful",
        max_turns=1,
        session_id="s",
        exposed_entities=[],
    )
    ha_client = AsyncMock()

    class _Boom(Exception):
        pass

    def _raise(*a, **kw):
        raise _Boom("boom")

    with (
        patch("backend.Runner") as Runner,
        patch("backend.SQLiteSession"),
        patch("backend.AsyncOpenAI"),
        patch("backend.set_default_openai_client"),
    ):
        Runner.run_streamed = MagicMock(side_effect=_raise)

        chunks = []
        async for c in OpenAIBackend(
            api_key="k",
            base_url="https://example/v1",
            sessions_db_path=str(tmp_path / "sessions.db"),
        ).stream_query(req, ha_client):
            chunks.append(c)

    events = _parse_sse_events(chunks)
    kinds = [e[0] for e in events]
    assert "exception" in kinds
