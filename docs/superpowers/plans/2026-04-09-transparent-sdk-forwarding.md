# Transparent SDK Forwarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the add-on ↔ integration wire protocol so the add-on becomes a transparent forwarder of Claude Agent SDK messages and exceptions, and `conversation.py` consumes them as real SDK dataclass instances and catches real SDK exception classes — identical to direct-SDK usage.

**Architecture:** The add-on's `_stream_query` loop becomes a generic forwarder: each SDK `Message` is walked by a recursive `to_jsonable` function that tags every dataclass with a `_type` field, and each caught exception is captured as a `{_type, module, message, attrs, traceback}` dict. Both are emitted as SSE events. The integration adds `claude-agent-sdk` as a runtime requirement, parses the SSE stream, reconstructs messages by looking up class names in `claude_agent_sdk` and using `cls(**fields)`, and reconstructs exceptions via `cls.__new__` + attribute restoration. The integration's consumer code becomes `async for message in sdk_stream(resp): match message:` wrapped in `except CLINotFoundError: ...` — indistinguishable from direct-SDK code.

**Tech Stack:** Python 3.13, FastAPI + uvicorn (add-on), aiohttp (integration), `claude-agent-sdk==0.1.56`, Home Assistant 2025.4+, pytest, uv.

---

## File Structure

**Create:**
- `ha_claude_agent_addon/src/serialization.py` — `to_jsonable()` dataclass walker + `exception_to_dict()` exception capture
- `custom_components/ha_claude_agent/stream.py` — SSE parser + message/exception reconstruction + public `sdk_stream()` async generator
- `tests/test_addon_serialization.py` — unit tests for the add-on's serialization primitives
- `tests/test_integration_stream.py` — unit tests for the integration's stream helper

**Modify:**
- `ha_claude_agent_addon/src/server.py` — replace `_stream_query`'s isinstance chain with a generic forwarder; collapse per-exception handlers into a single catch-all
- `custom_components/ha_claude_agent/manifest.json` — add `"requirements": ["claude-agent-sdk==0.1.56"]`
- `custom_components/ha_claude_agent/conversation.py` — rewrite `_async_handle_message` to consume real SDK message instances via `match`, catch real SDK exceptions via `except`; remove `_StreamState`, `_transform_stream`, `_map_stream_event`, `_parse_sse`; rekey `_ERROR_MESSAGES` to use SDK exception class names and ResultMessage subtypes

**Untouched (explicit):**
- `ha_claude_agent_addon/src/ha_client.py`, `ha_claude_agent_addon/src/tools.py` — HA REST API and MCP tool layers, unrelated to the streaming protocol
- `custom_components/ha_claude_agent/models.py` and `ha_claude_agent_addon/src/models.py` — these are the **request** models (`QueryRequest`), not the response models. The wire protocol change is entirely on the response side.
- `custom_components/ha_claude_agent/const.py`, `config_flow.py`, `helpers.py`, `__init__.py` — no changes needed.

---

## Task Overview

1. Add-on: `to_jsonable()` walker + tests
2. Add-on: `exception_to_dict()` capture + tests
3. Add-on: rewrite `server.py` streaming loop
4. Integration: add SDK runtime requirement to manifest
5. Integration: SSE parser + tests
6. Integration: `from_jsonable()` message reconstruction + tests
7. Integration: exception reconstruction + tests
8. Integration: public `sdk_stream()` helper + tests
9. Integration: rewrite `conversation.py` consumer
10. Verification sweep

---

### Task 1: Add-on `to_jsonable()` walker

**Files:**
- Create: `ha_claude_agent_addon/src/serialization.py`
- Test: `tests/test_addon_serialization.py`

**Context for the engineer:**
The SDK emits `@dataclass` instances like `AssistantMessage(content=[TextBlock(text="hi"), ToolUseBlock(id=..., name=..., input=...)], model="...", ...)`. To forward these over HTTP we need to convert them to JSON. `dataclasses.asdict()` alone loses type discrimination — a `TextBlock` becomes `{"text": "hi"}` and a `ToolUseBlock` becomes `{"id": ..., "name": ..., "input": ...}`, and the integration can't tell them apart cleanly. We need a walker that tags every dataclass instance with a `_type` field containing its class name, then recurses into fields so nested content blocks round-trip losslessly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_addon_serialization.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_addon_serialization.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'serialization'`

- [ ] **Step 3: Create the serialization module**

Create `ha_claude_agent_addon/src/serialization.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_addon_serialization.py -v`
Expected: PASS — all 7 tests green.

- [ ] **Step 5: Commit**

```bash
git add ha_claude_agent_addon/src/serialization.py tests/test_addon_serialization.py
git commit -m "feat(addon): add to_jsonable walker for SDK dataclass forwarding"
```

---

### Task 2: Add-on `exception_to_dict()` capture

**Files:**
- Modify: `ha_claude_agent_addon/src/serialization.py`
- Modify: `tests/test_addon_serialization.py`

**Context for the engineer:**
SDK exceptions like `CLINotFoundError`, `ProcessError(message, exit_code=1, stderr="...")`, and `CLIJSONDecodeError(line, original_error)` carry instance attributes beyond their message string. We need to capture all serializable attributes generically so the integration can reconstruct the full exception state. Non-JSON-serializable attrs (e.g. `CLIJSONDecodeError.original_error` is itself an Exception) get converted to `repr()` rather than dropped. We also capture the formatted traceback as a string so the integration can log the add-on-side stack trace alongside the re-raised exception.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_addon_serialization.py`:

```python
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
    assert "test_exception_to_dict_captures_traceback_when_raised" in payload["traceback"]


