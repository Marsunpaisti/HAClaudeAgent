"""Unit tests for the integration's SSE stream helper."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from custom_components.ha_claude_agent.stream import (
    from_jsonable,
    parse_sse_stream,
    reconstruct_exception,
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
async def test_parse_sse_stream_skips_malformed_json():
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
    assert events == [("good", {"ok": True})]


@pytest.mark.asyncio
async def test_parse_sse_stream_skips_events_missing_data():
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
    assert events == [("good", {"ok": True})]


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
