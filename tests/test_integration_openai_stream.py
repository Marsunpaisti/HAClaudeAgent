"""Tests for stream.py class lookup extension for openai-agents types."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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


async def _mock_sdk_stream(items):
    async def gen():
        for item in items:
            yield item

    return gen()


@pytest.mark.asyncio
async def test_deltas_from_openai_populates_session_id_from_init():
    state = _StreamResult()
    items = [
        OpenAIInitEvent(session_id="sess-hello"),
        OpenAIResultEvent(input_tokens=5, output_tokens=7),
    ]
    resp = MagicMock()

    # _deltas_from_openai consumes an already-started async iterator of
    # reconstructed items from sdk_stream. Build a small fake iterator.
    async def fake_sdk_stream(_):
        for i in items:
            yield i

    out = []
    async for delta in _deltas_from_openai(resp, state, sdk_stream=fake_sdk_stream):
        out.append(delta)

    assert state.session_id == "sess-hello"
    assert state.usage_dict == {"input_tokens": 5, "output_tokens": 7}
    # cost_usd stays None — integration sets it to 0.0 downstream when
    # dispatching to sensor, but _StreamResult keeps None to indicate
    # "no Claude ResultMessage was seen."
    assert state.cost_usd is None


@pytest.mark.asyncio
async def test_deltas_from_openai_surfaces_error_from_result():
    state = _StreamResult()
    items = [
        OpenAIInitEvent(session_id="s"),
        OpenAIResultEvent(error="boom"),
    ]
    resp = MagicMock()

    async def fake_sdk_stream(_):
        for i in items:
            yield i

    async for _ in _deltas_from_openai(resp, state, sdk_stream=fake_sdk_stream):
        pass

    assert state.assistant_error == "boom"
