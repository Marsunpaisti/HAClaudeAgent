"""Round-trip tests for Pydantic model serialization."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from serialization import to_jsonable  # noqa: E402


class _MyEvent(BaseModel):
    session_id: str
    tokens: int = 0


class _Nested(BaseModel):
    inner: _MyEvent
    note: str


def test_pydantic_model_gets_type_tag():
    obj = _MyEvent(session_id="abc-123", tokens=42)
    result = to_jsonable(obj)
    assert result == {"_type": "_MyEvent", "session_id": "abc-123", "tokens": 42}


def test_pydantic_model_is_json_serializable():
    obj = _MyEvent(session_id="abc-123")
    result = to_jsonable(obj)
    # Must not raise
    json.dumps(result)


def test_nested_pydantic_models_are_recursively_tagged():
    obj = _Nested(inner=_MyEvent(session_id="s1", tokens=1), note="hello")
    result = to_jsonable(obj)
    assert result == {
        "_type": "_Nested",
        "inner": {"_type": "_MyEvent", "session_id": "s1", "tokens": 1},
        "note": "hello",
    }


def test_pydantic_inside_dict_is_tagged():
    obj = {"event": _MyEvent(session_id="x")}
    result = to_jsonable(obj)
    assert result == {"event": {"_type": "_MyEvent", "session_id": "x", "tokens": 0}}


def test_pydantic_inside_list_is_tagged():
    obj = [_MyEvent(session_id="a"), _MyEvent(session_id="b")]
    result = to_jsonable(obj)
    assert result == [
        {"_type": "_MyEvent", "session_id": "a", "tokens": 0},
        {"_type": "_MyEvent", "session_id": "b", "tokens": 0},
    ]
