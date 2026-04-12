"""Unit tests for the streaming filter pipeline."""

from __future__ import annotations

from custom_components.ha_claude_agent.stream_filters import (
    LineBufferedFilter,
    SourcesFilter,
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


# ---------------------------------------------------------------------------
# TestSourcesFilter
# ---------------------------------------------------------------------------


class TestSourcesFilter:
    """Tests for the markdown sources-section stripper."""

    # --- Pass-through (no sources) ---

    def test_normal_text_unchanged(self):
        f = SourcesFilter()
        assert f.feed("Hello world\n") == "Hello world\n"
        assert f.flush() == ""

    def test_multiline_normal_text(self):
        f = SourcesFilter()
        text = "Line one\nLine two\nLine three\n"
        assert f.feed(text) == text
        assert f.flush() == ""

    # --- Basic source removal ---

    def test_sources_at_end(self):
        f = SourcesFilter()
        out = f.feed("Answer text\n# Sources\n[Google](https://google.com)\n")
        out += f.flush()
        assert out == "Answer text\n"

    def test_references_header(self):
        f = SourcesFilter()
        out = f.feed("Text\n## References\n[Link](https://example.com)\n")
        out += f.flush()
        assert out == "Text\n"

    def test_citations_header(self):
        f = SourcesFilter()
        out = f.feed("Text\n### Citations\nhttps://example.com\n")
        out += f.flush()
        assert out == "Text\n"

    def test_bold_header(self):
        f = SourcesFilter()
        out = f.feed("Text\n**Sources**\n[Link](https://example.com)\n")
        out += f.flush()
        assert out == "Text\n"

    def test_bold_header_with_colon(self):
        f = SourcesFilter()
        out = f.feed("Text\n**References:**\nhttps://example.com\n")
        out += f.flush()
        assert out == "Text\n"

    def test_header_with_colon(self):
        f = SourcesFilter()
        out = f.feed("Text\n## Sources:\n[L](https://x.com)\n")
        out += f.flush()
        assert out == "Text\n"

    # --- Multilingual ---

    def test_finnish_lahteet(self):
        f = SourcesFilter()
        out = f.feed("Vastaus\n# Lähteet\n[S](https://s.fi)\n")
        out += f.flush()
        assert out == "Vastaus\n"

    def test_finnish_viitteet(self):
        f = SourcesFilter()
        out = f.feed("Teksti\n## Viitteet\nhttps://example.fi\n")
        out += f.flush()
        assert out == "Teksti\n"

    # --- False alarm ---

    def test_header_followed_by_normal_text(self):
        """Header then non-source line = false alarm; both emitted."""
        f = SourcesFilter()
        out = f.feed("# Sources\nThis is not a link\n")
        out += f.flush()
        assert out == "# Sources\nThis is not a link\n"

    def test_header_at_end_of_stream_is_false_alarm(self):
        """Header with nothing after it at flush = false alarm; emitted."""
        f = SourcesFilter()
        out = f.feed("# Sources\n")
        out += f.flush()
        assert out == "# Sources\n"

    def test_header_at_end_no_newline(self):
        """Trailing partial header at flush = false alarm; emitted."""
        f = SourcesFilter()
        out = f.feed("# Sources")
        out += f.flush()
        assert out == "# Sources"

    # --- Sources followed by more content ---

    def test_sources_mid_text(self):
        f = SourcesFilter()
        text = "Intro\n# Sources\n[A](https://a.com)\n[B](https://b.com)\nConclusion\n"
        out = f.feed(text) + f.flush()
        assert out == "Intro\nConclusion\n"

    # --- Source-line variants ---

    def test_bare_urls(self):
        f = SourcesFilter()
        out = f.feed("Text\n# Sources\nhttps://a.com\nhttps://b.com\n") + f.flush()
        assert out == "Text\n"

    def test_list_marker_dash(self):
        f = SourcesFilter()
        out = (
            f.feed("Text\n# Sources\n- [A](https://a.com)\n- [B](https://b.com)\n")
            + f.flush()
        )
        assert out == "Text\n"

    def test_list_marker_asterisk(self):
        f = SourcesFilter()
        out = f.feed("Text\n# Sources\n* [A](https://a.com)\n") + f.flush()
        assert out == "Text\n"

    def test_list_marker_numbered(self):
        f = SourcesFilter()
        out = (
            f.feed("Text\n# Sources\n1. [A](https://a.com)\n2. [B](https://b.com)\n")
            + f.flush()
        )
        assert out == "Text\n"

    def test_list_marker_bare_url(self):
        f = SourcesFilter()
        out = f.feed("Text\n# Sources\n- https://a.com\n") + f.flush()
        assert out == "Text\n"

    def test_empty_lines_in_sources(self):
        f = SourcesFilter()
        out = f.feed("Text\n# Sources\n\n[A](https://a.com)\n\n") + f.flush()
        assert out == "Text\n"

    # --- Sources to end of stream ---

    def test_sources_to_end_discarded(self):
        f = SourcesFilter()
        out = f.feed("Intro\n# Sources\n[A](https://a.com)\n")
        out += f.flush()
        assert out == "Intro\n"

    # --- Chunk boundary tests ---

    def test_header_split_across_chunks(self):
        f = SourcesFilter()
        out = f.feed("Text\n# Sou")
        out += f.feed("rces\n[A](https://a.com)\n")
        out += f.flush()
        assert out == "Text\n"

    def test_source_line_split_across_chunks(self):
        f = SourcesFilter()
        out = f.feed("Text\n# Sources\n[A](htt")
        out += f.feed("ps://a.com)\n")
        out += f.flush()
        assert out == "Text\n"

    def test_header_and_link_in_separate_chunks(self):
        f = SourcesFilter()
        out = f.feed("Text\n")
        out += f.feed("# Sources\n")
        out += f.feed("[A](https://a.com)\n")
        out += f.flush()
        assert out == "Text\n"

    # --- Case insensitivity ---

    def test_case_insensitive_header(self):
        f = SourcesFilter()
        out = f.feed("Text\n# SOURCES\n[A](https://a.com)\n") + f.flush()
        assert out == "Text\n"

    def test_case_insensitive_mixed(self):
        f = SourcesFilter()
        out = f.feed("Text\n## ReFeReNcEs\nhttps://x.com\n") + f.flush()
        assert out == "Text\n"

    # --- Consecutive source headers ---

    def test_header_then_header(self):
        """# Sources immediately followed by # References — both held as
        source content, not two independent false alarms."""
        f = SourcesFilter()
        out = f.feed("Text\n# Sources\n# References\n[A](https://a.com)\n") + f.flush()
        assert out == "Text\n"

    # --- Integration with StreamingFilterProcessor ---

    def test_sources_through_processor(self):
        proc = StreamingFilterProcessor([SourcesFilter()])
        out = proc.feed("Text\n# Sources\n[A](https://a.com)\n")
        out += proc.flush()
        assert out == "Text\n"