def test_exception_to_dict_skips_dunder_and_underscore_attrs():
    from serialization import exception_to_dict

    class AnnotatedError(Exception):
        def __init__(self):
            super().__init__("msg")
            self.public = "keep"
            self._private = "drop"

    payload = exception_to_dict(AnnotatedError())
    assert payload["attrs"] == {"public": "keep"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_addon_serialization.py -v -k exception`
Expected: FAIL with `ImportError: cannot import name 'exception_to_dict' from 'serialization'`

- [ ] **Step 3: Add `exception_to_dict` to the serialization module**

Modify `ha_claude_agent_addon/src/serialization.py` — add these imports at the top (after the existing `dataclasses` import):

```python
import json
import traceback
```

Then append this function to the file:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_addon_serialization.py -v`
Expected: PASS — all tests (original 7 plus 6 new) green.

- [ ] **Step 5: Commit**

```bash
git add ha_claude_agent_addon/src/serialization.py tests/test_addon_serialization.py
git commit -m "feat(addon): add exception_to_dict capture for transparent exception forwarding"
```

---

### Task 3: Rewrite add-on `server.py` streaming loop

**Files:**
- Modify: `ha_claude_agent_addon/src/server.py`

**Context for the engineer:**
Today `_stream_query` has an isinstance chain (`if isinstance(message, SystemMessage) ... elif isinstance(message, StreamEvent) ... elif isinstance(message, ResultMessage)`) and separate `except` blocks per SDK exception class, each mapping to a hand-picked `error_code`. After this task, the loop forwards EVERY SDK message generically via `to_jsonable`, and catches ALL exceptions in one handler via `exception_to_dict`. The add-on no longer makes semantic decisions about which messages to forward or which exceptions mean what — that logic moves entirely to `conversation.py`.

**What to preserve:**
- Logging (`_LOGGER.info("Query: ...")` at the top, `_LOGGER.exception` on error)
- MCP server setup and `allowed_tools` construction
- `ClaudeAgentOptions` construction including `include_partial_messages=True`
- Session resume via `options.resume`

**What to remove:**
- The `isinstance` chain (replaced by a single `yield` line)
- Individual `except CLINotFoundError`, `except ProcessError`, `except CLIConnectionError`, `except CLIJSONDecodeError`, `except Exception` — replaced by one `except BaseException`
- Imports of `CLIConnectionError`, `CLIJSONDecodeError`, `CLINotFoundError`, `ProcessError`, `ResultMessage`, `SystemMessage`, `StreamEvent` from `claude_agent_sdk` — no longer referenced

- [ ] **Step 1: Update imports in `server.py`**

Replace the existing SDK imports block (lines ~19-30 in the current file) with:

```python
from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    query,
)
```

Remove these imports entirely:
- `CLIConnectionError`, `CLIJSONDecodeError`, `CLINotFoundError`, `ProcessError` (no longer catching by class)
- `ResultMessage`, `SystemMessage` (no longer branching on type)
- `from claude_agent_sdk.types import StreamEvent` (no longer branching on type)

Add an import for the new serialization helpers:

```python
from serialization import exception_to_dict, to_jsonable
```

- [ ] **Step 2: Rewrite `_stream_query` body**

Replace the entire body of `_stream_query` (everything after the docstring) with this:

```python
    _LOGGER.info(
        "Query: model=%s, effort=%s, max_turns=%d, resume=%s",
        body.model,
        body.effort,
        body.max_turns,
        body.session_id is not None,
    )

    try:
        mcp_tools = create_ha_tools(ha_client, body.exposed_entities)
        mcp_server = create_sdk_mcp_server(
            name=MCP_SERVER_NAME,
            version="1.0.0",
            tools=mcp_tools,
        )

        tool_prefix = f"mcp__{MCP_SERVER_NAME}__"
        allowed_tools = [
            f"{tool_prefix}call_service",
            f"{tool_prefix}get_entity_state",
            f"{tool_prefix}list_entities",
            "WebFetch",
            "WebSearch",
        ]

        options = ClaudeAgentOptions(
            model=body.model,
            system_prompt=body.system_prompt,
            mcp_servers={MCP_SERVER_NAME: mcp_server},
            allowed_tools=allowed_tools,
            max_turns=body.max_turns,
            env=auth_env,
            permission_mode="dontAsk",
            effort=body.effort,
            include_partial_messages=True,
            stderr=lambda line: _LOGGER.warning("CLI stderr: %s", line),
        )

        if body.session_id:
            options.resume = body.session_id

        async for message in query(prompt=body.prompt, options=options):
            yield _sse_event(type(message).__name__, to_jsonable(message))

    except BaseException as err:  # noqa: BLE001
        _LOGGER.exception("Query failed")
        yield _sse_event("exception", exception_to_dict(err))
```

Note: `BaseException` instead of `Exception` is deliberate — it ensures we forward things like `KeyboardInterrupt` and `SystemExit` too, which could happen during shutdown. The `# noqa: BLE001` suppresses ruff's blind-except warning (we genuinely want the broadest catch here).

- [ ] **Step 3: Verify lint passes**

Run: `uv run ruff check ha_claude_agent_addon/src/server.py`
Expected: PASS with no warnings (the `# noqa: BLE001` should suppress the blind-except warning).

If any `F401` unused-import warnings appear, remove the corresponding import — it means something was still referenced by the old code that no longer exists.

- [ ] **Step 4: Verify type check passes**

Run: `uv run mypy ha_claude_agent_addon/src/server.py`
Expected: PASS.

- [ ] **Step 5: Verify existing tests still pass**

Run: `uv run pytest tests/ -v`
Expected: PASS — `test_models_sync.py` and all serialization tests green. No server-specific tests yet.

- [ ] **Step 6: Commit**

```bash
git add ha_claude_agent_addon/src/server.py
git commit -m "refactor(addon): forward all SDK messages and exceptions transparently"
```

---

### Task 4: Add SDK runtime requirement to integration manifest

**Files:**
- Modify: `custom_components/ha_claude_agent/manifest.json`

**Context for the engineer:**
The integration currently declares `"requirements": []` — it has no runtime Python dependencies beyond HA core. To import SDK types (`AssistantMessage`, `StreamEvent`, `CLINotFoundError`, etc.) and use them in `match`/`except` statements, HA needs to install `claude-agent-sdk` at runtime when the integration is loaded. We pin to the exact version used in the add-on to avoid schema drift between the two sides of the wire.

`claude_agent_sdk` at import time only defines classes — it does not spawn the CLI or do any IO. Verified by reading `claude_agent_sdk/__init__.py`. Its only new transitive dep is `mcp` (pure Python, built on pydantic which HA already has).

The `pyproject.toml` dev dep already pins `claude-agent-sdk>=0.1.56`, so development/test environments already have it. This task only adds the HA runtime declaration.

- [ ] **Step 1: Update `manifest.json`**

Edit `custom_components/ha_claude_agent/manifest.json`:

Change:
```json
  "requirements": [],
```

To:
```json
  "requirements": ["claude-agent-sdk==0.1.56"],
```

Also bump the `version` field from `"0.5.0"` to `"0.6.0"` — this is a user-visible behavior change (new runtime dep, new error-handling flow) so it deserves a minor version bump.

Final `manifest.json` should look like:

```json
{
  "domain": "ha_claude_agent",
  "name": "HA Claude Agent",
  "codeowners": ["@Marsunpaisti"],
  "config_flow": true,
  "dependencies": ["conversation"],
  "documentation": "https://github.com/Marsunpaisti/HAClaudeAgent",
  "integration_type": "service",
  "iot_class": "cloud_polling",
  "requirements": ["claude-agent-sdk==0.1.56"],
  "version": "0.6.0"
}
```

- [ ] **Step 2: Verify JSON is valid**

Run: `uv run python -c "import json; json.load(open('custom_components/ha_claude_agent/manifest.json'))"`
Expected: No output (valid JSON).

- [ ] **Step 3: Verify tests still pass**

Run: `uv run pytest tests/ -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add custom_components/ha_claude_agent/manifest.json
git commit -m "feat(integration): declare claude-agent-sdk as runtime requirement"
```

---

### Task 5: Integration SSE parser + tests

**Files:**
- Create: `custom_components/ha_claude_agent/stream.py`
- Create: `tests/test_integration_stream.py`

**Context for the engineer:**
This is the first of four tasks building the integration-side stream helper. We start with the lowest layer: parsing raw SSE bytes from an `aiohttp.ClientResponse.content` iterator into `(event_type, data_dict)` tuples. The add-on emits events in this format:

```
event: AssistantMessage
data: {"_type": "AssistantMessage", "content": [...], ...}

event: exception
data: {"_type": "CLINotFoundError", ...}
```

Comment lines (`:...`) and lines without a valid `event:` + `data:` pair are skipped. The parser is iterator-style so malformed events don't poison later ones.

Note: We're building `stream.py` incrementally across Tasks 5-8. This task only adds the SSE parsing layer. Later tasks add message reconstruction (Task 6), exception reconstruction (Task 7), and the public `sdk_stream()` function (Task 8).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_integration_stream.py`:

```python
"""Unit tests for the integration's SSE stream helper."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from custom_components.ha_claude_agent.stream import parse_sse_stream


class _FakeContent:
    """Minimal aiohttp.ClientResponse.content stand-in for tests."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[bytes]:
        for line in self._lines:
            yield line


class _FakeResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self.content = _FakeContent(lines)


@pytest.mark.asyncio
async def test_parse_sse_stream_yields_single_event():
    resp = _FakeResponse([
        b"event: session\n",
        b'data: {"session_id": "abc"}\n',
        b"\n",
    ])
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("session", {"session_id": "abc"})]


@pytest.mark.asyncio
async def test_parse_sse_stream_yields_multiple_events():
    resp = _FakeResponse([
        b"event: a\n",
        b'data: {"n": 1}\n',
        b"\n",
        b"event: b\n",
        b'data: {"n": 2}\n',
        b"\n",
    ])
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("a", {"n": 1}), ("b", {"n": 2})]


@pytest.mark.asyncio
async def test_parse_sse_stream_ignores_comment_lines():
    resp = _FakeResponse([
        b": this is a comment\n",
        b"event: ping\n",
        b'data: {}\n',
        b"\n",
    ])
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("ping", {})]


@pytest.mark.asyncio
async def test_parse_sse_stream_skips_malformed_json():
    resp = _FakeResponse([
        b"event: bad\n",
        b"data: not-json\n",
        b"\n",
        b"event: good\n",
        b'data: {"ok": true}\n',
        b"\n",
    ])
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("good", {"ok": True})]


@pytest.mark.asyncio
async def test_parse_sse_stream_skips_events_missing_data():
    resp = _FakeResponse([
        b"event: orphan\n",
        b"\n",
        b"event: good\n",
        b'data: {"ok": true}\n',
        b"\n",
    ])
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("good", {"ok": True})]


@pytest.mark.asyncio
async def test_parse_sse_stream_handles_crlf():
    resp = _FakeResponse([
        b"event: a\r\n",
        b'data: {"n": 1}\r\n',
        b"\r\n",
    ])
    events = [evt async for evt in parse_sse_stream(resp)]
    assert events == [("a", {"n": 1})]
```

If `pytest-asyncio` is not installed in the dev env, add it: `uv add --dev pytest-asyncio` (check `pyproject.toml` first — it may already be listed). Also ensure `pytest.ini_options` in `pyproject.toml` has `asyncio_mode = "auto"` or mark each test manually with `@pytest.mark.asyncio` (the tests above use the explicit marker so either mode works).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_integration_stream.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.ha_claude_agent.stream'`.

- [ ] **Step 3: Create the stream module skeleton with SSE parser**

Create `custom_components/ha_claude_agent/stream.py`:

```python
"""SSE stream helper for consuming the add-on's /query endpoint.

Parses SSE events emitted by the add-on and reconstructs them into real
SDK dataclass instances and real SDK exception classes, so that
`conversation.py` consumer code reads exactly like direct-SDK usage.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any, Protocol

_LOGGER = logging.getLogger(__name__)


class _SSEResponse(Protocol):
    """Minimal duck-typed interface for the aiohttp response we consume."""

    content: Any


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
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_line = line[5:].strip()
        # Lines starting with `:` (comments) or anything else are ignored.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_integration_stream.py -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_claude_agent/stream.py tests/test_integration_stream.py
git commit -m "feat(integration): add SSE parser for add-on response stream"
```

---

### Task 6: Integration `from_jsonable()` message reconstruction + tests

**Files:**
- Modify: `custom_components/ha_claude_agent/stream.py`
- Modify: `tests/test_integration_stream.py`

**Context for the engineer:**
This is the inverse of the add-on's `to_jsonable()` walker from Task 1. Given a dict with a `_type` key, look up the class by name in `claude_agent_sdk` and instantiate it with the remaining fields (recursively reconstructing any nested `_type`-tagged dicts). The result is a real `AssistantMessage`/`TextBlock`/etc. instance that the integration can `match` on like direct SDK output.

**Handling version skew:** If the payload contains fields the local SDK version doesn't know about, `cls(**fields)` raises `TypeError: unexpected keyword argument`. In that case we fall back to constructing with only the fields the dataclass accepts, logging the unknown fields at DEBUG level. This keeps us robust across minor SDK version differences without silently swallowing problems.

**Handling unknown classes:** If `_type` names a class that doesn't exist in `claude_agent_sdk` (e.g. the add-on was upgraded to a newer SDK that added a new message type), we return the raw dict (minus the `_type` key) rather than crash. The integration's consumer can `case _:` ignore unknown messages.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_integration_stream.py`:

```python
from custom_components.ha_claude_agent.stream import from_jsonable


def test_from_jsonable_primitives_pass_through():
    assert from_jsonable(42) == 42
    assert from_jsonable("hi") == "hi"
    assert from_jsonable(None) is None
    assert from_jsonable(True) is True


def test_from_jsonable_plain_dict_returns_dict():
    assert from_jsonable({"a": 1, "b": "two"}) == {"a": 1, "b": "two"}


def test_from_jsonable_list_of_primitives():
    assert from_jsonable([1, 2, 3]) == [1, 2, 3]


def test_from_jsonable_reconstructs_text_block():
    from claude_agent_sdk import TextBlock

    result = from_jsonable({"_type": "TextBlock", "text": "hello"})
    assert isinstance(result, TextBlock)
    assert result.text == "hello"


def test_from_jsonable_reconstructs_assistant_message_with_nested_blocks():
    from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock

    payload = {
        "_type": "AssistantMessage",
        "content": [
            {"_type": "TextBlock", "text": "hello"},
            {
                "_type": "ToolUseBlock",
                "id": "tool_1",
                "name": "call_service",
                "input": {"foo": "bar"},
            },
        ],
        "model": "claude-opus-4-6",
        "parent_tool_use_id": None,
        "error": None,
        "usage": None,
        "message_id": None,
        "stop_reason": None,
        "session_id": None,
        "uuid": None,
    }
    result = from_jsonable(payload)

    assert isinstance(result, AssistantMessage)
    assert result.model == "claude-opus-4-6"
    assert len(result.content) == 2
    assert isinstance(result.content[0], TextBlock)
    assert result.content[0].text == "hello"
    assert isinstance(result.content[1], ToolUseBlock)
    assert result.content[1].name == "call_service"
    assert result.content[1].input == {"foo": "bar"}


def test_from_jsonable_reconstructs_stream_event():
    from claude_agent_sdk import StreamEvent

    payload = {
        "_type": "StreamEvent",
        "uuid": "uuid-1",
        "session_id": "session-1",
        "event": {"type": "content_block_delta", "delta": {"text": "hi"}},
        "parent_tool_use_id": None,
    }
    result = from_jsonable(payload)

    assert isinstance(result, StreamEvent)
    assert result.session_id == "session-1"
    assert result.event == {"type": "content_block_delta", "delta": {"text": "hi"}}


def test_from_jsonable_reconstructs_system_message_with_dict_data():
    from claude_agent_sdk import SystemMessage

    payload = {
        "_type": "SystemMessage",
        "subtype": "init",
        "data": {"session_id": "abc", "model": "claude-opus-4-6"},
    }
    result = from_jsonable(payload)

    assert isinstance(result, SystemMessage)
    assert result.subtype == "init"
    assert result.data == {"session_id": "abc", "model": "claude-opus-4-6"}


def test_from_jsonable_unknown_type_returns_raw_dict():
    payload = {"_type": "FutureMessageType", "field": "value"}
    result = from_jsonable(payload)
    # Unknown class → raw dict with _type stripped
    assert result == {"field": "value"}


def test_from_jsonable_tolerates_unknown_fields():
    """If payload has a field the local SDK doesn't know, log and drop it."""
    payload = {
        "_type": "TextBlock",
        "text": "hello",
        "unexpected_future_field": 123,
    }
    from claude_agent_sdk import TextBlock

    result = from_jsonable(payload)
    assert isinstance(result, TextBlock)
    assert result.text == "hello"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_integration_stream.py -v -k from_jsonable`
Expected: FAIL with `ImportError: cannot import name 'from_jsonable' from 'custom_components.ha_claude_agent.stream'`.

- [ ] **Step 3: Add `from_jsonable` to `stream.py`**

Add these imports to `custom_components/ha_claude_agent/stream.py` (after the existing imports):

```python
import dataclasses

import claude_agent_sdk
```

Then append this function to the file:

```python
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
            accepted = {k: v for k, v in fields_payload.items() if k in known_field_names}
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_integration_stream.py -v`
Expected: PASS — all SSE parser tests plus the 9 new `from_jsonable` tests green.

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_claude_agent/stream.py tests/test_integration_stream.py
git commit -m "feat(integration): reconstruct SDK dataclasses from forwarded payloads"
```

---

### Task 7: Integration exception reconstruction + tests

**Files:**
- Modify: `custom_components/ha_claude_agent/stream.py`
- Modify: `tests/test_integration_stream.py`

**Context for the engineer:**
Given an exception payload `{"_type": "ProcessError", "module": "claude_agent_sdk._errors", "message": "...", "attrs": {"exit_code": 137, "stderr": "..."}, "traceback": "..."}`, produce a real `ProcessError` instance that `conversation.py` can catch with `except ProcessError: ...`. The tricky part: many SDK exceptions have non-standard `__init__` signatures (e.g. `CLIJSONDecodeError.__init__(line, original_error)`), so we can't just call `cls(message)`. Instead, we use `cls.__new__(cls)` to create an uninitialized instance, then call `Exception.__init__(self, message)` for the base message, and finally restore the captured attrs via `setattr`.

**Fallback behavior:** If `_type` names a class that isn't in `claude_agent_sdk`, or names something that isn't a subclass of `BaseException`, we return a plain `ClaudeSDKError` with a composed message like `"CLINotFoundError: Claude Code not found"`. This ensures the integration's `except ClaudeSDKError:` handler still catches anything forwarded from the add-on.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_integration_stream.py`:

```python
from custom_components.ha_claude_agent.stream import reconstruct_exception


def test_reconstruct_exception_cli_not_found():
    from claude_agent_sdk import CLIConnectionError, CLINotFoundError

    payload = {
        "_type": "CLINotFoundError",
        "module": "claude_agent_sdk._errors",
        "message": "Claude Code not found: /usr/bin/claude",
        "attrs": {},
        "traceback": "Traceback...",
    }
    exc = reconstruct_exception(payload)

    assert isinstance(exc, CLINotFoundError)
    # CLINotFoundError inherits from CLIConnectionError
    assert isinstance(exc, CLIConnectionError)
    assert str(exc) == "Claude Code not found: /usr/bin/claude"


def test_reconstruct_exception_process_error_preserves_attrs():
    from claude_agent_sdk import ProcessError

    payload = {
        "_type": "ProcessError",
        "module": "claude_agent_sdk._errors",
        "message": "process crashed (exit code: 137)",
        "attrs": {"exit_code": 137, "stderr": "OOM killed"},
        "traceback": "Traceback...",
    }
    exc = reconstruct_exception(payload)

    assert isinstance(exc, ProcessError)
    assert exc.exit_code == 137
    assert exc.stderr == "OOM killed"
    assert "exit code: 137" in str(exc)


def test_reconstruct_exception_cli_json_decode_error_bypasses_init():
    """CLIJSONDecodeError.__init__ requires (line, original_error).
    Reconstruction must bypass __init__ to avoid signature mismatches."""
    from claude_agent_sdk import CLIJSONDecodeError

    payload = {
        "_type": "CLIJSONDecodeError",
        "module": "claude_agent_sdk._errors",
        "message": "Failed to decode JSON: bad line...",
        "attrs": {"line": "bad line"},
        "traceback": "Traceback...",
    }
    exc = reconstruct_exception(payload)

    assert isinstance(exc, CLIJSONDecodeError)
    assert exc.line == "bad line"
    assert "Failed to decode JSON" in str(exc)


def test_reconstruct_exception_unknown_class_falls_back_to_sdk_base():
    from claude_agent_sdk import ClaudeSDKError

    payload = {
        "_type": "SomeFutureError",
        "module": "claude_agent_sdk._errors",
        "message": "a future error",
        "attrs": {},
        "traceback": "",
    }
    exc = reconstruct_exception(payload)

    assert isinstance(exc, ClaudeSDKError)
    # The composed message includes the original class name for debuggability
    assert "SomeFutureError" in str(exc)
    assert "a future error" in str(exc)


def test_reconstruct_exception_non_sdk_class_falls_back_to_sdk_base():
    """A ValueError from the add-on's own code should still become a
    ClaudeSDKError so the integration's `except ClaudeSDKError` catches it."""
    from claude_agent_sdk import ClaudeSDKError

    payload = {
        "_type": "ValueError",
        "module": "builtins",
        "message": "bad value",
        "attrs": {},
        "traceback": "",
    }
    exc = reconstruct_exception(payload)

    assert isinstance(exc, ClaudeSDKError)
    assert "ValueError" in str(exc)
    assert "bad value" in str(exc)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_integration_stream.py -v -k reconstruct_exception`
Expected: FAIL with `ImportError: cannot import name 'reconstruct_exception' from 'custom_components.ha_claude_agent.stream'`.

- [ ] **Step 3: Add `reconstruct_exception` to `stream.py`**

Append this function to `custom_components/ha_claude_agent/stream.py`:

```python
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
    """
    cls_name = payload.get("_type", "")
    message = payload.get("message", "")
    attrs = payload.get("attrs") or {}

    cls = getattr(claude_agent_sdk, cls_name, None)
    if not (
        isinstance(cls, type)
        and issubclass(cls, BaseException)
        and issubclass(cls, claude_agent_sdk.ClaudeSDKError)
    ):
        composed = f"{cls_name}: {message}" if cls_name else message
        return claude_agent_sdk.ClaudeSDKError(composed)

    exc = cls.__new__(cls)
    Exception.__init__(exc, message)
    for key, value in attrs.items():
        try:
            setattr(exc, key, value)
        except (AttributeError, TypeError):
            _LOGGER.debug(
                "Could not restore attr %s on reconstructed %s", key, cls_name
            )
    return exc
```

Note: We require `issubclass(cls, ClaudeSDKError)` (not just `BaseException`) so that a payload like `{"_type": "KeyboardInterrupt", ...}` doesn't construct a real `KeyboardInterrupt` on the integration side. Only SDK exception classes get reconstructed as their real type; everything else becomes a generic `ClaudeSDKError`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_integration_stream.py -v`
Expected: PASS — all tests green including the 5 new reconstruction tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_claude_agent/stream.py tests/test_integration_stream.py
git commit -m "feat(integration): reconstruct SDK exceptions from forwarded payloads"
```

---

### Task 8: Integration public `sdk_stream()` helper + tests

**Files:**
- Modify: `custom_components/ha_claude_agent/stream.py`
- Modify: `tests/test_integration_stream.py`

**Context for the engineer:**
This ties Tasks 5-7 together into a single public async generator that `conversation.py` will call. Its contract: given an `aiohttp.ClientResponse` carrying an SSE stream from the add-on's `/query` endpoint, yield real SDK message instances one by one, and raise real SDK exceptions if the add-on reports one via an `exception` event. The stream ends cleanly when the add-on closes the connection; an `exception` event terminates iteration by raising.

After this task, `conversation.py` can write:

```python
async for message in sdk_stream(resp):
    match message:
        case StreamEvent(event=ev): ...
        case ResultMessage(session_id=sid, total_cost_usd=cost): ...
```

— identical to what it'd write against the SDK directly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_integration_stream.py`:

```python
from custom_components.ha_claude_agent.stream import sdk_stream


@pytest.mark.asyncio
async def test_sdk_stream_yields_reconstructed_messages():
    from claude_agent_sdk import StreamEvent, SystemMessage

    resp = _FakeResponse([
        b"event: SystemMessage\n",
        b'data: {"_type": "SystemMessage", "subtype": "init", "data": {"session_id": "s1"}}\n',
        b"\n",
        b"event: StreamEvent\n",
        b'data: {"_type": "StreamEvent", "uuid": "u1", "session_id": "s1", "event": {"type": "content_block_delta", "delta": {"text": "hi"}}, "parent_tool_use_id": null}\n',
        b"\n",
    ])
    messages = [m async for m in sdk_stream(resp)]

    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].subtype == "init"
    assert messages[0].data == {"session_id": "s1"}
    assert isinstance(messages[1], StreamEvent)
    assert messages[1].session_id == "s1"


@pytest.mark.asyncio
async def test_sdk_stream_raises_sdk_exception_on_exception_event():
    from claude_agent_sdk import CLINotFoundError

    resp = _FakeResponse([
        b"event: exception\n",
        b'data: {"_type": "CLINotFoundError", "module": "claude_agent_sdk._errors", "message": "Claude Code not found", "attrs": {}, "traceback": "..."}\n',
        b"\n",
    ])

    with pytest.raises(CLINotFoundError) as exc_info:
        async for _ in sdk_stream(resp):
            pass
    assert "Claude Code not found" in str(exc_info.value)


@pytest.mark.asyncio
async def test_sdk_stream_raises_process_error_with_attrs():
    from claude_agent_sdk import ProcessError

    resp = _FakeResponse([
        b"event: exception\n",
        b'data: {"_type": "ProcessError", "module": "claude_agent_sdk._errors", "message": "crashed", "attrs": {"exit_code": 137, "stderr": "OOM"}, "traceback": "..."}\n',
        b"\n",
    ])

    with pytest.raises(ProcessError) as exc_info:
        async for _ in sdk_stream(resp):
            pass
    assert exc_info.value.exit_code == 137
    assert exc_info.value.stderr == "OOM"


@pytest.mark.asyncio
async def test_sdk_stream_yields_messages_then_raises_on_trailing_exception():
    """A stream that yields some messages before an exception — consumer
    should receive the messages, then the exception is raised."""
    from claude_agent_sdk import CLIConnectionError, StreamEvent

    resp = _FakeResponse([
        b"event: StreamEvent\n",
        b'data: {"_type": "StreamEvent", "uuid": "u1", "session_id": "s1", "event": {"type": "content_block_delta", "delta": {"text": "partial"}}, "parent_tool_use_id": null}\n',
        b"\n",
        b"event: exception\n",
        b'data: {"_type": "CLIConnectionError", "module": "claude_agent_sdk._errors", "message": "lost connection", "attrs": {}, "traceback": "..."}\n',
        b"\n",
    ])

    seen: list = []
    with pytest.raises(CLIConnectionError):
        async for message in sdk_stream(resp):
            seen.append(message)

    assert len(seen) == 1
    assert isinstance(seen[0], StreamEvent)


@pytest.mark.asyncio
async def test_sdk_stream_logs_addon_traceback_before_raising(caplog):
    import logging
    from claude_agent_sdk import CLINotFoundError

    resp = _FakeResponse([
        b"event: exception\n",
        b'data: {"_type": "CLINotFoundError", "module": "claude_agent_sdk._errors", "message": "gone", "attrs": {}, "traceback": "Traceback (most recent call last):\\n  File \\"server.py\\"\\n"}\n',
        b"\n",
    ])

    with caplog.at_level(logging.ERROR, logger="custom_components.ha_claude_agent.stream"):
        with pytest.raises(CLINotFoundError):
            async for _ in sdk_stream(resp):
                pass

    # The add-on's traceback string should appear in the logs
    assert any("Traceback" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_integration_stream.py -v -k sdk_stream`
Expected: FAIL with `ImportError: cannot import name 'sdk_stream' from 'custom_components.ha_claude_agent.stream'`.

- [ ] **Step 3: Add `sdk_stream` to `stream.py`**

Append this function to `custom_components/ha_claude_agent/stream.py`:

```python
async def sdk_stream(resp: _SSEResponse) -> AsyncGenerator[Any]:
    """Consume the add-on's SSE stream, yielding SDK message instances.

    For each `Message`-typed event, yields the reconstructed SDK dataclass
    instance (AssistantMessage, StreamEvent, SystemMessage, ResultMessage,
    UserMessage, RateLimitEvent, etc.). For an `exception` event, logs the
    add-on-side traceback and raises the reconstructed SDK exception.

    If the add-on emits a `_type` that isn't a known SDK class, the raw
    dict is yielded; the consumer's `match` statement will naturally fall
    through its `case _:` arm without crashing.
    """
    async for event_type, data in parse_sse_stream(resp):
        if event_type == "exception":
            traceback_text = data.get("traceback", "")
            if traceback_text:
                _LOGGER.error(
                    "Add-on raised %s. Add-on-side traceback:\n%s",
                    data.get("_type", "unknown"),
                    traceback_text,
                )
            raise reconstruct_exception(data)

        yield from_jsonable(data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_integration_stream.py -v`
Expected: PASS — all stream helper tests green.

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_claude_agent/stream.py tests/test_integration_stream.py
git commit -m "feat(integration): add sdk_stream public async generator"
```

---

### Task 9: Rewrite `conversation.py` consumer

**Files:**
- Modify: `custom_components/ha_claude_agent/conversation.py`

**Context for the engineer:**
This is the biggest single change. `_async_handle_message` currently uses a custom `_StreamState` mutable object, a `_transform_stream` async generator that yields ChatLog deltas while stashing session metadata as side effects, a `_map_stream_event` function that translates raw Anthropic event dicts, a `_parse_sse` low-level parser, and a dict of wire-protocol error codes `_ERROR_MESSAGES`.

After this task, all of that is gone. The handler uses `sdk_stream()` from Task 8 to yield real SDK messages, a `match` statement to handle each message type, and `except` blocks to catch real SDK exceptions. The `_ERROR_MESSAGES` table is rekeyed from wire-protocol codes to SDK exception class names + `ResultMessage` subtypes + `AssistantMessageError` values + the one HTTP transport error.

**Delta-driving into ChatLog:** Currently, deltas are driven via `chat_log.async_add_delta_content_stream(agent_id, async_gen)`, which expects an async generator yielding `AssistantContentDeltaDict`. We keep that mechanism, but the generator is now a small adapter that consumes `sdk_stream(resp)` and emits deltas only for `StreamEvent` messages with `text_delta`/`thinking_delta` content. Other SDK messages (SystemMessage, ResultMessage, RateLimitEvent, etc.) are consumed for their side effects (updating session state) but don't produce ChatLog deltas.

Because side-effect state needs to cross out of the adapter generator, we pass a small mutable holder into it — same pattern as the current `_StreamState`, just simpler because the adapter only needs to know about one thing (session mapping from SystemMessage init and ResultMessage).

- [ ] **Step 1: Replace the imports block**

Replace lines 1-44 of `custom_components/ha_claude_agent/conversation.py` with:

```python
"""Conversation platform for HA Claude Agent."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import aiohttp
from claude_agent_sdk import (
    AssistantMessage,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ClaudeSDKError,
    ProcessError,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    SystemMessage,
)
from homeassistant.components import conversation
from homeassistant.components.conversation import (
    AssistantContentDeltaDict,
    ChatLog,
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
    ConversationResult,
)
from homeassistant.components.homeassistant.exposed_entities import (
    async_should_expose,
)
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CHAT_MODEL,
    CONF_MAX_TURNS,
    CONF_PROMPT,
    CONF_THINKING_EFFORT,
    DEFAULT_CHAT_MODEL,
    DEFAULT_MAX_TURNS,
    DEFAULT_PROMPT,
    DEFAULT_THINKING_EFFORT,
    DOMAIN,
    QUERY_TIMEOUT_SECONDS,
)
from .helpers import build_system_prompt
from .models import QueryRequest
from .stream import sdk_stream

_LOGGER = logging.getLogger(__name__)
```

- [ ] **Step 2: Replace the `_ERROR_MESSAGES` table**

Replace the `_ERROR_MESSAGES` dict (currently lines ~48-76) with this new table keyed on SDK exception class names, ResultMessage subtypes, AssistantMessageError values, and the transport error:

```python
# Error messages keyed by:
#   - SDK exception class name (CLINotFoundError, ProcessError, ...)
#   - ResultMessage subtype (error_max_turns, error_max_budget_usd, ...)
#   - AssistantMessageError value (authentication_failed, billing_error, ...)
#   - Transport-layer error code (addon_unreachable)
_ERROR_MESSAGES: dict[str, str] = {
    # SDK exceptions
    "CLINotFoundError": (
        "Claude Code CLI not found in the add-on container. "
        "Try restarting the add-on."
    ),
    "ProcessError": "Claude Code process crashed. Check the add-on logs.",
    "CLIConnectionError": (
        "Could not connect to Claude Code CLI. Check the add-on logs."
    ),
    "CLIJSONDecodeError": "Received an invalid response from Claude. Try again.",
    "ClaudeSDKError": "An unexpected error occurred in the add-on.",
    # ResultMessage error subtypes
    "error_max_turns": (
        "Used all tool turns and couldn't finish. "
        "Try a simpler request or increase the max turns setting."
    ),
    "error_max_budget_usd": "This request hit the spending limit.",
    "error_during_execution": "Something went wrong while processing.",
    # AssistantMessage.error values
    "authentication_failed": (
        "Claude authentication failed. Check the auth token in the add-on settings."
    ),
    "billing_error": "Billing issue — check your account at console.anthropic.com.",
    "rate_limit": "Rate limited. Please wait a moment and try again.",
    "invalid_request": "The request to Claude was invalid.",
    "server_error": "Claude's servers returned an error. Please try again.",
    "unknown": "An unknown error occurred.",
    # Transport layer
    "addon_unreachable": (
        "Cannot reach the HA Claude Agent add-on. "
        "Is the add-on installed and running?"
    ),
}
```

- [ ] **Step 3: Replace `_StreamState` with a lightweight result holder**

Delete the entire `_StreamState` class (currently lines ~79-87) and replace it with:

```python
@dataclass
class _StreamResult:
    """Mutable holder for stream side-effects consumed by the delta adapter."""

    session_id: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    result_error_subtype: str | None = None  # ResultMessage.subtype if != "success"
    assistant_error: str | None = None  # AssistantMessage.error if set
```

- [ ] **Step 4: Replace `_async_handle_message`**

Replace the entire `_async_handle_message` method (currently lines ~163-262) with this:

```python
    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Handle a conversation turn by delegating to the add-on."""
        runtime_data = self.entry.runtime_data

        # Build request payload
        model = self.subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)
        user_prompt = self.subentry.data.get(CONF_PROMPT, DEFAULT_PROMPT)
        system_prompt = build_system_prompt(
            self.hass, user_prompt, location=runtime_data.location
        )

        session_id: str | None = None
        if user_input.conversation_id:
            session_id = runtime_data.sessions.get(user_input.conversation_id)

        effort = self.subentry.data.get(CONF_THINKING_EFFORT, DEFAULT_THINKING_EFFORT)
        max_turns = int(self.subentry.data.get(CONF_MAX_TURNS, DEFAULT_MAX_TURNS))

        _LOGGER.info(
            "Handling message: model=%s, effort=%s, resume=%s",
            model,
            effort,
            session_id is not None,
        )

        request = QueryRequest(
            prompt=user_input.text,
            model=model,
            system_prompt=system_prompt,
            max_turns=max_turns,
            effort=effort,
            session_id=session_id,
            exposed_entities=self._get_exposed_entity_ids(),
        )

        addon_url = runtime_data.addon_url
        http_session = async_get_clientsession(self.hass)
        result_state = _StreamResult()

        try:
            async with http_session.post(
                f"{addon_url}/query",
                json=request.model_dump(exclude_none=True),
                timeout=aiohttp.ClientTimeout(total=QUERY_TIMEOUT_SECONDS),
                headers={"Accept": "text/event-stream"},
            ) as resp:
                resp.raise_for_status()
                async for _content in chat_log.async_add_delta_content_stream(
                    user_input.agent_id,
                    _deltas_from_sdk_stream(resp, result_state),
                ):
                    # ChatLog accumulates deltas internally — just drain.
                    pass
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.error("Add-on request failed: %s", err)
            return self._error_response(
                _ERROR_MESSAGES["addon_unreachable"],
                chat_log,
                user_input.language,
            )
        except CLINotFoundError:
            return self._error_response(
                _ERROR_MESSAGES["CLINotFoundError"], chat_log, user_input.language
            )
        except ProcessError as err:
            _LOGGER.error("Claude process crashed: exit=%s", err.exit_code)
            return self._error_response(
                _ERROR_MESSAGES["ProcessError"], chat_log, user_input.language
            )
        except CLIConnectionError:
            return self._error_response(
                _ERROR_MESSAGES["CLIConnectionError"],
                chat_log,
                user_input.language,
            )
        except CLIJSONDecodeError:
            return self._error_response(
                _ERROR_MESSAGES["CLIJSONDecodeError"],
                chat_log,
                user_input.language,
            )
        except ClaudeSDKError as err:
            _LOGGER.error("Unknown SDK error: %s", err)
            return self._error_response(
                _ERROR_MESSAGES["ClaudeSDKError"], chat_log, user_input.language
            )

        _LOGGER.info(
            "Stream complete: session=%s, cost=$%s, turns=%s, "
            "result_error=%s, assistant_error=%s",
            result_state.session_id,
            result_state.cost_usd,
            result_state.num_turns,
            result_state.result_error_subtype,
            result_state.assistant_error,
        )

        # Soft errors: ResultMessage with error subtype, or AssistantMessage.error
        if result_state.result_error_subtype:
            msg = _ERROR_MESSAGES.get(
                result_state.result_error_subtype,
                f"Query failed: {result_state.result_error_subtype}",
            )
            return self._error_response(msg, chat_log, user_input.language)
        if result_state.assistant_error:
            msg = _ERROR_MESSAGES.get(
                result_state.assistant_error,
                f"Assistant error: {result_state.assistant_error}",
            )
            return self._error_response(msg, chat_log, user_input.language)

        # Store session mapping
        if result_state.session_id:
            runtime_data.sessions[chat_log.conversation_id] = result_state.session_id

        # Build HA response
        speech = _last_assistant_text(chat_log) or "I have no response."
        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(speech)
        return ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=False,
        )
