"""Tests for stream.py class lookup extension for openai-agents types."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.ha_claude_agent.openai_events import (  # noqa: E402
    OpenAIInitEvent,
    OpenAIResultEvent,
)
from custom_components.ha_claude_agent.stream import from_jsonable  # noqa: E402


def test_openai_init_event_reconstructs_from_wire_payload():
    payload = {"_type": "OpenAIInitEvent", "session_id": "sess-abc"}
    result = from_jsonable(payload)
    assert isinstance(result, OpenAIInitEvent)
    assert result.session_id == "sess-abc"


def test_openai_result_event_reconstructs_with_defaults():
    payload = {"_type": "OpenAIResultEvent", "input_tokens": 10, "output_tokens": 20}
    result = from_jsonable(payload)
    assert isinstance(result, OpenAIResultEvent)
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    assert result.error is None


def test_claude_types_still_reconstruct():
    # Sanity check: claude-agent-sdk lookup still works.
    from claude_agent_sdk import SystemMessage

    payload = {"_type": "SystemMessage", "subtype": "init", "data": {"x": 1}}
    result = from_jsonable(payload)
    assert isinstance(result, SystemMessage)
    assert result.subtype == "init"


def test_unknown_type_falls_back_to_raw_dict():
    payload = {"_type": "NonexistentClass", "foo": "bar"}
    result = from_jsonable(payload)
    assert result == {"foo": "bar"}


from custom_components.ha_claude_agent.conversation import (  # noqa: E402
    _deltas_from_openai,
    _StreamResult,
)

@pytest.mark.asyncio
async def test_deltas_from_openai_populates_session_id_from_init():
    state = _StreamResult()
    items = [
        OpenAIInitEvent(session_id="sess-hello"),
        OpenAIResultEvent(input_tokens=5, output_tokens=7),
    ]

    async def fake_items():
        for i in items:
            yield i

    out = []
    async for delta in _deltas_from_openai(fake_items(), state):
        out.append(delta)

    assert state.session_id == "sess-hello"
    assert state.usage_dict == {"input_tokens": 5, "output_tokens": 7}
    # OpenAI does not provide provider cost today, but the stream state still
    # needs a concrete 0.0 so usage dispatch is not gated off.
    assert state.cost_usd == 0.0


@pytest.mark.asyncio
async def test_deltas_from_openai_surfaces_error_from_result():
    state = _StreamResult()
    items = [
        OpenAIInitEvent(session_id="s"),
        OpenAIResultEvent(error="boom"),
    ]

    async def fake_items():
        for i in items:
            yield i

    async for _ in _deltas_from_openai(fake_items(), state):
        pass

    assert state.assistant_error == "boom"


# ---------------------------------------------------------------------------
# Round-trip wire test: SSE bytes → parse_sse_stream → from_jsonable →
# _deltas_from_openai. Exercises the full on-wire reconstruction path that
# the integration runs against the add-on's SSE output.
# ---------------------------------------------------------------------------


import json  # noqa: E402

from custom_components.ha_claude_agent.conversation import (  # noqa: E402
    _deltas_from_sdk_stream,
)


class _FakeSSEResp:
    """Minimal aiohttp-like response that streams the given bytes line-wise
    (with trailing newlines preserved — parse_sse_stream splits on blank
    lines)."""

    def __init__(self, payload: bytes) -> None:
        self.content = self._lines(payload)

    async def _lines(self, payload: bytes):
        # Yield line-by-line (including trailing newline) to match what
        # aiohttp.StreamReader does when iterated.
        for line in payload.splitlines(keepends=True):
            yield line


def _sse(event_type: str, data: dict) -> bytes:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()


@pytest.mark.asyncio
async def test_deltas_from_openai_roundtrip_sse_bytes_to_state_and_deltas():
    """Feed real SSE bytes through sdk_stream so reconstruction is exercised
    end-to-end. Verifies:
      - OpenAIInitEvent session_id lands on _StreamResult.session_id
      - RawResponsesStreamEvent(output_text.delta) yields role+content deltas
      - OpenAIResultEvent usage + error populate _StreamResult
    This is the test that would have caught the 'exception event tears
    down the iterator before ResultEvent' bug if it had existed earlier.
    """
    # Init → text-delta → result (happy path with usage + non-null error)
    payload = (
        _sse("OpenAIInitEvent", {"_type": "OpenAIInitEvent", "session_id": "sess-RT"})
        + _sse(
            "RawResponsesStreamEvent",
            {
                "_type": "RawResponsesStreamEvent",
                # `data` mirrors the shape RawResponsesStreamEvent carries —
                # a Responses streaming event. from_jsonable is called on
                # the whole payload and the nested dict survives as-is
                # (unknown _type would otherwise become a raw dict anyway).
                "data": {
                    "type": "response.output_text.delta",
                    "delta": "hello",
                },
                "type": "raw_response_event",
            },
        )
        + _sse(
            "OpenAIResultEvent",
            {
                "_type": "OpenAIResultEvent",
                "input_tokens": 11,
                "output_tokens": 22,
                "error": None,
            },
        )
    )

    resp = _FakeSSEResp(payload)
    state = _StreamResult()

    deltas = []
    async for delta in _deltas_from_sdk_stream(resp, state):
        deltas.append(delta)

    # Session captured from the init event.
    assert state.session_id == "sess-RT"
    # Usage captured from the terminal result event.
    assert state.usage_dict == {"input_tokens": 11, "output_tokens": 22}
    # Text delta yielded once, prefixed by the role marker.
    assert deltas == [{"role": "assistant"}, {"content": "hello"}]
    # Happy path → no error recorded.
    assert state.assistant_error is None


@pytest.mark.asyncio
async def test_deltas_from_openai_roundtrip_error_path_preserves_result():
    """When the add-on reports an error via OpenAIResultEvent.error (no
    `exception` SSE event), the integration still consumes the terminal
    ResultEvent and records usage + assistant_error. Proves the fix for
    the 'sdk_stream raises and drops ResultEvent' bug."""
    payload = (
        _sse("OpenAIInitEvent", {"_type": "OpenAIInitEvent", "session_id": "s"})
        + _sse(
            "OpenAIResultEvent",
            {
                "_type": "OpenAIResultEvent",
                "input_tokens": 0,
                "output_tokens": 0,
                "error": "openai_auth_failed",
            },
        )
    )

    resp = _FakeSSEResp(payload)
    state = _StreamResult()

    async for _ in _deltas_from_sdk_stream(resp, state):
        pass

    assert state.session_id == "s"
    assert state.assistant_error == "openai_auth_failed"
    assert state.usage_dict == {"input_tokens": 0, "output_tokens": 0}


@pytest.mark.asyncio
async def test_router_recognizes_agent_updated_stream_event():
    """AgentUpdatedStreamEvent must route to the OpenAI path even when it
    is the first event seen (defensive — today the Init event always
    precedes it, but the router should not rely on that ordering)."""
    from agents import Agent, AgentUpdatedStreamEvent

    from custom_components.ha_claude_agent.conversation import _is_openai_event

    evt = AgentUpdatedStreamEvent(new_agent=Agent(name="x"))
    assert _is_openai_event(evt) is True
