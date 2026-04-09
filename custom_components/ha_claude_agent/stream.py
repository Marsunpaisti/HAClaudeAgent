"""SSE stream helper for consuming the add-on's /query endpoint.

Parses SSE events emitted by the add-on and reconstructs them into real
SDK dataclass instances and real SDK exception classes, so that
`conversation.py` consumer code reads exactly like direct-SDK usage.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any, Protocol

_LOGGER = logging.getLogger(__name__)


class _SSEResponse(Protocol):
    """Minimal duck-typed interface for the aiohttp response we consume."""

    content: Any


async def parse_sse_stream(
    resp: _SSEResponse,
) -> AsyncGenerator[tuple[str, dict[str, Any]]]:
    """Parse an SSE stream into (event_type, data_dict) tuples.

    Yields one tuple per completed event. Events without both a valid
    `event:` line and a parseable `data:` line are silently skipped.
    """
    event_type: str | None = None
    data_line: str | None = None

    async for raw_line in resp.content:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if line == "":
            if event_type is not None and data_line is not None:
                try:
                    data = json.loads(data_line)
                except json.JSONDecodeError:
                    _LOGGER.warning(
                        "Bad SSE data payload for event %s: %r",
                        event_type,
                        data_line,
                    )
                else:
                    if isinstance(data, dict):
                        yield event_type, data
            event_type = None
            data_line = None
            continue
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_line = line[5:].strip()
        # Lines starting with `:` (comments) or anything else are ignored.
