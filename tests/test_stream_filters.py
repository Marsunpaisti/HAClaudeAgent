"""Unit tests for the streaming filter pipeline."""

from __future__ import annotations

from custom_components.ha_claude_agent.stream_filters import (
    LineBufferedFilter,
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


# ---------------------------------------------------------------------------
# LineBufferedFilter helpers
# ---------------------------------------------------------------------------

class _EchoLineFilter(LineBufferedFilter):
    """Passes all lines through unchanged — exercises the base class."""

    def _process_line(self, line: str) -> str | None:
        return line


class _DropLineFilter(LineBufferedFilter):
    """Drops lines containing a keyword, emits the rest."""

    def __init__(self, keyword: str) -> None:
        super().__init__()
        self._keyword = keyword

    def _process_line(self, line: str) -> str | None:
        if self._keyword in line:
            return None
        return line


# ---------------------------------------------------------------------------
# TestLineBufferedFilter
# ---------------------------------------------------------------------------

class TestLineBufferedFilter:
    """Tests for the line-assembly base class."""

    def test_full_lines_emitted_immediately(self):
        f = _EchoLineFilter()
        assert f.feed("hello\nworld\n") == "hello\nworld\n"

    def test_partial_line_buffered(self):
        f = _EchoLineFilter()
        assert f.feed("hel") == ""
        assert f.feed("lo\n") == "hello\n"

    def test_accumulation_across_chunks(self):
        f = _EchoLineFilter()
        assert f.feed("hel") == ""
        assert f.feed("lo\nwo") == "hello\n"
        assert f.feed("rld\n") == "world\n"

    def test_trailing_partial_flushed(self):
        f = _EchoLineFilter()
        assert f.feed("no newline") == ""
        assert f.flush() == "no newline"

    def test_flush_empty_buffer(self):
        f = _EchoLineFilter()
        assert f.feed("line\n") == "line\n"
        assert f.flush() == ""

    def test_process_line_none_holds(self):
        f = _DropLineFilter("secret")
        assert f.feed("keep\nsecret line\nalso keep\n") == "keep\nalso keep\n"

    def test_finalize_called_at_flush(self):
        """_finalize output appears in flush."""

        class _FinalizeFilter(LineBufferedFilter):
            def _process_line(self, line: str) -> str | None:
                return line

            def _finalize(self) -> str:
                return "[END]"

        f = _FinalizeFilter()
        f.feed("text\n")
        assert f.flush() == "[END]"

    def test_trailing_partial_plus_finalize(self):
        class _FinalizeFilter(LineBufferedFilter):
            def _process_line(self, line: str) -> str | None:
                return line

            def _finalize(self) -> str:
                return "!"

        f = _FinalizeFilter()
        assert f.feed("tail") == ""
        assert f.flush() == "tail!"
