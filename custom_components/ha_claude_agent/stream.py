"""SSE stream helper for consuming the add-on's /query endpoint.

Parses SSE events emitted by the add-on and reconstructs them into real
SDK dataclass instances and real SDK exception classes, so that
`conversation.py` consumer code reads exactly like direct-SDK usage.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any, Protocol

import claude_agent_sdk
from pydantic import BaseModel as _PydanticBaseModel

from . import openai_events as _openai_events_module

try:
    import agents as _agents_module
except ImportError:
    _agents_module = None

_LOGGER = logging.getLogger(__name__)


class SSEResponse(Protocol):
    """Minimal duck-typed interface for the aiohttp response we consume."""

    content: AsyncIterable[bytes]


def _decode_event_data(event_type: str, data_line: str) -> dict[str, Any] | None:
    """Decode an SSE `data:` payload; return None if malformed."""
    try:
        data = json.loads(data_line)
    except json.JSONDecodeError:
        _LOGGER.warning(
            "Bad SSE data payload for event %s: %r",
            event_type,
            data_line,
        )
        return None
    if not isinstance(data, dict):
        _LOGGER.warning(
            "SSE data payload for event %s is not a JSON object: %r",
            event_type,
            data,
        )
        return None
    return data


async def parse_sse_stream(
    resp: SSEResponse,
) -> AsyncIterator[tuple[str, dict[str, Any] | None]]:
    """Parse an SSE stream into (event_type, data_dict_or_none) tuples.

    Yields one tuple per completed event whose `event:` header was seen.
    If the accompanying `data:` payload is missing, unparseable, or not a
    JSON object, the second element is ``None``; callers can distinguish
    "event arrived but payload was bad" from "event never arrived." This
    matters for the exception channel: an ``exception`` event with a
    broken payload must still surface as an error, not be silently dropped.
    """
    event_type: str | None = None
    data_line: str | None = None

    async for raw_line in resp.content:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if line == "":
            if event_type is not None:
                data = (
                    _decode_event_data(event_type, data_line)
                    if data_line is not None
                    else None
                )
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
    if event_type is not None:
        data = (
            _decode_event_data(event_type, data_line) if data_line is not None else None
        )
        yield event_type, data


def _resolve_wire_class(cls_name: str) -> type | None:
    """Find the class `cls_name` across the known wire-format namespaces.

    Lookup order:
      1. claude_agent_sdk (Claude backend dataclasses)
      2. agents (openai-agents public API: stream events, run items, ...)
      3. openai_events (this project's own Pydantic init/result events)

    Returns None if the name is not found in any namespace, or if found but
    not a reconstructable dataclass/pydantic model — caller falls back to
    returning a raw dict.
    """
    for module in (
        claude_agent_sdk,
        _agents_module,
        _openai_events_module,
    ):
        if module is None:
            continue
        cls = getattr(module, cls_name, None)
        if cls is None:
            continue
        if isinstance(cls, type) and (
            dataclasses.is_dataclass(cls)
            or issubclass(cls, _PydanticBaseModel)
        ):
            return cls
    return None


def from_jsonable(obj: Any) -> Any:
    """Reconstruct SDK dataclass instances from a JSON-friendly payload.

    This is the inverse of the add-on's `to_jsonable()` walker in
    ``ha_claude_agent_addon/src/serialization.py``. Dicts with a ``_type``
    key are looked up as classes in ``claude_agent_sdk`` and instantiated
    recursively. Unknown classes fall back to a plain dict with the
    ``_type`` key stripped. Unknown fields on a known class are dropped
    with a debug log, so minor SDK version drift between the add-on and
    the integration is tolerated.

    Wire-protocol contract: the ``_type`` key name is part of the wire
    format shared with the add-on's ``to_jsonable``. Both sides must agree
    — changing it on only one side will silently stop reconstructing
    dataclasses.
    """
    if isinstance(obj, dict):
        if "_type" in obj:
            cls_name = obj["_type"]
            fields_payload = {
                k: from_jsonable(v) for k, v in obj.items() if k != "_type"
            }
            # Security: class lookup is intentionally narrowed to dataclasses
            # exported from known safe namespaces (claude_agent_sdk, agents,
            # openai_events). The is_dataclass / issubclass(_PydanticBaseModel)
            # guard rejects non-model exports (e.g. `sys`, `Any`, `TypeVar`)
            # that may leak into those namespaces, and the module-scoped lookup
            # keeps a compromised or buggy add-on response from tricking us into
            # instantiating arbitrary classes.
            cls = _resolve_wire_class(cls_name)
            if cls is None:
                _LOGGER.debug(
                    "Unknown class %r in stream payload; returning raw dict",
                    cls_name,
                )
                return fields_payload

            if issubclass(cls, _PydanticBaseModel):
                try:
                    return cls.model_validate(fields_payload)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning(
                        "Failed to reconstruct pydantic %s: %s; raw dict",
                        cls_name,
                        err,
                    )
                    return fields_payload

            # Dataclass path (existing behavior).
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

    The wire payload carries a ``module`` field (e.g.
    ``claude_agent_sdk._errors``) captured from ``type(err).__module__``
    on the add-on side, but it is deliberately ignored here: lookup is
    scoped to the public ``claude_agent_sdk`` namespace by class name
    alone, which is the stable identity we share with callers. Honoring
    the raw module path would widen the instantiation surface to
    internal SDK submodules and make version skew harder to reason
    about, so the field is retained in the wire format only for debug
    logging.

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


async def sdk_stream(resp: SSEResponse) -> AsyncIterator[Any]:
    """Consume the add-on's SSE stream, yielding SDK message instances.

    For each `Message`-typed event, yields the reconstructed SDK dataclass
    instance (AssistantMessage, StreamEvent, SystemMessage, ResultMessage,
    UserMessage, RateLimitEvent, etc.). For an `exception` event, logs the
    add-on-side traceback and raises the reconstructed SDK exception.

    If the add-on emits a `_type` that isn't a known SDK class, the raw
    dict is yielded; the consumer's `match` statement will naturally fall
    through its `case _:` arm without crashing.

    Malformed events (bad JSON in the payload) are handled explicitly:
    non-exception events with an unreadable payload are skipped with a
    warning, but a malformed ``exception`` event raises
    ``ClaudeSDKError`` so the consumer sees an error rather than a
    silent truncated success.
    """
    async for event_type, data in parse_sse_stream(resp):
        if event_type == "exception":
            if data is None:
                # The add-on emitted an `exception` header but the payload
                # was unreadable. Surface as a generic SDK error rather
                # than terminating the stream silently — the consumer
                # must not mistake this for a successful turn.
                _LOGGER.error(
                    "Add-on emitted a malformed exception event; "
                    "surfacing as ClaudeSDKError"
                )
                raise claude_agent_sdk.ClaudeSDKError(
                    "Malformed exception event from add-on"
                )
            traceback_text = data.get("traceback", "")
            if traceback_text:
                _LOGGER.error(
                    "Add-on raised %s. Add-on-side traceback:\n%s",
                    data.get("_type", "unknown"),
                    traceback_text,
                )
            raise reconstruct_exception(data)

        if data is None:
            # Non-exception event with a bad payload — already logged
            # by the parser. Skip so the rest of the stream can proceed.
            continue

        yield from_jsonable(data)
