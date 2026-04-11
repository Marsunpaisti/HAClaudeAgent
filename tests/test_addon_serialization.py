"""Unit tests for the add-on serialization primitives."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# Make the add-on src importable without installing it as a package.
ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from serialization import (  # noqa: E402
    _reset_drop_log_dedupe_for_tests,
    to_jsonable,
)


@pytest.fixture(autouse=True)
def _clear_drop_log_dedupe():
    """Reset the field-drop dedupe set between tests so each test sees a
    clean slate. Without this, tests that trigger a drop on the same
    (class, field) pair would interact — the second test would see a
    DEBUG log where it expected a WARNING."""
    _reset_drop_log_dedupe_for_tests()
    yield
    _reset_drop_log_dedupe_for_tests()


@dataclass
class _Leaf:
    text: str


@dataclass
class _Branch:
    name: str
    children: list[_Leaf]
    metadata: dict[str, str] = field(default_factory=dict)


def test_primitive_values_pass_through():
    assert to_jsonable(42) == 42
    assert to_jsonable("hello") == "hello"
    assert to_jsonable(None) is None
    assert to_jsonable(True) is True
    assert to_jsonable(1.5) == 1.5


def test_plain_dict_is_not_tagged():
    result = to_jsonable({"a": 1, "b": "two"})
    assert result == {"a": 1, "b": "two"}
    assert "_type" not in result


def test_list_of_primitives_passes_through():
    assert to_jsonable([1, 2, 3]) == [1, 2, 3]


def test_dataclass_gets_type_tag():
    result = to_jsonable(_Leaf(text="hi"))
    assert result == {"_type": "_Leaf", "text": "hi"}


def test_nested_dataclasses_are_recursively_tagged():
    branch = _Branch(
        name="root",
        children=[_Leaf(text="a"), _Leaf(text="b")],
        metadata={"key": "value"},
    )
    result = to_jsonable(branch)
    assert result == {
        "_type": "_Branch",
        "name": "root",
        "children": [
            {"_type": "_Leaf", "text": "a"},
            {"_type": "_Leaf", "text": "b"},
        ],
        "metadata": {"key": "value"},
    }


def test_dict_values_containing_dataclasses_are_tagged():
    result = to_jsonable({"leaf": _Leaf(text="x")})
    assert result == {"leaf": {"_type": "_Leaf", "text": "x"}}


def test_sdk_assistant_message_is_serialized_with_content_block_types():
    """Smoke test against a real SDK type shape."""
    from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock

    msg = AssistantMessage(
        content=[
            TextBlock(text="hello"),
            ToolUseBlock(id="tool_1", name="call_service", input={"foo": "bar"}),
        ],
        model="claude-opus-4-6",
    )
    result = to_jsonable(msg)

    assert result["_type"] == "AssistantMessage"
    assert result["model"] == "claude-opus-4-6"
    assert result["content"][0] == {"_type": "TextBlock", "text": "hello"}
    assert result["content"][1] == {
        "_type": "ToolUseBlock",
        "id": "tool_1",
        "name": "call_service",
        "input": {"foo": "bar"},
    }


def test_dataclass_with_type_field_raises_collision_error():
    """Field named '_type' would collide with the type discriminator."""
    import pytest

    @dataclass
    class _Colliding:
        _type: str
        value: int

    with pytest.raises(ValueError, match="collides with the type discriminator"):
        to_jsonable(_Colliding(_type="user-supplied", value=1))


def test_to_jsonable_drops_unserializable_dataclass_field(caplog):
    """A non-JSON-native field (datetime) is dropped with a WARNING log;
    sibling fields survive."""
    import logging
    from datetime import datetime

    @dataclass
    class _HasBadField:
        good: str
        bad: Any
        also_good: int

    obj = _HasBadField(
        good="ok",
        bad=datetime(2024, 1, 1, 12, 0, 0),
        also_good=42,
    )

    with caplog.at_level(logging.WARNING, logger="serialization"):
        result = to_jsonable(obj)

    assert result == {"_type": "_HasBadField", "good": "ok", "also_good": 42}
    assert "bad" not in result
    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "Dropping unserializable field _HasBadField.bad" in r.message
        for r in warning_records
    )


def test_to_jsonable_drops_unserializable_dict_value():
    from datetime import datetime

    obj = {"keep": 1, "drop": datetime(2024, 1, 1), "also_keep": "yes"}
    result = to_jsonable(obj)
    assert result == {"keep": 1, "also_keep": "yes"}


def test_to_jsonable_drops_unserializable_list_element():
    from datetime import datetime

    obj = [1, datetime(2024, 1, 1), "keep", 3]
    result = to_jsonable(obj)
    # list element dropped (not replaced with None) — preserves JSON validity
    assert result == [1, "keep", 3]


def test_to_jsonable_drops_dict_entry_with_non_json_key():
    """Bytes keys can't be serialized — drop with a log, keep valid entries."""
    obj = {b"bytes_key": "dropped", "str_key": "kept"}
    result = to_jsonable(obj)
    assert result == {"str_key": "kept"}


