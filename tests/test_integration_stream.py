"""Unit tests for the integration's SSE stream helper."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from custom_components.ha_claude_agent.stream import (
    from_jsonable,
    parse_sse_stream,
    reconstruct_exception,
    sdk_stream,
)


class _FakeContent:
    """Minimal aiohttp.ClientResponse.content stand-in for tests."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[bytes]:
        for line in self._lines:
            yield line


class _FakeResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self.content = _FakeContent(lines)


@pytest.mark.asyncio
async def test_parse_sse_stream_yields_single_event():
    resp = _FakeResponse(
        [
            b"event: session\n",
            b'data: {"session_id": "abc"}\n',
            b"\n",
        ]
    )
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("session", {"session_id": "abc"})]


@pytest.mark.asyncio
async def test_parse_sse_stream_yields_multiple_events():
    resp = _FakeResponse(
        [
            b"event: a\n",
            b'data: {"n": 1}\n',
            b"\n",
            b"event: b\n",
            b'data: {"n": 2}\n',
            b"\n",
        ]
    )
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("a", {"n": 1}), ("b", {"n": 2})]


@pytest.mark.asyncio
async def test_parse_sse_stream_ignores_comment_lines():
    resp = _FakeResponse(
        [
            b": this is a comment\n",
            b"event: ping\n",
            b"data: {}\n",
            b"\n",
        ]
    )
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("ping", {})]


@pytest.mark.asyncio
async def test_parse_sse_stream_yields_none_for_malformed_json():
    """Bad JSON payloads surface as (event_type, None) so callers can
    distinguish 'event arrived with bad payload' from 'no event at all'."""
    resp = _FakeResponse(
        [
            b"event: bad\n",
            b"data: not-json\n",
            b"\n",
            b"event: good\n",
            b'data: {"ok": true}\n',
            b"\n",
        ]
    )
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("bad", None), ("good", {"ok": True})]


@pytest.mark.asyncio
async def test_parse_sse_stream_yields_none_for_events_missing_data():
    """An event header with no data line still yields (event_type, None)."""
    resp = _FakeResponse(
        [
            b"event: orphan\n",
            b"\n",
            b"event: good\n",
            b'data: {"ok": true}\n',
            b"\n",
        ]
    )
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("orphan", None), ("good", {"ok": True})]


@pytest.mark.asyncio
async def test_parse_sse_stream_yields_none_for_non_object_json():
    """A `data:` payload that parses to a scalar or list is not a valid
    message envelope and surfaces as None."""
    resp = _FakeResponse(
        [
            b"event: scalar\n",
            b"data: 42\n",
            b"\n",
            b"event: list\n",
            b"data: [1, 2]\n",
            b"\n",
        ]
    )
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("scalar", None), ("list", None)]


@pytest.mark.asyncio
async def test_parse_sse_stream_handles_crlf():
    resp = _FakeResponse(
        [
            b"event: a\r\n",
            b'data: {"n": 1}\r\n',
            b"\r\n",
        ]
    )
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("a", {"n": 1})]


@pytest.mark.asyncio
async def test_parse_sse_stream_flushes_event_without_trailing_blank_line():
    """Stream ending mid-event (no trailing blank) still yields the buffered event."""
    resp = _FakeResponse(
        [
            b"event: truncated\n",
            b'data: {"ok": true}\n',
            # NOTE: no trailing b"\n"
        ]
    )
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("truncated", {"ok": True})]


@pytest.mark.asyncio
async def test_parse_sse_stream_empty_stream_yields_nothing():
    """A stream with zero lines yields nothing and doesn't raise."""
    resp = _FakeResponse([])
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == []


@pytest.mark.asyncio
async def test_parse_sse_stream_concatenates_multiline_data():
    """Multiple data: lines per event should be joined with \\n per SSE spec."""
    resp = _FakeResponse(
        [
            b"event: multiline\n",
            b'data: {"line1":\n',
            b"data: true}\n",
            b"\n",
        ]
    )
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("multiline", {"line1": True})]


