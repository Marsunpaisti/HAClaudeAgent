"""Unit tests for the integration's SSE stream helper."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from custom_components.ha_claude_agent.stream import parse_sse_stream


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
    resp = _FakeResponse([
        b"event: truncated\n",
        b'data: {"ok": true}\n',
        # NOTE: no trailing b"\n"
    ])
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
    resp = _FakeResponse([
        b"event: multiline\n",
        b'data: {"line1":\n',
        b'data: true}\n',
        b"\n",
    ])
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("multiline", {"line1": True})]