```

- [ ] **Step 5: Replace the old helper functions at the bottom of the file**

Delete the entire block from `_last_assistant_text` through the end of the file (currently lines ~265-360 — covers `_last_assistant_text`, `_transform_stream`, `_map_stream_event`, and `_parse_sse`), and replace with this:

```python
def _last_assistant_text(chat_log: ChatLog) -> str:
    """Return the text content of the most recent assistant message, or ''."""
    for content in reversed(chat_log.content):
        if content.role == "assistant" and content.content:
            return content.content
    return ""


async def _deltas_from_sdk_stream(
    resp: aiohttp.ClientResponse,
    state: _StreamResult,
) -> AsyncGenerator[AssistantContentDeltaDict]:
    """Adapter: consume sdk_stream() and yield ChatLog deltas.

    Side-effects: records session/result metadata onto `state`. The
    ChatLog machinery only cares about assistant role markers and
    content/thinking deltas; other SDK message types (ResultMessage,
    SystemMessage, RateLimitEvent, etc.) are consumed silently for
    their metadata.
    """
    role_yielded = False

    async for message in sdk_stream(resp):
        match message:
            case StreamEvent(event=ev):
                delta = _delta_from_anthropic_event(ev)
                if delta is None:
                    continue
                if not role_yielded:
                    yield {"role": "assistant"}
                    role_yielded = True
                yield delta

            case SystemMessage(subtype="init", data=data):
                state.session_id = data.get("session_id") or state.session_id

            case ResultMessage(
                session_id=sid,
                subtype=subtype,
                total_cost_usd=cost,
                num_turns=turns,
            ):
                state.session_id = sid or state.session_id
                state.cost_usd = cost
                state.num_turns = turns
                if subtype != "success":
                    state.result_error_subtype = subtype

            case AssistantMessage(error=error) if error is not None:
                state.assistant_error = error

            case RateLimitEvent(rate_limit_info=info):
                _LOGGER.warning(
                    "Claude rate limit: status=%s type=%s utilization=%s",
                    info.status,
                    info.rate_limit_type,
                    info.utilization,
                )

            case _:
                # AssistantMessage (non-error), UserMessage (tool results),
                # and any future Message subtypes are ignored for now.
                pass


