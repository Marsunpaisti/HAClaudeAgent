"""JSON-friendly serialization of SDK dataclasses and exceptions.

The add-on forwards Claude Agent SDK messages to the integration over SSE.
Each SDK message is a `@dataclass` — we walk it recursively, injecting a
`_type` field on every dataclass instance so the integration can reconstruct
the original class on the other side of the wire.
"""

from __future__ import annotations

import dataclasses
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
