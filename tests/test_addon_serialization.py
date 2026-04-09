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


def test_sdk_assistant_message_round_trips_with_content_block_types():
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