def _delta_from_anthropic_event(
    event: dict,
) -> AssistantContentDeltaDict | None:
    """Map a raw Anthropic stream event dict to a ChatLog delta, or None."""
    if event.get("type") != "content_block_delta":
        return None
    delta = event.get("delta") or {}
    delta_type = delta.get("type")
    if delta_type == "text_delta":
        text = delta.get("text", "")
        return {"content": text} if text else None
    if delta_type == "thinking_delta":
        thinking = delta.get("thinking", "")
        return {"thinking_content": thinking} if thinking else None
    return None
```

Note: The `case AssistantMessage(error=error) if error is not None` guard syntax is Python 3.10+ structural pattern matching with a guard clause. We need the guard because matching `case AssistantMessage(error=None)` (the happy path) should NOT record an error. HA requires Python 3.13, so the syntax is supported.

- [ ] **Step 6: Verify lint passes**

Run: `uv run ruff check custom_components/ha_claude_agent/`
Expected: PASS — no unused imports, no undefined names.

If any `F401` warnings appear for unused imports (e.g. `json`, `AsyncGenerator[AssistantContentDeltaDict]` from the old file), remove them.

- [ ] **Step 7: Verify formatting**

Run: `uv run ruff format --check custom_components/ha_claude_agent/`
If it reports files would be reformatted, run: `uv run ruff format custom_components/ha_claude_agent/` and re-stage.

- [ ] **Step 8: Verify type check passes**

Run: `uv run mypy custom_components/ha_claude_agent/`
Expected: PASS. If the `match` statement's guarded case produces a mypy warning about exhaustiveness, add `# type: ignore[misc]` on the guarded case line — HA's mypy config already disables several strict checks.

