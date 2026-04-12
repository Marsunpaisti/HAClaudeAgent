"""Unit tests for the streaming filter pipeline."""

from __future__ import annotations

from custom_components.ha_claude_agent.stream_filters import (
    StreamFilter,
    StreamingFilterProcessor,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class _PassThroughFilter(StreamFilter):
    """Returns input unchanged."""

    def feed(self, text: str) -> str:
        return text

    def flush(self) -> str:
        return ""


class _UpperFilter(StreamFilter):
    """Uppercases everything — simplest transformation filter."""

    def feed(self, text: str) -> str:
        return text.upper()

    def flush(self) -> str:
        return ""


class _HoldFilter(StreamFilter):
    """Buffers all input, releases on flush."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> str:
        self._buf += text
        return ""

    def flush(self) -> str:
        out = self._buf
        self._buf = ""
        return out


# ---------------------------------------------------------------------------
# StreamingFilterProcessor
# ---------------------------------------------------------------------------

class TestStreamingFilterProcessor:
    """Tests for the filter pipeline orchestrator."""

    def test_empty_pipeline_passes_through(self):
        proc = StreamingFilterProcessor([])
        assert proc.feed("hello") == "hello"
        assert proc.flush() == ""

    def test_single_passthrough(self):
        proc = StreamingFilterProcessor([_PassThroughFilter()])
        assert proc.feed("hello") == "hello"
        assert proc.flush() == ""

    def test_single_transform(self):
        proc = StreamingFilterProcessor([_UpperFilter()])
        assert proc.feed("hello") == "HELLO"
        assert proc.flush() == ""

    def test_two_filters_compose(self):
        proc = StreamingFilterProcessor([_UpperFilter(), _PassThroughFilter()])
        assert proc.feed("abc") == "ABC"

    def test_hold_filter_buffers_then_flushes(self):
        proc = StreamingFilterProcessor([_HoldFilter()])
        assert proc.feed("a") == ""
        assert proc.feed("b") == ""
        assert proc.flush() == "ab"

    def test_flush_pipes_through_downstream(self):
        """Content flushed from F1 must pass through F2's feed() before
        F2 itself flushes."""
        proc = StreamingFilterProcessor([_HoldFilter(), _UpperFilter()])
        assert proc.feed("hello") == ""
        # flush: HoldFilter releases "hello", UpperFilter.feed("hello") -> "HELLO"
        assert proc.flush() == "HELLO"

    def test_flush_order_two_hold_filters(self):
        """Both filters hold; flush must chain correctly."""
        proc = StreamingFilterProcessor([_HoldFilter(), _HoldFilter()])
        proc.feed("x")
        proc.feed("y")
        # flush: F1 releases "xy" → F2.feed("xy")="" (F2 holds) → F2.flush()="xy"
        assert proc.flush() == "xy"

    def test_no_duplicate_output(self):
        """Feed + flush should not produce duplicated content."""
        proc = StreamingFilterProcessor([_PassThroughFilter()])
        out = proc.feed("data")
        out += proc.flush()
        assert out == "data"