def test_to_jsonable_drops_dict_entry_with_tuple_key():
    obj = {(1, 2): "dropped", "str_key": "kept"}
    result = to_jsonable(obj)
    assert result == {"str_key": "kept"}


def test_to_jsonable_keeps_primitive_dict_keys():
    """json.dumps coerces str/int/float/bool/None keys to strings natively,
    so these keys survive to_jsonable without being dropped.

    We check each key in isolation to sidestep Python's ``True == 1`` and
    ``False == 0`` equality semantics — a single dict containing both
    ``True`` and ``1`` as keys would collapse to one entry, which would
    mask the actual behavior under test.
    """
    import json as _json

    for key in ("str_key", 42, 3.14, True, False, None):
        obj = {key: "value"}
        result = to_jsonable(obj)
        assert key in result, f"{key!r} was dropped"
        assert result[key] == "value"
        # Smoke-check: must be json.dumps-safe at the caller.
        _json.dumps(result)


def test_to_jsonable_dropping_preserves_siblings_in_nested_structure():
    """A bad value deep in a nested structure only drops itself."""
    from datetime import datetime

    @dataclass
    class _Nested:
        name: str
        data: dict[str, Any]
        children: list[Any]

    obj = _Nested(
        name="outer",
        data={"ok": "yes", "bad": datetime(2024, 1, 1)},
        children=["a", datetime(2024, 1, 1), "b"],
    )
    result = to_jsonable(obj)
    assert result == {
        "_type": "_Nested",
        "name": "outer",
        "data": {"ok": "yes"},
        "children": ["a", "b"],
    }


def test_to_jsonable_drops_set_field():
    """Sets aren't JSON-serializable by default."""

    @dataclass
    class _HasSet:
        name: str
        tags: Any

    obj = _HasSet(name="x", tags={"a", "b", "c"})
    result = to_jsonable(obj)
    assert result == {"_type": "_HasSet", "name": "x"}
    assert "tags" not in result


def test_to_jsonable_drops_bytes_field():
    @dataclass
    class _HasBytes:
        name: str
        payload: Any

    obj = _HasBytes(name="x", payload=b"binary data")
    result = to_jsonable(obj)
    assert result == {"_type": "_HasBytes", "name": "x"}


def test_to_jsonable_drops_custom_object_field():
    """Arbitrary non-serializable objects (e.g. a file handle or a
    user-defined class with no __json__ protocol) are dropped."""

    class _NotSerializable:
        def __repr__(self) -> str:
            return "<NotSerializable>"

    @dataclass
    class _HasCustom:
        name: str
        obj: Any

    obj = _HasCustom(name="x", obj=_NotSerializable())
    result = to_jsonable(obj)
    assert result == {"_type": "_HasCustom", "name": "x"}


def test_to_jsonable_still_preserves_nan_and_inf_floats_as_values():
    """NaN/inf floats as leaf *values* currently survive via default
    json.dumps semantics; dropping them would be a wire-format behavior
    change. Guard against accidental regression."""
    obj = {"ok": 1.0, "nan_val": float("nan"), "inf_val": float("inf")}
    result = to_jsonable(obj)
    assert "nan_val" in result
    assert "inf_val" in result
    # Round-trip via default json.dumps (the caller's contract).
    import json as _json

    _json.dumps(result)