- [ ] **Step 9: Verify all tests pass**

Run: `uv run pytest tests/ -v`
Expected: PASS — all serialization, stream helper, and model sync tests green.

- [ ] **Step 10: Commit**

```bash
git add custom_components/ha_claude_agent/conversation.py
git commit -m "refactor(integration): consume SDK messages and exceptions via sdk_stream"
```

---

### Task 10: Full verification sweep

**Files:** None modified by default — this task only verifies the previous tasks landed cleanly. If any issue surfaces, fix it in a small follow-up commit within this task.

- [ ] **Step 1: Run the full lint check**

Run: `uv run ruff check custom_components/ ha_claude_agent_addon/src/ tests/`
Expected: PASS with no warnings.

- [ ] **Step 2: Run the formatting check**

Run: `uv run ruff format --check custom_components/ ha_claude_agent_addon/src/ tests/`
Expected: PASS. If not, run `uv run ruff format custom_components/ ha_claude_agent_addon/src/ tests/` and commit the formatting changes as `style: ruff format`.

- [ ] **Step 3: Run mypy on both sides**

Run: `uv run mypy custom_components/ha_claude_agent/ ha_claude_agent_addon/src/`
Expected: PASS.

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: PASS — all tests green. Expected test count:
- `test_models_sync.py`: 1 test
- `test_addon_serialization.py`: 13 tests (7 from Task 1 + 6 from Task 2)
- `test_integration_stream.py`: 25 tests (6 SSE + 9 from_jsonable + 5 reconstruct_exception + 5 sdk_stream)
- Total: 39 tests

