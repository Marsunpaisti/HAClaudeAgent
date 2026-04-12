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