def test_from_jsonable_primitives_pass_through():
    assert from_jsonable(42) == 42
    assert from_jsonable("hi") == "hi"
    assert from_jsonable(None) is None
    assert from_jsonable(True) is True


def test_from_jsonable_plain_dict_returns_dict():
    assert from_jsonable({"a": 1, "b": "two"}) == {"a": 1, "b": "two"}


def test_from_jsonable_list_of_primitives():
    assert from_jsonable([1, 2, 3]) == [1, 2, 3]


def test_from_jsonable_reconstructs_text_block():
    from claude_agent_sdk import TextBlock

    result = from_jsonable({"_type": "TextBlock", "text": "hello"})
    assert isinstance(result, TextBlock)
    assert result.text == "hello"


def test_from_jsonable_reconstructs_assistant_message_with_nested_blocks():
    from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock

    payload = {
        "_type": "AssistantMessage",
        "content": [
            {"_type": "TextBlock", "text": "hello"},
            {
                "_type": "ToolUseBlock",
                "id": "tool_1",
                "name": "call_service",
                "input": {"foo": "bar"},
            },
        ],
        "model": "claude-opus-4-6",
        "parent_tool_use_id": None,
        "error": None,
        "usage": None,
        "message_id": None,
        "stop_reason": None,
        "session_id": None,
        "uuid": None,
    }
    result = from_jsonable(payload)

    assert isinstance(result, AssistantMessage)
    assert result.model == "claude-opus-4-6"
    assert len(result.content) == 2
    assert isinstance(result.content[0], TextBlock)
    assert result.content[0].text == "hello"
    assert isinstance(result.content[1], ToolUseBlock)
    assert result.content[1].name == "call_service"
    assert result.content[1].input == {"foo": "bar"}


def test_from_jsonable_reconstructs_stream_event():
    from claude_agent_sdk import StreamEvent

    payload = {
        "_type": "StreamEvent",
        "uuid": "uuid-1",
        "session_id": "session-1",
        "event": {"type": "content_block_delta", "delta": {"text": "hi"}},
        "parent_tool_use_id": None,
    }
    result = from_jsonable(payload)

    assert isinstance(result, StreamEvent)
    assert result.session_id == "session-1"
    assert result.event == {"type": "content_block_delta", "delta": {"text": "hi"}}


def test_from_jsonable_reconstructs_system_message_with_dict_data():
    from claude_agent_sdk import SystemMessage

    payload = {
        "_type": "SystemMessage",
        "subtype": "init",
        "data": {"session_id": "abc", "model": "claude-opus-4-6"},
    }
    result = from_jsonable(payload)

    assert isinstance(result, SystemMessage)
    assert result.subtype == "init"
    assert result.data == {"session_id": "abc", "model": "claude-opus-4-6"}


def test_from_jsonable_unknown_type_returns_raw_dict():
    payload = {"_type": "FutureMessageType", "field": "value"}
    result = from_jsonable(payload)
    # Unknown class → raw dict with _type stripped
    assert result == {"field": "value"}


def test_from_jsonable_tolerates_unknown_fields():
    """If payload has a field the local SDK doesn't know, log and drop it."""
    payload = {
        "_type": "TextBlock",
        "text": "hello",
        "unexpected_future_field": 123,
    }
    from claude_agent_sdk import TextBlock

    result = from_jsonable(payload)
    assert isinstance(result, TextBlock)
    assert result.text == "hello"


def test_from_jsonable_reconstructs_thinking_block():
    """ThinkingBlock has a required `signature` field; must round-trip."""
    from claude_agent_sdk import ThinkingBlock

    payload = {
        "_type": "ThinkingBlock",
        "thinking": "deliberating...",
        "signature": "sig-abc",
    }
    result = from_jsonable(payload)
    assert isinstance(result, ThinkingBlock)
    assert result.thinking == "deliberating..."
    assert result.signature == "sig-abc"