- [ ] **Step 5: Confirm the wire protocol is truly generic (spec check)**

Open `ha_claude_agent_addon/src/server.py` and confirm:
- There is exactly ONE `yield _sse_event(...)` call in `_stream_query` for the happy path (one for messages)
- There is exactly ONE `yield _sse_event(...)` call in the exception handler
- There are NO `isinstance(message, ...)` checks in `_stream_query`
- There are NO SDK exception classes imported (only `ClaudeAgentOptions`, `query`, `create_sdk_mcp_server`)

Open `custom_components/ha_claude_agent/conversation.py` and confirm:
- The import block imports real SDK types from `claude_agent_sdk`
- The handler uses `except CLINotFoundError:` / `except ProcessError:` / etc. to catch SDK errors
- The handler uses `match message:` on SDK types, not on string event types
- The file does NOT contain `_parse_sse`, `_transform_stream`, `_map_stream_event`, or `_StreamState`

If any of these are violated, that indicates an incomplete earlier task — fix and commit as a follow-up.

- [ ] **Step 6: Rebuild the add-on Docker image (optional but recommended)**

The add-on's `Dockerfile` installs Python deps from `requirements.txt`. Since we didn't touch `requirements.txt` in this refactor, the image rebuild is only needed to verify the new `server.py` runs inside the container. Skip this step in CI; run it locally if you have Docker available.