def test_to_jsonable_drops_nan_and_inf_float_dict_keys():
    """NaN/inf floats as dict *keys* must be dropped explicitly: json.dumps
    would coerce them to the strings "NaN"/"Infinity" on the wire, silently
    mutating the key type. The wire-protocol contract preserves key
    identity, so drop-with-log is the correct behavior."""
    import math

    obj = {
        "ok_str": 1,
        float("nan"): "nan_val_dropped",
        float("inf"): "inf_val_dropped",
        float("-inf"): "neg_inf_val_dropped",
        1.5: "finite_float_kept",
    }
    result = to_jsonable(obj)
    assert "ok_str" in result
    assert 1.5 in result
    # The non-finite float keys are gone.
    assert not any(isinstance(k, float) and not math.isfinite(k) for k in result)
    # Every value that was kept should be findable.
    assert result["ok_str"] == 1
    assert result[1.5] == "finite_float_kept"


def test_to_jsonable_dropping_a_field_survives_getattr_exception(caplog):
    """If a dataclass attribute access raises (e.g. a broken property or
    descriptor), the field is dropped with a log instead of aborting the
    whole payload. This keeps the resilience guarantee honest."""
    import logging

    @dataclass
    class _Base:
        good: str = "ok"
        bad: Any = None
        also_good: int = 0

    class _Broken(_Base):
        """Dataclass subclass whose ``bad`` attribute access raises."""

        def __getattribute__(self, name: str) -> Any:
            if name == "bad":
                raise RuntimeError("attribute access exploded")
            return super().__getattribute__(name)

    obj = _Broken()
    with caplog.at_level(logging.WARNING, logger="serialization"):
        result = to_jsonable(obj)

    assert result["_type"] == "_Broken"
    assert result["good"] == "ok"
    assert result["also_good"] == 0
    assert "bad" not in result
    assert any(
        "_Broken.bad" in r.message and "getattr raised" in r.message
        for r in caplog.records
        if r.levelname == "WARNING"
    )


def test_to_jsonable_getattr_exception_does_not_catch_type_collision():
    """A nested `_type`-field collision in a child dataclass must still
    propagate all the way out. The drop machinery must only catch the
    getattr-level exception, not ValueError from the collision check."""

    @dataclass
    class _BadChild:
        _type: str = "user-supplied"

    @dataclass
    class _Parent:
        name: str
        child: Any

    obj = _Parent(name="outer", child=_BadChild())
    with pytest.raises(ValueError, match="collides with the type discriminator"):
        to_jsonable(obj)


def test_to_jsonable_field_drop_dedupe_first_warns_then_debugs(caplog):
    """Hitting the same (class, field) drop multiple times should only
    emit WARNING once — subsequent occurrences fall to DEBUG to prevent
    log storms on recurring messages (e.g. every StreamEvent in a turn
    with a bad field)."""
    import logging
    from datetime import datetime

    @dataclass
    class _Recurring:
        name: str
        bad: Any

    with caplog.at_level(logging.DEBUG, logger="serialization"):
        to_jsonable(_Recurring(name="a", bad=datetime(2024, 1, 1)))
        to_jsonable(_Recurring(name="b", bad=datetime(2024, 1, 2)))
        to_jsonable(_Recurring(name="c", bad=datetime(2024, 1, 3)))

    warning_records = [
        r
        for r in caplog.records
        if r.levelname == "WARNING" and "_Recurring.bad" in r.message
    ]
    debug_records = [
        r
        for r in caplog.records
        if r.levelname == "DEBUG" and "_Recurring.bad" in r.message
    ]
    assert len(warning_records) == 1, (
        f"expected exactly 1 WARNING, got {len(warning_records)}: {warning_records}"
    )
    assert len(debug_records) == 2, (
        f"expected 2 DEBUG records after the first warn, got {len(debug_records)}"
    )