def test_from_jsonable_reconstructs_result_message_with_session_and_cost():
    """ResultMessage is load-bearing: carries session_id, cost, and error
    subtype that conversation.py extracts. Guard against shape regression."""
    from claude_agent_sdk import ResultMessage

    payload = {
        "_type": "ResultMessage",
        "subtype": "success",
        "duration_ms": 1234,
        "duration_api_ms": 1100,
        "is_error": False,
        "num_turns": 3,
        "session_id": "session-42",
        "stop_reason": "end_turn",
        "total_cost_usd": 0.0125,
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "result": "done",
        "structured_output": None,
        "model_usage": None,
        "permission_denials": None,
        "errors": None,
        "uuid": "uuid-1",
    }
    result = from_jsonable(payload)
    assert isinstance(result, ResultMessage)
    assert result.subtype == "success"
    assert result.session_id == "session-42"
    assert result.total_cost_usd == 0.0125
    assert result.num_turns == 3
    assert result.duration_ms == 1234


def test_from_jsonable_reconstructs_rate_limit_event_with_nested_info():
    """RateLimitEvent is the only SDK message type with a nested dataclass
    field (rate_limit_info: RateLimitInfo). Exercises the recursive path."""
    from claude_agent_sdk import RateLimitEvent, RateLimitInfo

    payload = {
        "_type": "RateLimitEvent",
        "rate_limit_info": {
            "_type": "RateLimitInfo",
            "status": "allowed_warning",
            "resets_at": 1700000000,
            "rate_limit_type": "five_hour",
            "utilization": 0.87,
            "overage_status": None,
            "overage_resets_at": None,
            "overage_disabled_reason": None,
            "raw": {"foo": "bar"},
        },
        "uuid": "uuid-rl",
        "session_id": "s1",
    }
    result = from_jsonable(payload)
    assert isinstance(result, RateLimitEvent)
    assert result.session_id == "s1"
    assert isinstance(result.rate_limit_info, RateLimitInfo)
    assert result.rate_limit_info.status == "allowed_warning"
    assert result.rate_limit_info.utilization == 0.87
    assert result.rate_limit_info.rate_limit_type == "five_hour"
    assert result.rate_limit_info.raw == {"foo": "bar"}


def test_from_jsonable_falls_back_when_required_field_missing():
    """If the add-on sends a known class but omits a field that the local
    SDK marks as required (version skew in the other direction), cls(**accepted)
    raises TypeError and we degrade to a raw dict — the match statement in
    conversation.py will fall through to `case _: pass`."""
    # ResultMessage requires subtype, duration_ms, duration_api_ms, is_error,
    # num_turns, session_id — omit num_turns to simulate the skew.
    payload = {
        "_type": "ResultMessage",
        "subtype": "success",
        "duration_ms": 100,
        "duration_api_ms": 90,
        "is_error": False,
        # num_turns missing
        "session_id": "s1",
    }
    result = from_jsonable(payload)
    # Falls back to a raw dict with _type stripped
    assert isinstance(result, dict)
    assert "_type" not in result
    assert result["session_id"] == "s1"


def test_reconstruct_exception_cli_not_found():
    from claude_agent_sdk import CLIConnectionError, CLINotFoundError

    payload = {
        "_type": "CLINotFoundError",
        "module": "claude_agent_sdk._errors",
        "message": "Claude Code not found: /usr/bin/claude",
        "attrs": {},
        "traceback": "Traceback...",
    }
    exc = reconstruct_exception(payload)

    assert isinstance(exc, CLINotFoundError)
    # CLINotFoundError inherits from CLIConnectionError
    assert isinstance(exc, CLIConnectionError)
    assert str(exc) == "Claude Code not found: /usr/bin/claude"


def test_reconstruct_exception_process_error_preserves_attrs():
    from claude_agent_sdk import ProcessError

    payload = {
        "_type": "ProcessError",
        "module": "claude_agent_sdk._errors",
        "message": "process crashed (exit code: 137)",
        "attrs": {"exit_code": 137, "stderr": "OOM killed"},
        "traceback": "Traceback...",
    }
    exc = reconstruct_exception(payload)

    assert isinstance(exc, ProcessError)
    assert exc.exit_code == 137
    assert exc.stderr == "OOM killed"
    assert "exit code: 137" in str(exc)