Run (from repo root):

```bash
cd ha_claude_agent_addon && docker build -t ha-claude-agent-test .
```

Expected: Build succeeds. No need to actually run the container in this step.

- [ ] **Step 7: Manual smoke test (requires running HA + add-on)**

This is a manual end-to-end check. It's not part of automated verification; run it when you have a HA instance with the add-on running.

1. From HA, open the conversation entity for HA Claude Agent.
2. Send a simple message: "Turn on the kitchen light."
3. Observe streaming behavior in the HA UI — tokens should arrive incrementally, not all at once at the end.
4. Check the add-on logs: confirm you see `Query: model=... effort=... max_turns=... resume=...` followed by normal CLI output. No `isinstance` errors or serialization warnings.
5. Stop the add-on container, then send another message from HA. Confirm the integration surfaces "Cannot reach the HA Claude Agent add-on" (the transport-level error path).
6. Start the add-on again with a deliberately broken auth token to test SDK-level error reporting. The integration should surface "Claude authentication failed..." (the `AssistantMessageError` path for `authentication_failed`).

- [ ] **Step 8: Commit any follow-up fixes**

If steps 1-5 surfaced issues, commit the fixes as small focused commits:

```bash
git add <fixed files>
git commit -m "fix: <short description of what broke>"
```

