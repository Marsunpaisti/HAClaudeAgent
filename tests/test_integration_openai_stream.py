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
