"""Tests for OpenAIBackend."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from agents import ModelSettings

ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from backend import OpenAIBackend, _map_openai_exception_to_key  # noqa: E402
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
    """Patches agents.Runner.run_streamed to yield canned events.

    Mirrors real RunResultStreaming: usage is exposed via
    ``result.context_wrapper.usage`` (not ``result.usage``).
    """

    class _FakeResult:
        def __init__(self, events):
            self._events = events
            self.final_output = "done"
            self.new_items = []
            self.raw_responses = []
            self.context_wrapper = SimpleNamespace(
                usage=SimpleNamespace(input_tokens=11, output_tokens=22)
            )

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
        patch("backend.OpenAIChatCompletionsModel"),
        patch("backend.Agent"),
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
    assert events[-1][1]["error"] is None


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
        patch("backend.OpenAIChatCompletionsModel"),
        patch("backend.Agent"),
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
async def test_openai_backend_surfaces_auth_error_via_result_event(tmp_path):
    """Auth failures land on ResultEvent.error as 'openai_auth_failed',
    NOT as a separate `exception` SSE event — the integration needs the
    terminal ResultEvent to record usage + map the error string."""
    import openai

    req = QueryRequest(
        prompt="hello",
        model="gpt-4.1",
        system_prompt="be helpful",
        max_turns=1,
        session_id="s",
        exposed_entities=[],
    )
    ha_client = AsyncMock()

    mock_response = Mock()
    mock_response.request = Mock()
    auth_err = openai.AuthenticationError(
        message="bad key", response=mock_response, body=None
    )

    def _raise(*a, **kw):
        raise auth_err

    with (
        patch("backend.Runner") as Runner,
        patch("backend.SQLiteSession"),
        patch("backend.AsyncOpenAI"),
        patch("backend.OpenAIChatCompletionsModel"),
        patch("backend.Agent"),
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
    # Critical: no `exception` event — it would raise in sdk_stream and
    # tear down the iterator before the terminal ResultEvent is seen.
    assert "exception" not in kinds
    assert kinds[-1] == "OpenAIResultEvent"
    assert events[-1][1]["error"] == "openai_auth_failed"


@pytest.mark.asyncio
async def test_openai_backend_surfaces_unknown_error_as_raw_string(tmp_path):
    """Non-openai exceptions still land on ResultEvent.error (not via
    the exception channel), falling back to a 'Cls: msg' string."""
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
        patch("backend.OpenAIChatCompletionsModel"),
        patch("backend.Agent"),
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
    assert "exception" not in kinds
    assert kinds[-1] == "OpenAIResultEvent"
    assert events[-1][1]["error"] == "_Boom: boom"


@pytest.mark.asyncio
async def test_openai_backend_passes_model_and_tools_to_runner(
    fake_run_streamed, tmp_path
):
    """Wiring check: Agent receives OpenAIChatCompletionsModel(model, client)
    and the HA tool list. Runner.run_streamed gets the session + max_turns."""
    req = QueryRequest(
        prompt="hello",
        model="gpt-4.1",
        system_prompt="be helpful",
        max_turns=3,
        session_id="sess-1",
        exposed_entities=["light.kitchen"],
    )
    ha_client = AsyncMock()
    fake_result = fake_run_streamed([])

    ha_tool_sentinel = [object(), object()]

    with (
        patch("backend.Runner") as Runner,
        patch("backend.SQLiteSession") as SQLiteSession,
        patch("backend.AsyncOpenAI") as AsyncOpenAICls,
        patch("backend.OpenAIChatCompletionsModel") as ModelCls,
        patch("backend.Agent") as AgentCls,
        patch(
            "backend.create_ha_tools_openai", return_value=ha_tool_sentinel
        ) as mk_tools,
    ):
        Runner.run_streamed = MagicMock(return_value=fake_result)
        SQLiteSession.return_value = MagicMock(name="session")

        async for _ in OpenAIBackend(
            api_key="k",
            base_url="https://example/v1",
            sessions_db_path=str(tmp_path / "sessions.db"),
        ).stream_query(req, ha_client):
            pass

    # AsyncOpenAI built once per request with the right creds.
    AsyncOpenAICls.assert_called_once_with(base_url="https://example/v1", api_key="k")
    # Model wraps the client, no set_default_openai_client races.
    ModelCls.assert_called_once()
    _, model_kwargs = ModelCls.call_args
    assert model_kwargs["model"] == "gpt-4.1"
    assert model_kwargs["openai_client"] is AsyncOpenAICls.return_value

    # Agent receives our HA tools + the wrapped model.
    _, agent_kwargs = AgentCls.call_args
    assert agent_kwargs["tools"] is ha_tool_sentinel
    assert agent_kwargs["model"] is ModelCls.return_value
    assert agent_kwargs["instructions"] == "be helpful"
    assert isinstance(agent_kwargs["model_settings"], ModelSettings)
    assert agent_kwargs["model_settings"].reasoning.effort == "medium"

    mk_tools.assert_called_once_with(ha_client, ["light.kitchen"])

    # SQLite session uses the resolved session_id + configured db path.
    _, sess_kwargs = SQLiteSession.call_args
    assert sess_kwargs["session_id"] == "sess-1"
    assert sess_kwargs["db_path"] == str(tmp_path / "sessions.db")

    # Runner.run_streamed gets the Agent, the prompt, the session, max_turns.
    run_args, run_kwargs = Runner.run_streamed.call_args
    assert run_args[0] is AgentCls.return_value
    assert run_args[1] == "hello"
    assert run_kwargs["session"] is SQLiteSession.return_value
    assert run_kwargs["max_turns"] == 3


@pytest.mark.asyncio
async def test_openai_backend_maps_max_effort_to_xhigh(fake_run_streamed, tmp_path):
    req = QueryRequest(
        prompt="hello",
        model="gpt-4.1",
        system_prompt="be helpful",
        max_turns=1,
        effort="max",
        session_id="sess-1",
        exposed_entities=[],
    )
    ha_client = AsyncMock()
    fake_result = fake_run_streamed([])

    with (
        patch("backend.Runner") as Runner,
        patch("backend.SQLiteSession"),
        patch("backend.AsyncOpenAI"),
        patch("backend.OpenAIChatCompletionsModel"),
        patch("backend.Agent") as AgentCls,
    ):
        Runner.run_streamed = MagicMock(return_value=fake_result)

        async for _ in OpenAIBackend(
            api_key="k",
            base_url="https://example/v1",
            sessions_db_path=str(tmp_path / "sessions.db"),
        ).stream_query(req, ha_client):
            pass

    _, agent_kwargs = AgentCls.call_args
    assert agent_kwargs["model_settings"].reasoning.effort == "xhigh"


# _map_openai_exception_to_key lives on the add-on side now (so the
# error-string lands on OpenAIResultEvent.error instead of the
# `exception` channel that sdk_stream converts into a terminal raise).


def test_map_openai_exception_auth():
    import openai

    mock_response = Mock()
    mock_response.request = Mock()
    err = openai.AuthenticationError(
        message="bad key", response=mock_response, body=None
    )
    assert _map_openai_exception_to_key(err) == "openai_auth_failed"


def test_map_openai_exception_rate_limit():
    import openai

    mock_response = Mock()
    mock_response.request = Mock()
    err = openai.RateLimitError(message="too many", response=mock_response, body=None)
    assert _map_openai_exception_to_key(err) == "openai_rate_limit"


def test_map_openai_exception_not_found():
    import openai

    mock_response = Mock()
    mock_response.request = Mock()
    err = openai.NotFoundError(message="no model", response=mock_response, body=None)
    assert _map_openai_exception_to_key(err) == "openai_invalid_model"


def test_map_openai_exception_connection():
    import openai

    err = openai.APIConnectionError(request=None)
    assert _map_openai_exception_to_key(err) == "openai_connection_error"


def test_map_openai_exception_server_error():
    import openai

    mock_response = Mock()
    mock_response.request = Mock()
    err = openai.InternalServerError(message="500", response=mock_response, body=None)
    assert _map_openai_exception_to_key(err) == "openai_server_error"


def test_map_openai_exception_unknown_returns_none():
    assert _map_openai_exception_to_key(ValueError("x")) is None
