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
    the class name. Plain dicts, lists, and primitives pass through
    unchanged. Nested dataclasses (e.g. `AssistantMessage.content[0]` being
    a `TextBlock`) are walked recursively so every level carries its type
    tag.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        result: dict[str, Any] = {"_type": type(obj).__name__}
        for f in dataclasses.fields(obj):
            result[f.name] = to_jsonable(getattr(obj, f.name))
        return result
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    return obj
