"""JSON-friendly serialization of SDK dataclasses and exceptions.

The add-on forwards Claude Agent SDK messages to the integration over SSE.
Each SDK message is a `@dataclass` — we walk it recursively, injecting a
`_type` field on every dataclass instance so the integration can reconstruct
the original class on the other side of the wire.

Wire-protocol contract: the `_type` key name is part of the wire format
shared with ``custom_components/ha_claude_agent/stream.py``'s
``from_jsonable``. Do not rename it on one side without updating the other
— the integration will silently stop reconstructing dataclasses and the
consumer will only see raw dicts.
"""

from __future__ import annotations

import dataclasses
import json
import traceback
from typing import Any


def to_jsonable(obj: Any) -> Any:
    """Recursively convert an object to JSON-serializable form.

    Dataclass instances are emitted as dicts with a `_type` key containing
    the class name. Plain dicts and primitives pass through unchanged.
    Lists and tuples are both serialized as JSON arrays — tuple-ness is
    not preserved (no SDK type uses tuples, so this is acceptable). Nested
    dataclasses (e.g. `AssistantMessage.content[0]` being a `TextBlock`)
    are walked recursively so every level carries its type tag.

    Raises ValueError if a dataclass has a field literally named `_type`,
    which would collide with the type discriminator key.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        result: dict[str, Any] = {"_type": type(obj).__name__}
        for f in dataclasses.fields(obj):
            if f.name == "_type":
                raise ValueError(
                    f"{type(obj).__name__} has a field named '_type', "
                    "which collides with the type discriminator key"
                )
            result[f.name] = to_jsonable(getattr(obj, f.name))
        return result
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    return obj


def exception_to_dict(err: BaseException) -> dict[str, Any]:
    """Capture an exception as a JSON-serializable dict.

    Serializes all public instance attributes (anything in `vars(err)` that
    doesn't start with an underscore). Attributes that can't be serialized
    to valid JSON fall back to their `repr()` form — this preserves
    debugging information for things like `CLIJSONDecodeError.original_error`
    which is itself an Exception. NaN and infinity float values also fall
    back to their `repr()` form because they are not valid JSON.

    The function is designed to be called from inside an exception handler
    and never raises — even if the exception's `vars()` or an attribute's
    `__repr__` itself misbehaves, a placeholder string is substituted.

    Note: attributes stored on `__slots__` (rather than `__dict__`) are
    invisible to this function. None of the SDK exceptions currently use
    `__slots__`, so this is acceptable as a known limitation.

    The formatted traceback is included as a string so the integration can
    log the add-on-side stack trace when re-raising.
    """
    safe_attrs: dict[str, Any] = {}
    try:
        attrs_source = vars(err)
    except Exception:  # noqa: BLE001
        attrs_source = {}

    for key, value in attrs_source.items():
        if key.startswith("_"):
            continue
        try:
            json.dumps(value, allow_nan=False)
            safe_attrs[key] = value
        except (TypeError, ValueError):
            try:
                safe_attrs[key] = repr(value)
            except Exception:  # noqa: BLE001
                safe_attrs[key] = f"<unrepresentable {type(value).__name__}>"

    return {
        "_type": type(err).__name__,
        "module": type(err).__module__,
        "message": str(err),
        "attrs": safe_attrs,
        "traceback": "".join(
            traceback.format_exception(type(err), err, err.__traceback__)
        ),
    }
