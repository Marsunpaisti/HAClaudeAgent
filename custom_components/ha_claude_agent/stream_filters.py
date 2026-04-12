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