If everything passed cleanly, no commit is needed for this task.

---

## Self-Review Notes

**Spec coverage check (all requirements from the discussion):**

| Requirement | Task |
|-------------|------|
| Add-on forwards ALL SDK messages, not a selected subset | Task 3 (generic `yield` loop) |
| `AssistantMessage`, `UserMessage`, `RateLimitEvent` flow through | Tasks 3 + 6 (no isinstance filter; `from_jsonable` reconstructs any SDK class) |
| Exceptions forwarded over SSE as serialized payloads | Task 2 + Task 3 (exception handler emits `exception` event) |
| Integration catches real SDK exception classes | Task 7 + Task 9 (`reconstruct_exception` + `except CLINotFoundError` blocks) |
| Exception attrs preserved (e.g. `ProcessError.exit_code`) | Task 2 + Task 7 (generic `vars(err)` capture + `setattr` restoration) |
| Add-on side traceback logged on integration side | Task 8 (sdk_stream logs `traceback` field before raising) |
| Nested `ContentBlock`s round-trip with correct types | Task 1 + Task 6 (recursive `_type` tagging + reconstruction) |
| SDK version skew tolerated (unknown fields dropped) | Task 6 (from_jsonable filters to known field names) |
| Unknown exception classes fall back to `ClaudeSDKError` | Task 7 (`issubclass` gate + composed-message fallback) |
| Transport errors stay separate from SDK errors | Task 9 (separate `except (aiohttp.ClientError, TimeoutError)` block) |
| Integration declares SDK as runtime requirement | Task 4 (manifest.json) |
| `_ERROR_MESSAGES` rekeyed to use SDK class names | Task 9 (new table structure) |
| Consumer code reads like direct SDK usage | Task 9 (match on SDK types, except on SDK exceptions) |

**Known limitations (documented, not blockers):**

- Python traceback objects are lost across the wire; only the formatted string is preserved. The integration logs the string before re-raising, so debuggability is preserved in HA logs.
- `raise X from Y` exception chains are not reconstructed. This is acceptable because our consumer catches by class, not by cause.
- Non-SDK exceptions from the add-on's own code (e.g. a bug that raises `ValueError`) become `ClaudeSDKError` on the integration side, with the original class name prepended to the message. They're still caught by the generic `except ClaudeSDKError:` handler.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-09-transparent-sdk-forwarding.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?

