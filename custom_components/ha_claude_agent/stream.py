"""SSE stream helper for consuming the add-on's /query endpoint.

Parses SSE events emitted by the add-on and reconstructs them into real
SDK dataclass instances and real SDK exception classes, so that
`conversation.py` consumer code reads exactly like direct-SDK usage.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any, Protocol

import claude_agent_sdk

_LOGGER = logging.getLogger(__name__)


class _SSEResponse(Protocol):
    """Minimal duck-typed interface for the aiohttp response we consume."""

    content: AsyncIterable[bytes]


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
            event_type = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            chunk = line.removeprefix("data:").strip()
            data_line = chunk if data_line is None else f"{data_line}\n{chunk}"
        # Lines starting with `:` (comments) or anything else are ignored.

    # Flush any partial event buffered when the stream ends without a
    # trailing blank line — e.g. when the add-on closes the connection
    # mid-event after an abrupt failure.
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


def from_jsonable(obj: Any) -> Any:
    """Reconstruct SDK dataclass instances from a JSON-friendly payload.

    This is the inverse of the add-on's `to_jsonable()` walker. Dicts with
    a `_type` key are looked up as classes in `claude_agent_sdk` and
    instantiated recursively. Unknown classes fall back to a plain dict
    with the `_type` key stripped. Unknown fields on a known class are
    dropped with a debug log, so minor SDK version drift between the
    add-on and the integration is tolerated.
    """
    if isinstance(obj, dict):
        if "_type" in obj:
            cls_name = obj["_type"]
            fields_payload = {
                k: from_jsonable(v) for k, v in obj.items() if k != "_type"
            }
            cls = getattr(claude_agent_sdk, cls_name, None)
            if cls is None or not (
                isinstance(cls, type) and dataclasses.is_dataclass(cls)
            ):
                _LOGGER.debug(
                    "Unknown SDK class %r in stream payload; returning raw dict",
                    cls_name,
                )
                return fields_payload

            known_field_names = {f.name for f in dataclasses.fields(cls)}
            accepted = {
                k: v for k, v in fields_payload.items() if k in known_field_names
            }
            dropped = set(fields_payload) - known_field_names
            if dropped:
                _LOGGER.debug(
                    "Dropping unknown fields %s from %s payload (SDK version skew?)",
                    dropped,
                    cls_name,
                )
            try:
                return cls(**accepted)
            except TypeError as err:
                _LOGGER.warning(
                    "Failed to reconstruct %s: %s; returning raw dict",
                    cls_name,
                    err,
                )
                return fields_payload
        return {k: from_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [from_jsonable(x) for x in obj]
    return obj


def reconstruct_exception(payload: dict[str, Any]) -> BaseException:
    """Reconstruct an SDK exception from a forwarded payload.

    Looks up the class by name in `claude_agent_sdk`. For classes with
    non-standard `__init__` signatures (e.g. `CLIJSONDecodeError`),
    bypasses `__init__` via `cls.__new__(cls)` and directly sets the
    message via `Exception.__init__`, then restores captured instance
    attributes via `setattr`.

    Unknown class names or non-exception classes fall back to a plain
    `ClaudeSDKError` with a composed message, so the integration's
    `except ClaudeSDKError` handler always catches the result.

    Known limitation: `MessageParseError` is defined in
    `claude_agent_sdk._errors` but not exported from the package's
    top-level namespace in SDK 0.1.56. Any forwarded `MessageParseError`
    silently degrades to `ClaudeSDKError` here. The integration's
    generic `ClaudeSDKError` handler still catches it.
    """
    cls_name = payload.get("_type", "")
    message = payload.get("message", "")
    attrs = payload.get("attrs")
    if not isinstance(attrs, dict):
        attrs = {}

    cls = getattr(claude_agent_sdk, cls_name, None)
    if not (isinstance(cls, type) and issubclass(cls, claude_agent_sdk.ClaudeSDKError)):
        composed = f"{cls_name}: {message}" if cls_name else message
        return claude_agent_sdk.ClaudeSDKError(composed)

    exc = cls.__new__(cls)
    # Call Exception.__init__ directly (not cls.__init__) because SDK exception
    # subclasses have varying constructor signatures (e.g. CLIJSONDecodeError
    # requires `line, original_error`). Setting args=(message,) via Exception's
    # base __init__ gives us a valid exception whose str() returns the message.
    Exception.__init__(exc, message)
    for key, value in attrs.items():
        try:
            setattr(exc, key, value)
        except (AttributeError, TypeError):
            _LOGGER.debug(
                "Could not restore attr %s=%r on reconstructed %s",
                key,
                value,
                cls_name,
            )
    return exc
