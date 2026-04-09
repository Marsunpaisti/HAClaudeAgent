"""JSON-friendly serialization of SDK dataclasses and exceptions.

The add-on forwards Claude Agent SDK messages to the integration over SSE.
Each SDK message is a `@dataclass` — we walk it recursively, injecting a
`_type` field on every dataclass instance so the integration can reconstruct
the original class on the other side of the wire.
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
    to JSON fall back to their `repr()` form — this preserves debugging
    information for things like `CLIJSONDecodeError.original_error` which
    is itself an Exception.

    The formatted traceback is included as a string so the integration can
    log the add-on-side stack trace when re-raising.
    """
    safe_attrs: dict[str, Any] = {}
    for key, value in vars(err).items():
        if key.startswith("_"):
            continue
        try:
            json.dumps(value)
            safe_attrs[key] = value
        except (TypeError, ValueError):
            safe_attrs[key] = repr(value)

    return {
        "_type": type(err).__name__,
        "module": type(err).__module__,
        "message": str(err),
        "attrs": safe_attrs,
        "traceback": "".join(
            traceback.format_exception(type(err), err, err.__traceback__)
        ),
    }