def test_reconstruct_exception_cli_json_decode_error_bypasses_init():
    """CLIJSONDecodeError.__init__ requires (line, original_error).
    Reconstruction must bypass __init__ to avoid signature mismatches."""
    from claude_agent_sdk import CLIJSONDecodeError

    payload = {
        "_type": "CLIJSONDecodeError",
        "module": "claude_agent_sdk._errors",
        "message": "Failed to decode JSON: bad line...",
        "attrs": {"line": "bad line"},
        "traceback": "Traceback...",
    }
    exc = reconstruct_exception(payload)

    assert isinstance(exc, CLIJSONDecodeError)
    assert exc.line == "bad line"
    assert "Failed to decode JSON" in str(exc)


def test_reconstruct_exception_unknown_class_falls_back_to_sdk_base():
    from claude_agent_sdk import ClaudeSDKError

    payload = {
        "_type": "SomeFutureError",
        "module": "claude_agent_sdk._errors",
        "message": "a future error",
        "attrs": {},
        "traceback": "",
    }
    exc = reconstruct_exception(payload)

    assert isinstance(exc, ClaudeSDKError)
    # The composed message includes the original class name for debuggability
    assert "SomeFutureError" in str(exc)
    assert "a future error" in str(exc)


def test_reconstruct_exception_non_sdk_class_falls_back_to_sdk_base():
    """A ValueError from the add-on's own code should still become a
    ClaudeSDKError so the integration's `except ClaudeSDKError` catches it."""
    from claude_agent_sdk import ClaudeSDKError

    payload = {
        "_type": "ValueError",
        "module": "builtins",
        "message": "bad value",
        "attrs": {},
        "traceback": "",
    }
    exc = reconstruct_exception(payload)

    assert isinstance(exc, ClaudeSDKError)
    assert "ValueError" in str(exc)
    assert "bad value" in str(exc)


@pytest.mark.asyncio
async def test_sdk_stream_yields_reconstructed_messages():
    from claude_agent_sdk import StreamEvent, SystemMessage

    resp = _FakeResponse(
        [
            b"event: SystemMessage\n",
            b'data: {"_type": "SystemMessage", "subtype": "init", "data": {"session_id": "s1"}}\n',
            b"\n",
            b"event: StreamEvent\n",
            b'data: {"_type": "StreamEvent", "uuid": "u1", "session_id": "s1", "event": {"type": "content_block_delta", "delta": {"text": "hi"}}, "parent_tool_use_id": null}\n',
            b"\n",
        ]
    )
    messages = [m async for m in sdk_stream(resp)]

    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].subtype == "init"
    assert messages[0].data == {"session_id": "s1"}
    assert isinstance(messages[1], StreamEvent)
    assert messages[1].session_id == "s1"


@pytest.mark.asyncio
async def test_sdk_stream_raises_sdk_exception_on_exception_event():
    from claude_agent_sdk import CLINotFoundError

    resp = _FakeResponse(
        [
            b"event: exception\n",
            b'data: {"_type": "CLINotFoundError", "module": "claude_agent_sdk._errors", "message": "Claude Code not found", "attrs": {}, "traceback": "..."}\n',
            b"\n",
        ]
    )

    with pytest.raises(CLINotFoundError) as exc_info:
        async for _ in sdk_stream(resp):
            pass
    assert "Claude Code not found" in str(exc_info.value)


@pytest.mark.asyncio
async def test_sdk_stream_raises_process_error_with_attrs():
    from claude_agent_sdk import ProcessError

    resp = _FakeResponse(
        [
            b"event: exception\n",
            b'data: {"_type": "ProcessError", "module": "claude_agent_sdk._errors", "message": "crashed", "attrs": {"exit_code": 137, "stderr": "OOM"}, "traceback": "..."}\n',
            b"\n",
        ]
    )

    with pytest.raises(ProcessError) as exc_info:
        async for _ in sdk_stream(resp):
            pass
    assert exc_info.value.exit_code == 137
    assert exc_info.value.stderr == "OOM"


