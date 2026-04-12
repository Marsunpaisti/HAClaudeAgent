"""Composable streaming text filters.

Filters are stateful transducers: they receive text chunks via ``feed()``
and return whatever is safe to emit immediately.  Uncertain content is
held internally until the filter can decide (emit or discard).  At end
of stream, ``flush()`` releases any remaining content per the filter's
end-of-stream policy.

Filters are chained by :class:`StreamingFilterProcessor` — the output
of one becomes the input of the next.
"""

from __future__ import annotations

import enum
import re
from abc import ABC, abstractmethod


class StreamFilter(ABC):
    """A stateful text transducer that may buffer uncertain content."""

    @abstractmethod
    def feed(self, text: str) -> str:
        """Feed a chunk of text; return whatever is safe to emit now.

        May return an empty string if the filter is currently holding
        uncertain content.  May return more text than was fed if the
        filter releases previously held content.
        """

    @abstractmethod
    def flush(self) -> str:
        """End-of-stream.  Return any remaining content per the filter's
        policy (emit, discard, or partial release).
        """


class StreamingFilterProcessor:
    """Composes multiple StreamFilters into a sequential pipeline."""

    def __init__(self, filters: list[StreamFilter]) -> None:
        self._filters = filters

    def feed(self, text: str) -> str:
        for f in self._filters:
            text = f.feed(text)
        return text

    def flush(self) -> str:
        """Flush all filters in order.

        When filter *N* flushes its held content, that content still
        needs to pass through filters *N+1*, *N+2*, etc.  The loop
        feeds each filter's flushed output into the next filter's
        ``feed()`` before that next filter itself flushes.
        """
        buf = ""
        for f in self._filters:
            buf = f.feed(buf) + f.flush()
        return buf


class LineBufferedFilter(StreamFilter):
    """Base for line-oriented filters.

    Accumulates text until newlines are found, then calls
    :meth:`_process_line` for each complete line.  Subclasses implement
    ``_process_line`` and optionally ``_finalize`` for end-of-stream.
    """

    def __init__(self) -> None:
        self._line_buffer = ""

    def feed(self, text: str) -> str:
        self._line_buffer += text
        output: list[str] = []
        while (nl := self._line_buffer.find("\n")) != -1:
            line = self._line_buffer[:nl]
            self._line_buffer = self._line_buffer[nl + 1 :]
            result = self._process_line(line)
            if result is not None:
                output.append(result + "\n")
        return "".join(output)

    def flush(self) -> str:
        output: list[str] = []
        if self._line_buffer:
            result = self._process_line(self._line_buffer)
            if result is not None:
                output.append(result)
            self._line_buffer = ""
        final = self._finalize()
        if final:
            output.append(final)
        return "".join(output)

    @abstractmethod
    def _process_line(self, line: str) -> str | None:
        """Process one line (without trailing newline).

        Return the line to emit it, or ``None`` to suppress it.
        The base class appends ``\\n`` to emitted lines automatically
        (except for trailing partial lines at flush).
        """

    def _finalize(self) -> str:
        """Called at end of stream after the trailing partial line (if any)
        has been processed.  Override to release held state.
        """
        return ""


class SourcesFilter(LineBufferedFilter):
    """Detects and removes markdown 'Sources' sections from streaming text.

    Uses a 3-state machine:

    - **NORMAL** — default; emits lines, watches for source headers.
    - **POSSIBLE_HEADER** — saw a header; holds it while waiting for
      confirmation (a source-line) or rejection (any other line).
    - **IN_SOURCES** — confirmed sources section; holds (and ultimately
      discards) source lines until a non-source line appears.
    """

    _SOURCE_KEYWORDS = ("sources", "references", "citations", "lähteet", "viitteet")

    _HEADER_PATTERN = re.compile(
        r"^\s*(?:#{1,3}\s+)?(?:\*\*)?(?:"
        + "|".join(re.escape(kw) for kw in _SOURCE_KEYWORDS)
        + r")\s*:?\s*(?:\*\*)?\s*$",
        re.IGNORECASE,
    )

    _SOURCE_LINE_PATTERN = re.compile(
        r"^\s*$"
        r"|^\s*(?:(?:[-*]|\d+\.)\s+)?(?:\[.*?\]\(https?://[^)]*\)|https?://\S+)\s*$",
        re.IGNORECASE,
    )

    class _State(enum.Enum):
        NORMAL = "normal"
        POSSIBLE_HEADER = "possible_header"
        IN_SOURCES = "in_sources"

    def __init__(self) -> None:
        super().__init__()
        self._state = self._State.NORMAL
        self._held: list[str] = []
        self._last_held_is_partial = False

    def flush(self) -> str:
        # Signal that any line processed during the base flush() is partial
        # (no trailing newline), so _finalize can reconstruct it correctly.
        self._last_held_is_partial = bool(self._line_buffer)
        return super().flush()

    def _is_header(self, line: str) -> bool:
        return bool(self._HEADER_PATTERN.match(line))

    def _is_source_line(self, line: str) -> bool:
        return self._is_header(line) or bool(self._SOURCE_LINE_PATTERN.match(line))

    def _process_line(self, line: str) -> str | None:
        match self._state:
            case self._State.NORMAL:
                if self._is_header(line):
                    self._held.append(line)
                    self._state = self._State.POSSIBLE_HEADER
                    return None
                return line

            case self._State.POSSIBLE_HEADER:
                if self._is_source_line(line):
                    self._held.append(line)
                    self._state = self._State.IN_SOURCES
                    return None
                # False alarm — release held header + emit this line
                held = self._held
                self._held = []
                self._state = self._State.NORMAL
                return "\n".join(held) + "\n" + line

            case self._State.IN_SOURCES:
                if self._is_source_line(line):
                    self._held.append(line)
                    return None
                # End of sources section — discard held, emit this line
                self._held.clear()
                self._state = self._State.NORMAL
                return line

        return line  # pragma: no cover

    def _finalize(self) -> str:
        match self._state:
            case self._State.POSSIBLE_HEADER:
                # End of stream with unconfirmed header = false alarm.
                # All held lines were full lines except possibly the last,
                # which may be a partial (no trailing newline) if it arrived
                # from flush() rather than feed().
                parts: list[str] = []
                for i, line in enumerate(self._held):
                    is_last = i == len(self._held) - 1
                    if is_last and self._last_held_is_partial:
                        parts.append(line)
                    else:
                        parts.append(line + "\n")
                self._held = []
                self._last_held_is_partial = False
                self._state = self._State.NORMAL
                return "".join(parts)
            case self._State.IN_SOURCES:
                # Confirmed sources section — discard
                self._held.clear()
                self._last_held_is_partial = False
                self._state = self._State.NORMAL
        return ""