def test_to_jsonable_field_drop_dedupe_is_per_location(caplog):
    """Dedupe keys on (class, field), so a drop in a different class or a
    different field of the same class still emits its first WARNING."""
    import logging
    from datetime import datetime

    @dataclass
    class _A:
        bad_one: Any
        bad_two: Any

    @dataclass
    class _B:
        bad_one: Any

    with caplog.at_level(logging.WARNING, logger="serialization"):
        to_jsonable(_A(bad_one=datetime(2024, 1, 1), bad_two=datetime(2024, 1, 2)))
        to_jsonable(_B(bad_one=datetime(2024, 1, 3)))

    warning_messages = [r for r in caplog.records if r.levelname == "WARNING"]
    locations = {"_A.bad_one", "_A.bad_two", "_B.bad_one"}
    matched = {
        loc for loc in locations if any(loc in r.message for r in warning_messages)
    }
    assert matched == locations, (
        f"expected warnings at every location, matched={matched}"
    )


def test_to_jsonable_output_is_always_json_dumpable_after_drops():
    """End-to-end invariant: whatever to_jsonable produces must survive
    a default json.dumps call, even when the input had bad fields."""
    import json as _json
    from datetime import datetime

    @dataclass
    class _Mixed:
        good: str
        bad_leaf: Any
        bad_dict: dict[str, Any]
        bad_list: list[Any]

    obj = _Mixed(
        good="yes",
        bad_leaf=datetime(2024, 1, 1),
        bad_dict={b"bytes_key": 1, "str_key": 2, "bad_val": datetime(2024, 1, 1)},
        bad_list=[1, set(), datetime(2024, 1, 1), "tail"],
    )
    result = to_jsonable(obj)
    # Must not raise
    _json.dumps(result)


def test_to_jsonable_invariant_holds_through_three_container_levels():
    """Stronger invariant: a bad value buried inside
    dataclass → list → dataclass → dict → bad leaf drops only the bad
    leaf, and every surrounding structure survives intact and
    json.dumps-safe. Verifies the sentinel propagates correctly through
    multiple enclosing walkers."""
    import json as _json
    from datetime import datetime

    @dataclass
    class _Inner:
        label: str
        payload: dict[str, Any]

    @dataclass
    class _Middle:
        items: list[Any]

    @dataclass
    class _Outer:
        name: str
        inner: _Middle

    obj = _Outer(
        name="root",
        inner=_Middle(
            items=[
                "first",
                _Inner(label="nested", payload={"ok": 1, "bad": datetime(2024, 1, 1)}),
                42,
            ],
        ),
    )
    result = to_jsonable(obj)
    # Must not raise
    _json.dumps(result)

    # Every level of the outer structure must be intact.
    assert result["_type"] == "_Outer"
    assert result["name"] == "root"
    middle = result["inner"]
    assert middle["_type"] == "_Middle"
    assert len(middle["items"]) == 3
    assert middle["items"][0] == "first"
    assert middle["items"][2] == 42
    inner = middle["items"][1]
    assert inner["_type"] == "_Inner"
    assert inner["label"] == "nested"
    # Only the bad leaf inside the innermost dict was dropped.
    assert inner["payload"] == {"ok": 1}


def test_exception_to_dict_captures_basic_exception():
    from serialization import exception_to_dict

    err = ValueError("bad value")
    payload = exception_to_dict(err)

    assert payload["_type"] == "ValueError"
    assert payload["module"] == "builtins"
    assert payload["message"] == "bad value"
    assert payload["attrs"] == {}
    assert "traceback" in payload
    assert isinstance(payload["traceback"], str)


def test_exception_to_dict_captures_sdk_cli_not_found():
    from claude_agent_sdk import CLINotFoundError
    from serialization import exception_to_dict

    err = CLINotFoundError(message="Claude Code not found", cli_path="/usr/bin/claude")
    payload = exception_to_dict(err)

    assert payload["_type"] == "CLINotFoundError"
    assert payload["module"] == "claude_agent_sdk._errors"
    assert "Claude Code not found" in payload["message"]
    assert "/usr/bin/claude" in payload["message"]