@pytest.mark.asyncio
async def test_sdk_stream_yields_messages_then_raises_on_trailing_exception():
    """A stream that yields some messages before an exception — consumer
    should receive the messages, then the exception is raised."""
    from claude_agent_sdk import CLIConnectionError, StreamEvent

    resp = _FakeResponse(
        [
            b"event: StreamEvent\n",
            b'data: {"_type": "StreamEvent", "uuid": "u1", "session_id": "s1", "event": {"type": "content_block_delta", "delta": {"text": "partial"}}, "parent_tool_use_id": null}\n',
            b"\n",
            b"event: exception\n",
            b'data: {"_type": "CLIConnectionError", "module": "claude_agent_sdk._errors", "message": "lost connection", "attrs": {}, "traceback": "..."}\n',
            b"\n",
        ]
    )

    seen: list = []
    with pytest.raises(CLIConnectionError):
        async for message in sdk_stream(resp):
            seen.append(message)

    assert len(seen) == 1
    assert isinstance(seen[0], StreamEvent)


@pytest.mark.asyncio
async def test_sdk_stream_raises_on_malformed_exception_event():
    """An `exception` header with an unreadable payload MUST surface as
    an error, not be silently dropped. Otherwise the consumer treats
    the turn as a truncated success and the user sees "I have no response."
    """
    from claude_agent_sdk import ClaudeSDKError

    resp = _FakeResponse(
        [
            b"event: exception\n",
            b"data: not-valid-json\n",
            b"\n",
        ]
    )

    with pytest.raises(ClaudeSDKError, match="Malformed exception event"):
        async for _ in sdk_stream(resp):
            pass


@pytest.mark.asyncio
async def test_sdk_stream_raises_on_exception_event_with_missing_data():
    """Same as above but for an `exception` header with no data line at all."""
    from claude_agent_sdk import ClaudeSDKError

    resp = _FakeResponse(
        [
            b"event: exception\n",
            b"\n",
        ]
    )

    with pytest.raises(ClaudeSDKError, match="Malformed exception event"):
        async for _ in sdk_stream(resp):
            pass


@pytest.mark.asyncio
async def test_sdk_stream_skips_non_exception_events_with_bad_payload():
    """A malformed non-exception event should be skipped so the rest of
    the stream can proceed — only the exception channel is load-bearing."""
    from claude_agent_sdk import StreamEvent

    resp = _FakeResponse(
        [
            b"event: StreamEvent\n",
            b"data: not-json\n",
            b"\n",
            b"event: StreamEvent\n",
            b'data: {"_type": "StreamEvent", "uuid": "u2", "session_id": "s1", '
            b'"event": {"type": "content_block_delta", "delta": {"text": "ok"}}, '
            b'"parent_tool_use_id": null}\n',
            b"\n",
        ]
    )
    messages = [m async for m in sdk_stream(resp)]
    assert len(messages) == 1
    assert isinstance(messages[0], StreamEvent)
    assert messages[0].session_id == "s1"


@pytest.mark.asyncio
async def test_sdk_stream_logs_addon_traceback_before_raising(caplog):
    import logging

    from claude_agent_sdk import CLINotFoundError

    resp = _FakeResponse(
        [
            b"event: exception\n",
            b'data: {"_type": "CLINotFoundError", "module": "claude_agent_sdk._errors", "message": "gone", "attrs": {}, "traceback": "Traceback (most recent call last):\\n  File \\"server.py\\"\\n"}\n',
            b"\n",
        ]
    )

    with (
        caplog.at_level(
            logging.ERROR, logger="custom_components.ha_claude_agent.stream"
        ),
        pytest.raises(CLINotFoundError),
    ):
        async for _ in sdk_stream(resp):
            pass

    # The add-on's traceback string should appear in the logs
    assert any("Traceback" in r.message for r in caplog.records)
