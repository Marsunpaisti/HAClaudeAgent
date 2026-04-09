"""Unit tests for the add-on serialization primitives."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# Make the add-on src importable without installing it as a package.
ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from serialization import to_jsonable  # noqa: E402


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