def test_exception_to_dict_captures_process_error_attrs():
    from claude_agent_sdk import ProcessError
    from serialization import exception_to_dict

    err = ProcessError("process crashed", exit_code=137, stderr="OOM killed")
    payload = exception_to_dict(err)

    assert payload["_type"] == "ProcessError"
    assert payload["attrs"]["exit_code"] == 137
    assert payload["attrs"]["stderr"] == "OOM killed"


def test_exception_to_dict_non_serializable_attrs_become_repr():
    from serialization import exception_to_dict

    class WeirdError(Exception):
        def __init__(self):
            super().__init__("weird")
            self.nested_exc = ValueError("inner")

    err = WeirdError()
    payload = exception_to_dict(err)

    # ValueError is not json.dumps-able → must fall back to repr
    assert isinstance(payload["attrs"]["nested_exc"], str)
    assert "ValueError" in payload["attrs"]["nested_exc"]
    assert "inner" in payload["attrs"]["nested_exc"]


def test_exception_to_dict_captures_traceback_when_raised():
    from serialization import exception_to_dict

    try:
        raise RuntimeError("boom")
    except RuntimeError as err:
        payload = exception_to_dict(err)

    assert "RuntimeError" in payload["traceback"]
    assert "boom" in payload["traceback"]
    assert (
        "test_exception_to_dict_captures_traceback_when_raised" in payload["traceback"]
    )


def test_exception_to_dict_skips_dunder_and_underscore_attrs():
    from serialization import exception_to_dict

    class AnnotatedError(Exception):
        def __init__(self):
            super().__init__("msg")
            self.public = "keep"
            self._private = "drop"

    payload = exception_to_dict(AnnotatedError())
    assert payload["attrs"] == {"public": "keep"}


def test_exception_to_dict_nan_float_attr_falls_back_to_repr():
    from serialization import exception_to_dict

    class NaNError(Exception):
        def __init__(self):
            super().__init__("nan err")
            self.bad_float = float("nan")

    payload = exception_to_dict(NaNError())
    # NaN is not valid JSON; must fall back to repr
    assert isinstance(payload["attrs"]["bad_float"], str)
    assert "nan" in payload["attrs"]["bad_float"].lower()


def test_exception_to_dict_handles_repr_that_raises():
    from serialization import exception_to_dict

    class UnreprableValue:
        def __repr__(self):
            raise RuntimeError("repr is broken")

    class HostileError(Exception):
        def __init__(self):
            super().__init__("hostile")
            self.weapon = UnreprableValue()

    # Must not raise
    payload = exception_to_dict(HostileError())
    assert "<unrepresentable UnreprableValue>" in payload["attrs"]["weapon"]


def test_exception_to_dict_handles_vars_that_raises():
    from serialization import exception_to_dict

    class NoVarsError(Exception):
        @property
        def __dict__(self):
            raise RuntimeError("no dict for you")

    err = NoVarsError("ouch")
    # Must not raise; attrs must be empty
    payload = exception_to_dict(err)
    assert payload["attrs"] == {}
    assert payload["_type"] == "NoVarsError"
    assert payload["message"] == "ouch"


def test_exception_to_dict_output_is_strict_json():
    """The whole payload must be parseable as strict (RFC 8259) JSON."""
    import json as _json

    from serialization import exception_to_dict

    class MixedError(Exception):
        def __init__(self):
            super().__init__("mixed")
            self.ok = "string value"
            self.also_ok = [1, 2, 3]
            self.bad = float("inf")

    payload = exception_to_dict(MixedError())
    serialized = _json.dumps(payload, allow_nan=False)
    reparsed = _json.loads(serialized)

    assert reparsed["_type"] == "MixedError"
    assert reparsed["attrs"]["ok"] == "string value"
    assert reparsed["attrs"]["also_ok"] == [1, 2, 3]
    # bad was inf → fell back to repr → string
    assert isinstance(reparsed["attrs"]["bad"], str)
