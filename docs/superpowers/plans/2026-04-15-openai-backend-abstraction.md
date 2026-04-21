# OpenAI-compatible backend abstraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second agent backend (`openai-agents` SDK, any OpenAI-compatible endpoint) to the add-on so users can choose `claude` or `openai` mode in add-on options, with the integration consuming both backends' native event streams.

**Architecture:** Add-on gains a `Backend` Protocol with two implementations (`ClaudeBackend`, `OpenAIBackend`), chosen from add-on options at startup. Each backend streams its SDK's native events over SSE; the integration reconstructs both event families (`claude-agent-sdk` dataclasses + `openai-agents` Pydantic models) and routes to a per-backend delta adapter. HA tools are shared via a `tool_logic.py` core that both backend-specific wrappers call.

**Tech Stack:** Python 3.13, `claude-agent-sdk==0.1.56`, `openai-agents`, FastAPI, pytest/pytest-asyncio (strict), HomeAssistant `ConversationEntity`, Pydantic v2.

**Spec:** [`docs/superpowers/specs/2026-04-15-openai-backend-abstraction-design.md`](../specs/2026-04-15-openai-backend-abstraction-design.md)

---

## File Structure

**New add-on files:**
- `ha_claude_agent_addon/src/tool_logic.py` — pure async core logic + sentinel exceptions (shared between backends).
- `ha_claude_agent_addon/src/tools_claude.py` — replaces `tools.py`, `@tool`-decorated wrappers calling `tool_logic`.
- `ha_claude_agent_addon/src/tools_openai.py` — `@function_tool`-decorated wrappers calling `tool_logic`.
- `ha_claude_agent_addon/src/openai_events.py` — `OpenAIInitEvent`, `OpenAIResultEvent` Pydantic models.
- `ha_claude_agent_addon/src/backend.py` — `Backend` Protocol + `ClaudeBackend` + `OpenAIBackend`.

**Modified add-on files:**
- `ha_claude_agent_addon/src/serialization.py` — add Pydantic dispatch in `to_jsonable`.
- `ha_claude_agent_addon/src/server.py` — becomes thin FastAPI shell delegating to `app.state.backend`.
- `ha_claude_agent_addon/config.yaml` — new four-field schema, version bump to 0.7.0.
- `ha_claude_agent_addon/pyproject.toml` + `requirements.txt` + `uv.lock` — add `openai-agents`.

**New integration files:**
- `custom_components/ha_claude_agent/openai_events.py` — mirror of add-on's `openai_events.py` for reconstruction.

**Modified integration files:**
- `custom_components/ha_claude_agent/stream.py` — extend `from_jsonable` class-lookup to `agents` + `openai_events`.
- `custom_components/ha_claude_agent/conversation.py` — `_deltas_from_sdk_stream` becomes a router; add `_deltas_from_openai`; add `openai_*` keys to `_ERROR_MESSAGES`; catch new openai exception classes.
- `custom_components/ha_claude_agent/config_flow.py` — `SelectSelectorConfig(..., custom_value=True)` on the model field.
- `custom_components/ha_claude_agent/manifest.json` — add `openai-agents` requirement.

**New test files:**
- `tests/test_addon_tool_logic.py`
- `tests/test_addon_tools_claude.py`
- `tests/test_addon_tools_openai.py`
- `tests/test_addon_serialization_pydantic.py`
- `tests/test_addon_openai_backend.py`
- `tests/test_integration_openai_stream.py`
- `tests/test_integration_error_mapping_openai.py`

---

## Task 1: Add `openai-agents` dependency to both sides

**Files:**
- Modify: `ha_claude_agent_addon/pyproject.toml`
- Modify: `ha_claude_agent_addon/requirements.txt` (regenerated)
- Modify: `ha_claude_agent_addon/uv.lock` (regenerated)
- Modify: `custom_components/ha_claude_agent/manifest.json`
- Modify: `pyproject.toml` (root, dev deps)

No tests — pure dependency update.

- [ ] **Step 1: Add `openai-agents` to the add-on's `pyproject.toml`**

Edit `ha_claude_agent_addon/pyproject.toml` `dependencies`:

```toml
dependencies = [
    "claude-agent-sdk>=0.1.56",
    "openai-agents>=0.7.0",
    "aiohttp>=3.13.0",
    "fastapi>=0.135.0",
    "uvicorn[standard]>=0.44.0",
    "pydantic>=2.0.0",
]
```

- [ ] **Step 2: Regenerate the add-on's lockfile and requirements**

Run from `ha_claude_agent_addon/`:

```bash
cd ha_claude_agent_addon
uv lock
uv export --no-hashes --no-emit-project -o requirements.txt
cd ..
```

Expected: `uv.lock` and `requirements.txt` updated with `openai-agents` and its transitive deps.

- [ ] **Step 3: Add `openai-agents` to the integration's `manifest.json`**

Edit `custom_components/ha_claude_agent/manifest.json` `requirements`:

```json
"requirements": ["claude-agent-sdk==0.1.56", "openai-agents==0.7.0"],
```

Pin to a specific version — HA's `manifest.json` requires exact pins.

- [ ] **Step 4: Add `openai-agents` to root `pyproject.toml` dev deps**

Edit `pyproject.toml` `[project].dependencies`:

```toml
dependencies = [
    "homeassistant>=2025.4.0",
    "voluptuous",
    "pydantic>=2.0.0",
    "pytest",
    "mypy",
    "ruff",
    "pre-commit",
    "claude-agent-sdk>=0.1.56",
    "openai-agents>=0.7.0",
    "fastapi>=0.135.0",
]
```

Also add an mypy override for openai-agents:

```toml
[[tool.mypy.overrides]]
module = "agents.*"
ignore_missing_imports = true
```

- [ ] **Step 5: Sync the dev environment**

Run:

```bash
uv sync
```

Expected: `agents` package installable; `python -c "import agents; from agents import Agent, Runner"` succeeds.

- [ ] **Step 6: Run existing tests to confirm no regression**

Run:

```bash
uv run pytest tests/ -q
```

Expected: all existing tests pass.

- [ ] **Step 7: Commit**

```bash
git add ha_claude_agent_addon/pyproject.toml ha_claude_agent_addon/uv.lock ha_claude_agent_addon/requirements.txt custom_components/ha_claude_agent/manifest.json pyproject.toml
git commit -m "deps: add openai-agents SDK to add-on and integration"
```

---

## Task 2: Extract tool core logic into `tool_logic.py` (TDD)

**Files:**
- Create: `ha_claude_agent_addon/src/tool_logic.py`
- Create: `tests/test_addon_tool_logic.py`

Pure async functions + sentinel exceptions. No decorators yet — those come in later tasks.

- [ ] **Step 1: Write the failing test**

Create `tests/test_addon_tool_logic.py`:

```python
"""Unit tests for the shared HA tool core logic."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from tool_logic import (  # noqa: E402
    ToolBlocked,
    ToolInvalidArgs,
    ToolNotFound,
    call_service_logic,
    get_entity_state_logic,
    list_entities_logic,
)


@pytest.fixture
def ha_client():
    client = AsyncMock()
    client.call_service = AsyncMock()
    client.get_state = AsyncMock()
    client.get_states = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_call_service_blocks_unexposed_entity(ha_client):
    with pytest.raises(ToolBlocked):
        await call_service_logic(
            ha_client,
            exposed_set={"light.kitchen"},
            domain="light",
            service="turn_on",
            entity_id="light.bedroom",
            service_data="{}",
        )
    ha_client.call_service.assert_not_called()


@pytest.mark.asyncio
async def test_call_service_rejects_invalid_json(ha_client):
    with pytest.raises(ToolInvalidArgs):
        await call_service_logic(
            ha_client,
            exposed_set={"light.kitchen"},
            domain="light",
            service="turn_on",
            entity_id="light.kitchen",
            service_data="{not json",
        )


@pytest.mark.asyncio
async def test_call_service_success_returns_state_string(ha_client):
    ha_client.get_state.return_value = {"state": "on", "attributes": {}}

    result = await call_service_logic(
        ha_client,
        exposed_set={"light.kitchen"},
        domain="light",
        service="turn_on",
        entity_id="light.kitchen",
        service_data="{}",
    )

    ha_client.call_service.assert_awaited_once_with(
        "light", "turn_on", {"entity_id": "light.kitchen"}
    )
    assert "light.kitchen" in result
    assert "on" in result


@pytest.mark.asyncio
async def test_get_entity_state_blocks_unexposed(ha_client):
    with pytest.raises(ToolBlocked):
        await get_entity_state_logic(
            ha_client,
            exposed_set={"light.kitchen"},
            entity_id="sensor.unexposed",
        )


@pytest.mark.asyncio
async def test_get_entity_state_not_found(ha_client):
    ha_client.get_state.return_value = None
    with pytest.raises(ToolNotFound):
        await get_entity_state_logic(
            ha_client,
            exposed_set={"light.kitchen"},
            entity_id="light.kitchen",
        )


@pytest.mark.asyncio
async def test_get_entity_state_returns_json_text(ha_client):
    ha_client.get_state.return_value = {
        "state": "on",
        "attributes": {"friendly_name": "Kitchen Light", "brightness": 255},
    }

    result = await get_entity_state_logic(
        ha_client,
        exposed_set={"light.kitchen"},
        entity_id="light.kitchen",
    )

    assert '"entity_id": "light.kitchen"' in result
    assert '"state": "on"' in result
    assert '"friendly_name": "Kitchen Light"' in result


@pytest.mark.asyncio
async def test_list_entities_filters_by_domain_and_exposed(ha_client):
    ha_client.get_states.return_value = [
        {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
        {"entity_id": "light.bedroom", "state": "off", "attributes": {}},
        {"entity_id": "switch.kitchen", "state": "on", "attributes": {}},
    ]

    result = await list_entities_logic(
        ha_client,
        exposed_set={"light.kitchen", "switch.kitchen"},
        domain_filter="light",
    )

    assert "light.kitchen" in result
    assert "light.bedroom" not in result  # not exposed
    assert "switch.kitchen" not in result  # wrong domain
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_addon_tool_logic.py -v
```

Expected: FAIL with `ImportError: cannot import name 'ToolBlocked' from 'tool_logic'` (module doesn't exist).

- [ ] **Step 3: Implement `tool_logic.py`**

Create `ha_claude_agent_addon/src/tool_logic.py`:

```python
"""Pure async HA tool core logic shared between backends.

The two decorator wrappers (``tools_claude.py`` for ``@tool`` and
``tools_openai.py`` for ``@function_tool``) call these functions. Core
logic raises typed sentinels; each wrapper maps them into its SDK's
native error envelope.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ha_client import HAClient

_LOGGER = logging.getLogger(__name__)


class ToolBlocked(Exception):
    """Raised when an operation targets an entity not exposed to the agent."""


class ToolInvalidArgs(Exception):
    """Raised when tool arguments fail basic validation (e.g. bad JSON)."""


class ToolNotFound(Exception):
    """Raised when a referenced entity does not exist in HA."""


async def call_service_logic(
    ha_client: HAClient,
    exposed_set: set[str],
    domain: str,
    service: str,
    entity_id: str,
    service_data: str,
) -> str:
    """Call an HA service and return a human-readable confirmation string."""
    if entity_id not in exposed_set:
        _LOGGER.warning("Blocked service call on unexposed entity: %s", entity_id)
        raise ToolBlocked(
            f"Entity {entity_id} is not exposed to conversation agents."
        )

    try:
        extra_data = json.loads(service_data) if service_data else {}
    except json.JSONDecodeError as err:
        raise ToolInvalidArgs("service_data must be valid JSON.") from err

    payload = {"entity_id": entity_id, **extra_data}
    await ha_client.call_service(domain, service, payload)

    state = await ha_client.get_state(entity_id)
    state_str = state["state"] if state else "unknown"
    return (
        f"Called {domain}.{service} on {entity_id}. Current state: {state_str}"
    )


async def get_entity_state_logic(
    ha_client: HAClient,
    exposed_set: set[str],
    entity_id: str,
) -> str:
    """Return the current state of an entity as a JSON string."""
    if entity_id not in exposed_set:
        _LOGGER.warning("Blocked state read on unexposed entity: %s", entity_id)
        raise ToolBlocked(
            f"Entity {entity_id} is not exposed to conversation agents."
        )

    state = await ha_client.get_state(entity_id)
    if state is None:
        raise ToolNotFound(f"Entity {entity_id} not found.")

    attrs = dict(state.get("attributes", {}))
    info = {
        "entity_id": entity_id,
        "state": state["state"],
        "friendly_name": attrs.pop("friendly_name", entity_id),
        "attributes": attrs,
    }
    return json.dumps(info, default=str)


async def list_entities_logic(
    ha_client: HAClient,
    exposed_set: set[str],
    domain_filter: str,
) -> str:
    """Return a JSON list of exposed entities, optionally filtered by domain."""
    all_states = await ha_client.get_states()
    entities = []
    for state in all_states:
        eid = state["entity_id"]
        if eid not in exposed_set:
            continue
        if domain_filter and not eid.startswith(f"{domain_filter}."):
            continue
        entities.append(
            {
                "entity_id": eid,
                "name": state.get("attributes", {}).get("friendly_name", eid),
                "state": state["state"],
            }
        )
    return json.dumps(entities, default=str)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_addon_tool_logic.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add ha_claude_agent_addon/src/tool_logic.py tests/test_addon_tool_logic.py
git commit -m "feat(addon): extract HA tool core logic into shared module"
```

---

## Task 3: Refactor `tools.py` → `tools_claude.py` to use `tool_logic` (TDD)

**Files:**
- Rename: `ha_claude_agent_addon/src/tools.py` → `ha_claude_agent_addon/src/tools_claude.py`
- Create: `tests/test_addon_tools_claude.py`
- Modify: `ha_claude_agent_addon/src/server.py` (update import)

Keep behavior identical — just delegate to `tool_logic`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_addon_tools_claude.py`:

```python
"""Smoke tests for the Claude-flavored HA tool wrappers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from tools_claude import create_ha_tools_claude  # noqa: E402


@pytest.fixture
def ha_client():
    client = AsyncMock()
    client.call_service = AsyncMock()
    client.get_state = AsyncMock(return_value={"state": "on", "attributes": {}})
    client.get_states = AsyncMock(return_value=[])
    return client


def _find(tools, name):
    for t in tools:
        if getattr(t, "name", None) == name:
            return t
    raise AssertionError(f"tool {name!r} not found; have {[t.name for t in tools]}")


@pytest.mark.asyncio
async def test_factory_returns_three_tools(ha_client):
    tools = create_ha_tools_claude(ha_client, exposed_entities=["light.kitchen"])
    names = {t.name for t in tools}
    assert names == {"call_service", "get_entity_state", "list_entities"}


@pytest.mark.asyncio
async def test_call_service_success_envelope(ha_client):
    tools = create_ha_tools_claude(ha_client, exposed_entities=["light.kitchen"])
    call = _find(tools, "call_service")
    result = await call.handler(
        {
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.kitchen",
            "service_data": "{}",
        }
    )
    assert result["content"][0]["type"] == "text"
    assert "Called light.turn_on" in result["content"][0]["text"]
    assert "is_error" not in result


@pytest.mark.asyncio
async def test_call_service_blocks_unexposed_returns_error_envelope(ha_client):
    tools = create_ha_tools_claude(ha_client, exposed_entities=["light.kitchen"])
    call = _find(tools, "call_service")
    result = await call.handler(
        {
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.bedroom",
            "service_data": "{}",
        }
    )
    assert result.get("is_error") is True
    assert "not exposed" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_call_service_invalid_json_returns_error_envelope(ha_client):
    tools = create_ha_tools_claude(ha_client, exposed_entities=["light.kitchen"])
    call = _find(tools, "call_service")
    result = await call.handler(
        {
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.kitchen",
            "service_data": "{not json",
        }
    )
    assert result.get("is_error") is True
    assert "valid JSON" in result["content"][0]["text"]
```

Note: `@tool` from claude_agent_sdk returns an object with `.name` and `.handler` attributes. If the SDK exposes these differently in 0.1.56, adjust the assertions but keep the behavioral coverage. Check by reading `claude_agent_sdk`'s `tool` source — the decorator wraps the function in a class that stores name/description/schema.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_addon_tools_claude.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tools_claude'`.

- [ ] **Step 3: Create `tools_claude.py` by refactoring `tools.py`**

`git mv ha_claude_agent_addon/src/tools.py ha_claude_agent_addon/src/tools_claude.py`, then rewrite the file to delegate to `tool_logic`:

```python
"""Claude-flavored HA tool wrappers.

Thin ``@tool``-decorated closures that call ``tool_logic`` and wrap the
result in the ``{"content": [...], "is_error": bool}`` envelope the
claude-agent-sdk expects.
"""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_sdk import tool

from ha_client import HAClient
from tool_logic import (
    ToolBlocked,
    ToolInvalidArgs,
    ToolNotFound,
    call_service_logic,
    get_entity_state_logic,
    list_entities_logic,
)

_LOGGER = logging.getLogger(__name__)


def create_ha_tools_claude(
    ha_client: HAClient,
    exposed_entities: list[str],
) -> list:
    """Create Claude SDK tool instances that proxy to HA via ha_client."""
    exposed_set = set(exposed_entities)

    def _ok(text: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": text}]}

    def _err(text: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": text}], "is_error": True}

    @tool(
        "call_service",
        "Call a Home Assistant service to control a device. "
        "Examples: domain='light', service='turn_on', "
        "entity_id='light.living_room'. "
        "service_data is a JSON object string for additional "
        "parameters; pass '{}' if none needed.",
        {
            "domain": str,
            "service": str,
            "entity_id": str,
            "service_data": str,
        },
    )
    async def call_service(args: dict[str, Any]) -> dict[str, Any]:
        try:
            text = await call_service_logic(
                ha_client,
                exposed_set,
                domain=args["domain"],
                service=args["service"],
                entity_id=args["entity_id"],
                service_data=args.get("service_data", "{}"),
            )
            return _ok(text)
        except (ToolBlocked, ToolInvalidArgs) as err:
            return _err(str(err))
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("call_service failed: %s", err)
            return _err(f"Error calling service: {err}")

    @tool(
        "get_entity_state",
        "Get the current state and attributes of a Home Assistant entity.",
        {"entity_id": str},
    )
    async def get_entity_state(args: dict[str, Any]) -> dict[str, Any]:
        try:
            text = await get_entity_state_logic(
                ha_client, exposed_set, entity_id=args["entity_id"]
            )
            return _ok(text)
        except (ToolBlocked, ToolNotFound) as err:
            return _err(str(err))

    @tool(
        "list_entities",
        "List Home Assistant entities filtered by domain "
        "(e.g., 'light', 'switch', 'sensor'). Pass empty string "
        "to list all. Returns entity IDs, names, and states.",
        {"domain": str},
    )
    async def list_entities(args: dict[str, Any]) -> dict[str, Any]:
        text = await list_entities_logic(
            ha_client, exposed_set, domain_filter=args.get("domain", "")
        )
        return _ok(text)

    return [call_service, get_entity_state, list_entities]
```

- [ ] **Step 4: Update `server.py` import**

Edit `ha_claude_agent_addon/src/server.py`. Change:

```python
from tools import create_ha_tools
```

to:

```python
from tools_claude import create_ha_tools_claude
```

And update the call site in `_stream_query`:

```python
mcp_tools = create_ha_tools_claude(ha_client, body.exposed_entities)
```

- [ ] **Step 5: Run all addon tests to verify no regression**

Run:

```bash
uv run pytest tests/test_addon_tool_logic.py tests/test_addon_tools_claude.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add ha_claude_agent_addon/src/tools_claude.py ha_claude_agent_addon/src/server.py tests/test_addon_tools_claude.py
git rm ha_claude_agent_addon/src/tools.py
git commit -m "refactor(addon): rename tools.py -> tools_claude.py, delegate to tool_logic"
```

---

## Task 4: Add Pydantic serialization to `serialization.py` (TDD)

**Files:**
- Modify: `ha_claude_agent_addon/src/serialization.py`
- Create: `tests/test_addon_serialization_pydantic.py`

`to_jsonable` currently handles dataclasses only. Add a branch for `pydantic.BaseModel` instances so OpenAI-event types serialize with the same `_type` tag mechanism.

- [ ] **Step 1: Write the failing test**

Create `tests/test_addon_serialization_pydantic.py`:

```python
"""Round-trip tests for Pydantic model serialization."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
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
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_addon_serialization_pydantic.py -v
```

Expected: FAIL — `to_jsonable` currently treats `BaseModel` instances as leaves and `json.dumps(BaseModel(...))` raises `TypeError`.

- [ ] **Step 3: Add Pydantic dispatch to `to_jsonable`**

Edit `ha_claude_agent_addon/src/serialization.py`. At the top, add import:

```python
from pydantic import BaseModel
```

In `to_jsonable`, add a branch BEFORE the `if isinstance(obj, dict):` branch and AFTER the dataclass branch:

```python
    if isinstance(obj, BaseModel):
        cls_name = type(obj).__name__
        # model_dump(mode="json") handles nested pydantic models, datetimes,
        # UUID, etc. as JSON-native values, but loses the _type tag on nested
        # pydantic instances — so walk the dumped dict recursively to re-tag.
        dumped = obj.model_dump(mode="json")
        # Walk nested values to preserve _type tagging for any dataclass or
        # pydantic children the model_dump flattened. We re-run to_jsonable
        # on the original attribute values (not the dumped copies), so nested
        # models get their own _type injected.
        result: dict[str, Any] = {"_type": cls_name}
        for field_name in type(obj).model_fields:
            try:
                value = getattr(obj, field_name)
            except Exception as err:  # noqa: BLE001
                _log_field_drop(
                    cls_name,
                    field_name,
                    "Dropping field %s.%s: getattr raised %s: %s",
                    cls_name,
                    field_name,
                    type(err).__name__,
                    err,
                )
                continue
            try:
                result[field_name] = to_jsonable(value)
            except _UnserializableValue as err:
                _log_field_drop(
                    cls_name,
                    field_name,
                    "Dropping unserializable field %s.%s from wire payload: %s",
                    cls_name,
                    field_name,
                    err,
                )
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_addon_serialization_pydantic.py tests/test_addon_serialization.py -v
```

Expected: all tests pass (new Pydantic tests + existing dataclass tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add ha_claude_agent_addon/src/serialization.py tests/test_addon_serialization_pydantic.py
git commit -m "feat(addon): serialize Pydantic models with _type tag in to_jsonable"
```

---

## Task 5: Create `openai_events.py` Pydantic models (both sides)

**Files:**
- Create: `ha_claude_agent_addon/src/openai_events.py`
- Create: `custom_components/ha_claude_agent/openai_events.py` (mirror)

Two tiny Pydantic models. Must be byte-for-byte identical — add a sync test like `test_models_sync.py` does for `QueryRequest`.

- [ ] **Step 1: Write the sync-check test**

Create/append to `tests/test_models_sync.py` a second sync check:

```python
def test_openai_events_sync():
    """The add-on and integration copies of openai_events.py must match."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    addon_file = root / "ha_claude_agent_addon" / "src" / "openai_events.py"
    integration_file = (
        root / "custom_components" / "ha_claude_agent" / "openai_events.py"
    )
    assert addon_file.read_text(encoding="utf-8") == integration_file.read_text(
        encoding="utf-8"
    ), "openai_events.py copies have drifted — keep them identical"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_models_sync.py::test_openai_events_sync -v
```

Expected: FAIL — files don't exist yet.

- [ ] **Step 3: Create both copies of `openai_events.py`**

Identical content in both files:

`ha_claude_agent_addon/src/openai_events.py`:

```python
"""Shared Pydantic models for OpenAI-backend wire events.

This file is duplicated in custom_components/ha_claude_agent/openai_events.py.
The two copies MUST stay identical — see tests/test_models_sync.py.
"""

from __future__ import annotations

from pydantic import BaseModel


class OpenAIInitEvent(BaseModel):
    """Emitted once at stream start on the OpenAI backend.

    Carries the SQLiteSession key so the integration can store it in its
    existing session LRU cache the same way it consumes the Claude
    backend's SystemMessage(subtype="init") today.
    """

    session_id: str


class OpenAIResultEvent(BaseModel):
    """Emitted once at stream end on the OpenAI backend.

    Token counts are populated from the openai-agents Runner result. Cost
    tracking is not attempted on this backend — ``cost_usd`` stays 0 in
    the integration's ``_StreamResult`` because OpenAI-compatible
    endpoints return token counts only (pricing tables rot too fast to
    maintain).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
```

Copy the same content to `custom_components/ha_claude_agent/openai_events.py`.

- [ ] **Step 4: Run the sync test**

Run:

```bash
uv run pytest tests/test_models_sync.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ha_claude_agent_addon/src/openai_events.py custom_components/ha_claude_agent/openai_events.py tests/test_models_sync.py
git commit -m "feat: add OpenAIInitEvent and OpenAIResultEvent wire models"
```

---

## Task 6: Create `tools_openai.py` (@function_tool wrappers) (TDD)

**Files:**
- Create: `ha_claude_agent_addon/src/tools_openai.py`
- Create: `tests/test_addon_tools_openai.py`

`@function_tool` from `agents` reads the Python signature for schema generation. Explicit typed args; return str (success) or `"Error: {msg}"` (sentinel).

- [ ] **Step 1: Write the failing test**

Create `tests/test_addon_tools_openai.py`:

```python
"""Smoke tests for the OpenAI-flavored HA tool wrappers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from tools_openai import create_ha_tools_openai  # noqa: E402


@pytest.fixture
def ha_client():
    client = AsyncMock()
    client.call_service = AsyncMock()
    client.get_state = AsyncMock(return_value={"state": "on", "attributes": {}})
    client.get_states = AsyncMock(return_value=[])
    return client


def _tool_names(tools):
    # openai-agents FunctionTool exposes `.name` on the decorated wrapper.
    return {t.name for t in tools}


@pytest.mark.asyncio
async def test_factory_returns_three_named_tools(ha_client):
    tools = create_ha_tools_openai(ha_client, exposed_entities=["light.kitchen"])
    assert _tool_names(tools) == {
        "call_service",
        "get_entity_state",
        "list_entities",
    }


@pytest.mark.asyncio
async def test_call_service_returns_string_on_success(ha_client):
    tools = create_ha_tools_openai(ha_client, exposed_entities=["light.kitchen"])
    # Each openai-agents FunctionTool has an `on_invoke_tool` async callable
    # taking (run_context, arguments_json). We bypass the SDK wrapping by
    # calling the underlying async function captured in closure — the
    # factory exposes it as `tool.params_json_schema` is derived from the
    # python signature. Simplest: call the underlying function directly by
    # attribute: openai-agents attaches it as `.on_invoke_tool` or keeps the
    # original callable on the tool object. Consult the agents source for
    # the exact attribute; for testability we also export the raw async
    # funcs as `create_ha_tools_openai._raw` if needed (see implementation).
    raw = create_ha_tools_openai.__ha_raw__(ha_client, exposed_entities=["light.kitchen"])
    text = await raw["call_service"](
        domain="light",
        service="turn_on",
        entity_id="light.kitchen",
        service_data="{}",
    )
    assert "Called light.turn_on" in text
    assert not text.startswith("Error:")


@pytest.mark.asyncio
async def test_call_service_blocks_unexposed(ha_client):
    raw = create_ha_tools_openai.__ha_raw__(ha_client, exposed_entities=["light.kitchen"])
    text = await raw["call_service"](
        domain="light",
        service="turn_on",
        entity_id="light.bedroom",
        service_data="{}",
    )
    assert text.startswith("Error:")
    assert "not exposed" in text


@pytest.mark.asyncio
async def test_get_entity_state_not_found_returns_error_string(ha_client):
    ha_client.get_state.return_value = None
    raw = create_ha_tools_openai.__ha_raw__(ha_client, exposed_entities=["light.kitchen"])
    text = await raw["get_entity_state"](entity_id="light.kitchen")
    assert text.startswith("Error:")
    assert "not found" in text
```

Note the `__ha_raw__` helper — the implementation exposes the undecorated async callables as an alternate factory for test reach-through. This avoids wrestling with the openai-agents test harness for basic wrapper behavior.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_addon_tools_openai.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `tools_openai.py`**

Create `ha_claude_agent_addon/src/tools_openai.py`:

```python
"""OpenAI-flavored HA tool wrappers.

Thin ``@function_tool``-decorated closures that call ``tool_logic`` and
return plain strings (success) or ``"Error: ..."`` (sentinel), which is
the format openai-agents feeds back to the model.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from agents import function_tool

from ha_client import HAClient
from tool_logic import (
    ToolBlocked,
    ToolInvalidArgs,
    ToolNotFound,
    call_service_logic,
    get_entity_state_logic,
    list_entities_logic,
)

_LOGGER = logging.getLogger(__name__)


def _build_raw(
    ha_client: HAClient, exposed_set: set[str]
) -> dict[str, Callable[..., Awaitable[str]]]:
    """Return undecorated async callables — used by the factory to build
    both the ``@function_tool`` list and a test-visible raw map."""

    async def call_service(
        domain: str,
        service: str,
        entity_id: str,
        service_data: str,
    ) -> str:
        """Call a Home Assistant service to control a device.

        domain: HA service domain (e.g. 'light', 'switch').
        service: Service name (e.g. 'turn_on').
        entity_id: Target entity ID (e.g. 'light.living_room').
        service_data: JSON object string with extra parameters; pass '{}' if none.
        """
        try:
            return await call_service_logic(
                ha_client,
                exposed_set,
                domain=domain,
                service=service,
                entity_id=entity_id,
                service_data=service_data,
            )
        except (ToolBlocked, ToolInvalidArgs) as err:
            return f"Error: {err}"
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("call_service failed: %s", err)
            return f"Error: {err}"

    async def get_entity_state(entity_id: str) -> str:
        """Get the current state and attributes of a Home Assistant entity.

        entity_id: Target entity ID (e.g. 'sensor.temperature').
        """
        try:
            return await get_entity_state_logic(
                ha_client, exposed_set, entity_id=entity_id
            )
        except (ToolBlocked, ToolNotFound) as err:
            return f"Error: {err}"

    async def list_entities(domain: str) -> str:
        """List Home Assistant entities filtered by domain.

        domain: Domain filter (e.g. 'light'). Pass empty string to list all.
        Returns entity IDs, names, and states as a JSON string.
        """
        return await list_entities_logic(
            ha_client, exposed_set, domain_filter=domain
        )

    return {
        "call_service": call_service,
        "get_entity_state": get_entity_state,
        "list_entities": list_entities,
    }


def create_ha_tools_openai(
    ha_client: HAClient,
    exposed_entities: list[str],
) -> list:
    """Create openai-agents FunctionTool instances that proxy to HA."""
    exposed_set = set(exposed_entities)
    raw = _build_raw(ha_client, exposed_set)
    return [function_tool(fn) for fn in raw.values()]


# Test reach-through: returns the undecorated async callables so unit tests
# can invoke the wrappers without the openai-agents runtime harness.
def __ha_raw__(
    ha_client: HAClient, exposed_entities: list[str]
) -> dict[str, Callable[..., Awaitable[str]]]:
    return _build_raw(ha_client, set(exposed_entities))


create_ha_tools_openai.__ha_raw__ = __ha_raw__  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_addon_tools_openai.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add ha_claude_agent_addon/src/tools_openai.py tests/test_addon_tools_openai.py
git commit -m "feat(addon): add OpenAI-flavored HA tool wrappers"
```

---

## Task 7: Create `backend.py` with `Backend` Protocol + `ClaudeBackend` (TDD)

**Files:**
- Create: `ha_claude_agent_addon/src/backend.py`
- Modify: `ha_claude_agent_addon/src/server.py` (move logic into ClaudeBackend)

Extract the existing `_stream_query` body (the Claude path) into `ClaudeBackend.stream_query`. Behavior-preserving refactor.

- [ ] **Step 1: Write the failing test**

Create `tests/test_addon_backend.py`:

```python
"""Tests for the Backend Protocol + ClaudeBackend refactor."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from backend import Backend, ClaudeBackend  # noqa: E402
from models import QueryRequest  # noqa: E402


def test_claude_backend_has_name():
    b = ClaudeBackend(auth_env={"ANTHROPIC_API_KEY": "sk-test"})
    assert b.name == "claude"
    assert isinstance(b, Backend)


@pytest.mark.asyncio
async def test_claude_backend_streams_query_events():
    """Smoke test: ClaudeBackend.stream_query yields SSE-formatted strings
    when the underlying SDK query yields a known message."""
    from claude_agent_sdk import SystemMessage

    req = QueryRequest(
        prompt="hello",
        model="claude-sonnet-4-6",
        system_prompt="be helpful",
        max_turns=1,
        exposed_entities=[],
    )
    ha_client = AsyncMock()

    async def fake_query(**kwargs):
        yield SystemMessage(subtype="init", data={"session_id": "sess-1"})

    with patch("backend.query", side_effect=lambda **kw: fake_query(**kw)):
        events = []
        async for event_str in ClaudeBackend(
            auth_env={"ANTHROPIC_API_KEY": "sk-test"}
        ).stream_query(req, ha_client):
            events.append(event_str)

    assert any("SystemMessage" in e for e in events)
    assert any("sess-1" in e for e in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_addon_backend.py -v
```

Expected: FAIL — `backend` module doesn't exist.

- [ ] **Step 3: Create `backend.py` with Protocol + ClaudeBackend**

Create `ha_claude_agent_addon/src/backend.py`:

```python
"""Backend abstraction for the HA Claude Agent add-on.

Two implementations live here: ``ClaudeBackend`` wraps claude-agent-sdk
(preserves the existing behavior from server._stream_query); ``OpenAIBackend``
wraps openai-agents (added in a later task).

Both backends yield pre-formatted SSE strings — the FastAPI shell in
``server.py`` just pipes them into a StreamingResponse.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any, Protocol

from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    query,
)

from ha_client import HAClient
from models import QueryRequest
from serialization import exception_to_dict, to_jsonable
from tools_claude import create_ha_tools_claude

_LOGGER = logging.getLogger(__name__)

MCP_SERVER_NAME = "homeassistant"


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


class Backend(Protocol):
    """Interface each backend implements."""

    name: str  # "claude" | "openai"

    async def stream_query(
        self,
        req: QueryRequest,
        ha_client: HAClient,
    ) -> AsyncGenerator[str, None]:
        ...


class ClaudeBackend:
    """Backend that wraps claude-agent-sdk. Behavior-identical to the
    pre-refactor ``server._stream_query``."""

    name = "claude"

    def __init__(self, auth_env: dict[str, str]) -> None:
        self._auth_env = auth_env

    async def stream_query(
        self,
        req: QueryRequest,
        ha_client: HAClient,
    ) -> AsyncGenerator[str, None]:
        _LOGGER.info(
            "Claude query: model=%s, effort=%s, max_turns=%d, resume=%s",
            req.model,
            req.effort,
            req.max_turns,
            req.session_id is not None,
        )

        try:
            mcp_tools = create_ha_tools_claude(ha_client, req.exposed_entities)
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
                model=req.model,
                system_prompt=req.system_prompt,
                mcp_servers={MCP_SERVER_NAME: mcp_server},
                allowed_tools=allowed_tools,
                max_turns=req.max_turns,
                env=self._auth_env,
                permission_mode="dontAsk",
                effort=req.effort,
                include_partial_messages=True,
                stderr=lambda line: _LOGGER.warning("CLI stderr: %s", line),
            )
            if req.session_id:
                options.resume = req.session_id

            async for message in query(prompt=req.prompt, options=options):
                yield _sse_event(type(message).__name__, to_jsonable(message))

        except GeneratorExit:
            raise
        except asyncio.CancelledError:
            raise
        except BaseException as err:  # noqa: BLE001
            _LOGGER.exception("Claude query failed")
            yield _sse_event("exception", exception_to_dict(err))
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_addon_backend.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ha_claude_agent_addon/src/backend.py tests/test_addon_backend.py
git commit -m "feat(addon): introduce Backend Protocol and ClaudeBackend"
```

---

## Task 8: Implement `OpenAIBackend` (TDD)

**Files:**
- Modify: `ha_claude_agent_addon/src/backend.py`
- Create: `tests/test_addon_openai_backend.py`

Wraps openai-agents, emits Init+Result events, uses SQLiteSession keyed by session_id.

- [ ] **Step 1: Write the failing test**

Create `tests/test_addon_openai_backend.py`:

```python
"""Tests for OpenAIBackend."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ADDON_SRC = Path(__file__).resolve().parent.parent / "ha_claude_agent_addon" / "src"
if str(ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(ADDON_SRC))

from backend import OpenAIBackend  # noqa: E402
from models import QueryRequest  # noqa: E402


def _parse_sse_events(chunks: list[str]) -> list[tuple[str, dict]]:
    """Parse a list of SSE-string chunks into [(event_type, data_dict)]."""
    out = []
    for chunk in chunks:
        lines = chunk.strip().split("\n")
        event_type = lines[0].removeprefix("event: ").strip()
        data_str = lines[1].removeprefix("data: ").strip()
        out.append((event_type, json.loads(data_str)))
    return out


@pytest.fixture
def fake_run_streamed():
    """Patches agents.Runner.run_streamed to yield canned events."""

    class _FakeResult:
        def __init__(self, events):
            self._events = events
            self.final_output = "done"
            self.new_items = []
            self.raw_responses = []
            self.usage = SimpleNamespace(input_tokens=11, output_tokens=22)

        async def stream_events(self):
            for e in self._events:
                yield e

    def _make(events):
        return _FakeResult(events)

    return _make


@pytest.mark.asyncio
async def test_openai_backend_emits_init_and_result(fake_run_streamed, tmp_path):
    req = QueryRequest(
        prompt="hello",
        model="gemini-2.0-flash",
        system_prompt="be helpful",
        max_turns=1,
        session_id="sess-xyz",
        exposed_entities=[],
    )
    ha_client = AsyncMock()

    fake_result = fake_run_streamed([])

    with (
        patch("backend.Runner") as Runner,
        patch("backend.SQLiteSession") as SQLiteSession,
        patch("backend.AsyncOpenAI"),
        patch("backend.set_default_openai_client"),
    ):
        Runner.run_streamed = MagicMock(return_value=fake_result)
        SQLiteSession.return_value = MagicMock()

        chunks = []
        async for c in OpenAIBackend(
            api_key="k",
            base_url="https://example/v1",
            sessions_db_path=str(tmp_path / "sessions.db"),
        ).stream_query(req, ha_client):
            chunks.append(c)

    events = _parse_sse_events(chunks)
    # First event is init, last is result
    assert events[0][0] == "OpenAIInitEvent"
    assert events[0][1]["session_id"] == "sess-xyz"
    assert events[-1][0] == "OpenAIResultEvent"
    assert events[-1][1]["input_tokens"] == 11
    assert events[-1][1]["output_tokens"] == 22


@pytest.mark.asyncio
async def test_openai_backend_generates_uuid_when_no_session_id(
    fake_run_streamed, tmp_path
):
    req = QueryRequest(
        prompt="hello",
        model="gpt-4.1",
        system_prompt="be helpful",
        max_turns=1,
        session_id=None,
        exposed_entities=[],
    )
    ha_client = AsyncMock()

    fake_result = fake_run_streamed([])

    with (
        patch("backend.Runner") as Runner,
        patch("backend.SQLiteSession") as SQLiteSession,
        patch("backend.AsyncOpenAI"),
        patch("backend.set_default_openai_client"),
    ):
        Runner.run_streamed = MagicMock(return_value=fake_result)
        SQLiteSession.return_value = MagicMock()

        chunks = []
        async for c in OpenAIBackend(
            api_key="k",
            base_url="https://example/v1",
            sessions_db_path=str(tmp_path / "sessions.db"),
        ).stream_query(req, ha_client):
            chunks.append(c)

    events = _parse_sse_events(chunks)
    assert events[0][0] == "OpenAIInitEvent"
    sid = events[0][1]["session_id"]
    assert isinstance(sid, str)
    assert len(sid) >= 16  # uuid hex is 32 chars


@pytest.mark.asyncio
async def test_openai_backend_emits_exception_event_on_runner_failure(tmp_path):
    req = QueryRequest(
        prompt="hello",
        model="gpt-4.1",
        system_prompt="be helpful",
        max_turns=1,
        session_id="s",
        exposed_entities=[],
    )
    ha_client = AsyncMock()

    class _Boom(Exception):
        pass

    def _raise(*a, **kw):
        raise _Boom("boom")

    with (
        patch("backend.Runner") as Runner,
        patch("backend.SQLiteSession"),
        patch("backend.AsyncOpenAI"),
        patch("backend.set_default_openai_client"),
    ):
        Runner.run_streamed = MagicMock(side_effect=_raise)

        chunks = []
        async for c in OpenAIBackend(
            api_key="k",
            base_url="https://example/v1",
            sessions_db_path=str(tmp_path / "sessions.db"),
        ).stream_query(req, ha_client):
            chunks.append(c)

    events = _parse_sse_events(chunks)
    kinds = [e[0] for e in events]
    assert "exception" in kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_addon_openai_backend.py -v
```

Expected: FAIL — `OpenAIBackend` not defined.

- [ ] **Step 3: Implement `OpenAIBackend` in `backend.py`**

Append to `ha_claude_agent_addon/src/backend.py`:

```python
import uuid

from agents import Agent, Runner, set_default_openai_client
from agents.extensions.memory import SQLiteSession
from openai import AsyncOpenAI

from openai_events import OpenAIInitEvent, OpenAIResultEvent
from tools_openai import create_ha_tools_openai


class OpenAIBackend:
    """Backend that wraps openai-agents for any OpenAI-compatible endpoint."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        sessions_db_path: str = "/data/sessions.db",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._sessions_db_path = sessions_db_path

    async def stream_query(
        self,
        req: QueryRequest,
        ha_client: HAClient,
    ) -> AsyncGenerator[str, None]:
        session_id = req.session_id or uuid.uuid4().hex
        _LOGGER.info(
            "OpenAI query: model=%s, effort=%s, max_turns=%d, session=%s, resumed=%s",
            req.model,
            req.effort,
            req.max_turns,
            session_id,
            req.session_id is not None,
        )

        # Leading init event — integration picks this up into its session cache.
        yield _sse_event(
            "OpenAIInitEvent",
            to_jsonable(OpenAIInitEvent(session_id=session_id)),
        )

        error_text: str | None = None
        input_tokens = 0
        output_tokens = 0

        try:
            client = AsyncOpenAI(base_url=self._base_url, api_key=self._api_key)
            set_default_openai_client(client)

            tools = create_ha_tools_openai(ha_client, req.exposed_entities)
            agent = Agent(
                name="ha_assistant",
                instructions=req.system_prompt,
                model=req.model,
                tools=tools,
            )
            session = SQLiteSession(
                session_id=session_id,
                db_path=self._sessions_db_path,
            )

            result = Runner.run_streamed(
                agent,
                req.prompt,
                session=session,
                max_turns=req.max_turns,
            )
            async for event in result.stream_events():
                yield _sse_event(type(event).__name__, to_jsonable(event))

            usage = getattr(result, "usage", None)
            if usage is not None:
                input_tokens = getattr(usage, "input_tokens", 0) or 0
                output_tokens = getattr(usage, "output_tokens", 0) or 0

        except GeneratorExit:
            raise
        except asyncio.CancelledError:
            raise
        except BaseException as err:  # noqa: BLE001
            _LOGGER.exception("OpenAI query failed")
            error_text = f"{type(err).__name__}: {err}"
            yield _sse_event("exception", exception_to_dict(err))
            # Fall through to emit the terminal result event anyway — the
            # integration uses ResultEvent presence as the "stream ended
            # cleanly enough to record usage" signal.

        yield _sse_event(
            "OpenAIResultEvent",
            to_jsonable(
                OpenAIResultEvent(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    error=error_text,
                )
            ),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_addon_openai_backend.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add ha_claude_agent_addon/src/backend.py tests/test_addon_openai_backend.py
git commit -m "feat(addon): implement OpenAIBackend with SQLiteSession and init/result events"
```

---

## Task 9: Refactor `server.py` to thin FastAPI shell + startup backend selection

**Files:**
- Modify: `ha_claude_agent_addon/src/server.py`

Move the inline `_stream_query` logic out (it's now in `backend.ClaudeBackend.stream_query`). Read options, pick a backend, validate required fields.

- [ ] **Step 1: Write the startup-validation test**

Append to `tests/test_addon_backend.py`:

```python
from unittest.mock import patch  # already imported above


@patch("server._read_addon_options")
def test_server_picks_claude_backend_when_configured(mock_options):
    import server

    mock_options.return_value = {
        "backend": "claude",
        "claude_auth_token": "sk-ant-api-test",
        "openai_api_key": "",
        "openai_base_url": "",
    }
    backend = server._select_backend(mock_options.return_value)
    assert backend.name == "claude"


@patch("server._read_addon_options")
def test_server_picks_openai_backend_when_configured(mock_options):
    import server

    mock_options.return_value = {
        "backend": "openai",
        "claude_auth_token": "",
        "openai_api_key": "sk-test",
        "openai_base_url": "https://example/v1",
    }
    backend = server._select_backend(mock_options.return_value)
    assert backend.name == "openai"


@patch("server._read_addon_options")
def test_server_raises_when_claude_token_missing(mock_options):
    import server

    mock_options.return_value = {
        "backend": "claude",
        "claude_auth_token": "",
    }
    with pytest.raises(RuntimeError, match="claude_auth_token"):
        server._select_backend(mock_options.return_value)


@patch("server._read_addon_options")
def test_server_raises_when_openai_key_missing(mock_options):
    import server

    mock_options.return_value = {
        "backend": "openai",
        "openai_api_key": "",
        "openai_base_url": "https://example/v1",
    }
    with pytest.raises(RuntimeError, match="openai_api_key"):
        server._select_backend(mock_options.return_value)


@patch("server._read_addon_options")
def test_server_raises_when_openai_base_url_missing(mock_options):
    import server

    mock_options.return_value = {
        "backend": "openai",
        "openai_api_key": "sk-test",
        "openai_base_url": "",
    }
    with pytest.raises(RuntimeError, match="openai_base_url"):
        server._select_backend(mock_options.return_value)


@patch("server._read_addon_options")
def test_server_accepts_legacy_auth_token_for_claude(mock_options):
    import server

    mock_options.return_value = {
        "backend": "claude",
        "auth_token": "sk-ant-legacy",  # old field, no claude_auth_token
        "claude_auth_token": "",
    }
    backend = server._select_backend(mock_options.return_value)
    assert backend.name == "claude"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_addon_backend.py -v
```

Expected: failures — `server._select_backend` doesn't exist yet.

- [ ] **Step 3: Rewrite `server.py`**

Replace `ha_claude_agent_addon/src/server.py` with:

```python
"""HTTP server for the HA Claude Agent add-on.

Reads options at startup, selects a Backend, and routes /query to it. The
backend yields SSE-formatted strings; this shell does no query logic.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from backend import Backend, ClaudeBackend, OpenAIBackend
from ha_client import HAClient
from models import QueryRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
_LOGGER = logging.getLogger(__name__)

ADDON_OPTIONS_PATH = "/data/options.json"
DEFAULT_PORT = 8099
API_VERSION = 3  # bumped: /health now exposes `backend`


def _read_addon_options() -> dict:
    try:
        with open(ADDON_OPTIONS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as err:
        _LOGGER.error("Cannot read add-on options: %s", err)
        return {}


def _build_claude_auth_env(token: str) -> dict[str, str]:
    if not token:
        return {}
    if token.startswith("sk-ant-api"):
        return {"ANTHROPIC_API_KEY": token}
    return {"CLAUDE_CODE_OAUTH_TOKEN": token}


def _select_backend(options: dict) -> Backend:
    backend_name = (options.get("backend") or "claude").strip().lower()

    if backend_name == "claude":
        token = options.get("claude_auth_token") or options.get("auth_token") or ""
        if not token:
            raise RuntimeError(
                "Missing claude_auth_token — required when backend=claude."
            )
        if options.get("auth_token") and not options.get("claude_auth_token"):
            _LOGGER.warning(
                "Using legacy `auth_token` option; rename to `claude_auth_token` "
                "before the next minor release."
            )
        return ClaudeBackend(auth_env=_build_claude_auth_env(token))

    if backend_name == "openai":
        api_key = options.get("openai_api_key") or ""
        base_url = options.get("openai_base_url") or ""
        if not api_key:
            raise RuntimeError(
                "Missing openai_api_key — required when backend=openai."
            )
        if not base_url:
            raise RuntimeError(
                "Missing openai_base_url — required when backend=openai. "
                "Example: https://generativelanguage.googleapis.com/v1beta/openai/"
            )
        return OpenAIBackend(api_key=api_key, base_url=base_url)

    raise RuntimeError(
        f"Unknown backend {backend_name!r}. Must be 'claude' or 'openai'."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    options = _read_addon_options()
    app.state.backend = _select_backend(options)
    _LOGGER.info("Selected backend: %s", app.state.backend.name)

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        _LOGGER.error(
            "SUPERVISOR_TOKEN not set — HA REST API calls will fail. "
            "Is the add-on running inside the Supervisor?"
        )
        supervisor_token = ""

    app.state.ha_client = HAClient(
        base_url="http://supervisor/core",
        token=supervisor_token,
    )

    yield

    await app.state.ha_client.close()


app = FastAPI(title="HA Claude Agent Add-on", lifespan=lifespan)


@app.get("/health")
async def health():
    backend_name = getattr(getattr(app.state, "backend", None), "name", "unknown")
    return {"status": "ok", "api_version": API_VERSION, "backend": backend_name}


@app.post("/query")
async def handle_query(body: QueryRequest) -> StreamingResponse:
    backend: Backend = app.state.backend
    ha_client: HAClient = app.state.ha_client
    return StreamingResponse(
        backend.stream_query(body, ha_client),
        media_type="text/event-stream",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    _LOGGER.info("Starting HA Claude Agent add-on on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run pytest tests/test_addon_backend.py tests/test_addon_tool_logic.py tests/test_addon_tools_claude.py tests/test_addon_tools_openai.py tests/test_addon_openai_backend.py tests/test_addon_serialization.py tests/test_addon_serialization_pydantic.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ha_claude_agent_addon/src/server.py tests/test_addon_backend.py
git commit -m "refactor(addon): server.py becomes thin shell with backend selection"
```

---

## Task 10: Update add-on `config.yaml` schema + version bump

**Files:**
- Modify: `ha_claude_agent_addon/config.yaml`

No tests — this is a Supervisor-side schema declaration. Validation logic is covered by Task 9's tests.

- [ ] **Step 1: Update `config.yaml`**

Replace `ha_claude_agent_addon/config.yaml` with:

```yaml
name: "HA Claude Agent"
version: "0.7.0"
slug: "ha_claude_agent"
description: "Claude Code CLI / OpenAI-compatible agent runner for the HA Claude Agent integration"
url: "https://github.com/Marsunpaisti/HAClaudeAgent"
arch:
  - aarch64
  - amd64
homeassistant_api: true
discovery:
  - ha_claude_agent
ports:
  8099/tcp: null
ports_description:
  8099/tcp: "Internal API (not exposed to host)"
options:
  backend: "claude"
  claude_auth_token: ""
  openai_api_key: ""
  openai_base_url: ""
schema:
  backend: "list(claude|openai)"
  claude_auth_token: "password?"
  openai_api_key: "password?"
  openai_base_url: "url?"
init: false
map:
  - addon_config:rw
```

Note: all three credential fields are made optional at schema level (`?` suffix) so users can leave the inactive backend's fields empty. Runtime validation in `_select_backend` enforces that the fields for the selected backend are populated.

- [ ] **Step 2: Commit**

```bash
git add ha_claude_agent_addon/config.yaml
git commit -m "feat(addon): add backend switch and openai_* options; version 0.7.0"
```

---

## Task 11: Integration — extend `from_jsonable` to look up openai-agents classes (TDD)

**Files:**
- Modify: `custom_components/ha_claude_agent/stream.py`
- Create: `tests/test_integration_openai_stream.py`

Today `from_jsonable` looks up class names in `claude_agent_sdk`. Extend to also check `agents` (the openai-agents public namespace) and the local `openai_events` module.

- [ ] **Step 1: Write the failing test**

Create `tests/test_integration_openai_stream.py`:

```python
"""Tests for stream.py class lookup extension for openai-agents types."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.ha_claude_agent.openai_events import (  # noqa: E402
    OpenAIInitEvent,
    OpenAIResultEvent,
)
from custom_components.ha_claude_agent.stream import from_jsonable  # noqa: E402


def test_openai_init_event_reconstructs_from_wire_payload():
    payload = {"_type": "OpenAIInitEvent", "session_id": "sess-abc"}
    result = from_jsonable(payload)
    assert isinstance(result, OpenAIInitEvent)
    assert result.session_id == "sess-abc"


def test_openai_result_event_reconstructs_with_defaults():
    payload = {"_type": "OpenAIResultEvent", "input_tokens": 10, "output_tokens": 20}
    result = from_jsonable(payload)
    assert isinstance(result, OpenAIResultEvent)
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    assert result.error is None


def test_claude_types_still_reconstruct():
    # Sanity check: claude-agent-sdk lookup still works.
    from claude_agent_sdk import SystemMessage

    payload = {"_type": "SystemMessage", "subtype": "init", "data": {"x": 1}}
    result = from_jsonable(payload)
    assert isinstance(result, SystemMessage)
    assert result.subtype == "init"


def test_unknown_type_falls_back_to_raw_dict():
    payload = {"_type": "NonexistentClass", "foo": "bar"}
    result = from_jsonable(payload)
    assert result == {"foo": "bar"}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_integration_openai_stream.py -v
```

Expected: `OpenAIInitEvent` reconstruction fails — `from_jsonable` doesn't know where to find it.

- [ ] **Step 3: Extend `from_jsonable` class lookup**

Edit `custom_components/ha_claude_agent/stream.py`. At top, add:

```python
import importlib
from pydantic import BaseModel as _PydanticBaseModel

from . import openai_events as _openai_events_module

try:
    import agents as _agents_module
except ImportError:
    _agents_module = None
```

Replace the class-lookup block in `from_jsonable` (the part currently doing `getattr(claude_agent_sdk, cls_name, None)`) with:

```python
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
            or (issubclass(cls, _PydanticBaseModel))
        ):
            return cls
    return None
```

Update the body of `from_jsonable` to use it, AND to handle both dataclass and pydantic reconstruction:

```python
    if isinstance(obj, dict):
        if "_type" in obj:
            cls_name = obj["_type"]
            fields_payload = {
                k: from_jsonable(v) for k, v in obj.items() if k != "_type"
            }
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
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run pytest tests/test_integration_openai_stream.py tests/test_integration_stream.py -v
```

Expected: new tests pass + existing stream tests pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_claude_agent/stream.py tests/test_integration_openai_stream.py
git commit -m "feat(integration): extend from_jsonable to reconstruct openai-agents types"
```

---

## Task 12: Integration — add `_deltas_from_openai` router in `conversation.py` (TDD)

**Files:**
- Modify: `custom_components/ha_claude_agent/conversation.py`
- Modify: `tests/test_integration_openai_stream.py` (append)

Split `_deltas_from_sdk_stream` into a router that peeks the first typed message and dispatches to `_deltas_from_claude` (existing logic, renamed) or `_deltas_from_openai` (new).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_integration_openai_stream.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from custom_components.ha_claude_agent.conversation import (
    _deltas_from_openai,
    _StreamResult,
)
from custom_components.ha_claude_agent.openai_events import (
    OpenAIInitEvent,
    OpenAIResultEvent,
)


async def _mock_sdk_stream(items):
    async def gen():
        for item in items:
            yield item

    return gen()


@pytest.mark.asyncio
async def test_deltas_from_openai_populates_session_id_from_init():
    state = _StreamResult()
    items = [
        OpenAIInitEvent(session_id="sess-hello"),
        OpenAIResultEvent(input_tokens=5, output_tokens=7),
    ]
    resp = MagicMock()

    # _deltas_from_openai consumes an already-started async iterator of
    # reconstructed items from sdk_stream. Build a small fake iterator.
    async def fake_sdk_stream(_):
        for i in items:
            yield i

    out = []
    async for delta in _deltas_from_openai(resp, state, sdk_stream=fake_sdk_stream):
        out.append(delta)

    assert state.session_id == "sess-hello"
    assert state.usage_dict == {"input_tokens": 5, "output_tokens": 7}
    # cost_usd stays None — integration sets it to 0.0 downstream when
    # dispatching to sensor, but _StreamResult keeps None to indicate
    # "no Claude ResultMessage was seen."
    assert state.cost_usd is None


@pytest.mark.asyncio
async def test_deltas_from_openai_surfaces_error_from_result():
    state = _StreamResult()
    items = [
        OpenAIInitEvent(session_id="s"),
        OpenAIResultEvent(error="boom"),
    ]
    resp = MagicMock()

    async def fake_sdk_stream(_):
        for i in items:
            yield i

    async for _ in _deltas_from_openai(resp, state, sdk_stream=fake_sdk_stream):
        pass

    assert state.assistant_error == "boom"
```

Accept that the router/peek mechanics are exercised via the Claude path's existing tests; this task's new tests focus on `_deltas_from_openai` behavior directly.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_integration_openai_stream.py -v
```

Expected: FAIL — `_deltas_from_openai` doesn't exist.

- [ ] **Step 3: Refactor `_deltas_from_sdk_stream` into a router**

Edit `custom_components/ha_claude_agent/conversation.py`:

Add imports at top:

```python
from .openai_events import OpenAIInitEvent, OpenAIResultEvent

# openai-agents event types used for matching; import lazily-guarded so a
# missing openai-agents install (unlikely in practice, but possible during
# testing) degrades gracefully to Claude-only behavior.
try:
    from agents import RawResponsesStreamEvent, RunItemStreamEvent
except ImportError:
    RawResponsesStreamEvent = None  # type: ignore[assignment]
    RunItemStreamEvent = None  # type: ignore[assignment]
```

Replace the existing `_deltas_from_sdk_stream` function with the router + two paths. Rename the current body to `_deltas_from_claude` (preserving its behavior exactly); add `_deltas_from_openai`:

```python
async def _deltas_from_sdk_stream(
    resp: aiohttp.ClientResponse,
    state: _StreamResult,
) -> AsyncIterator[AssistantContentDeltaDict]:
    """Route to the right per-backend delta adapter.

    Peeks the first typed message to decide which backend's event model
    is in play, then dispatches. Both adapters share ``_StreamResult``.
    """
    stream = sdk_stream(resp)
    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        return

    if _is_openai_event(first):
        async for delta in _deltas_from_openai_with_first(first, stream, state):
            yield delta
    else:
        async for delta in _deltas_from_claude_with_first(first, stream, state):
            yield delta


def _is_openai_event(obj: object) -> bool:
    """Return True if `obj` is one of the openai-agents wire types."""
    if isinstance(obj, (OpenAIInitEvent, OpenAIResultEvent)):
        return True
    if RawResponsesStreamEvent is not None and isinstance(obj, RawResponsesStreamEvent):
        return True
    if RunItemStreamEvent is not None and isinstance(obj, RunItemStreamEvent):
        return True
    return False


async def _deltas_from_claude_with_first(
    first,
    stream,
    state: _StreamResult,
) -> AsyncIterator[AssistantContentDeltaDict]:
    """Claude-path delta adapter. Body is the pre-refactor
    ``_deltas_from_sdk_stream`` logic, with the iteration sourced from
    ``chain([first], stream)`` instead of iterating ``sdk_stream(resp)``
    directly."""
    processor = StreamingFilterProcessor([SourcesFilter()])
    role_yielded = False

    async def _iter():
        yield first
        async for m in stream:
            yield m

    async for message in _iter():
        match message:
            case StreamEvent(event=ev):
                delta = _delta_from_anthropic_event(ev)
                if delta is None:
                    continue
                if "content" in delta:
                    filtered = processor.feed(delta["content"])
                    if not filtered:
                        continue
                    delta = {"content": filtered}
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
                usage=usage_dict,
            ):
                state.session_id = sid or state.session_id
                state.cost_usd = cost
                state.num_turns = turns
                state.usage_dict = usage_dict
                if subtype != "success":
                    state.result_error_subtype = subtype

            case AssistantMessage(error=error) if error is not None:
                state.assistant_error = error

            case RateLimitEvent(rate_limit_info=info):
                level = (
                    logging.WARNING if info.status != "allowed" else logging.DEBUG
                )
                _LOGGER.log(
                    level,
                    "Claude rate limit: status=%s type=%s utilization=%s",
                    info.status,
                    info.rate_limit_type,
                    info.utilization,
                )

            case _:
                pass

    final = processor.flush()
    if final:
        if not role_yielded:
            yield {"role": "assistant"}
            role_yielded = True
        yield {"content": final}


async def _deltas_from_openai_with_first(
    first,
    stream,
    state: _StreamResult,
) -> AsyncIterator[AssistantContentDeltaDict]:
    """OpenAI-path delta adapter."""
    async def _iter():
        yield first
        async for m in stream:
            yield m

    async for this_wrapper in _deltas_from_openai(
        resp=None,
        state=state,
        sdk_stream=lambda _: _iter(),
    ):
        yield this_wrapper


async def _deltas_from_openai(
    resp,
    state: _StreamResult,
    *,
    sdk_stream=sdk_stream,  # injectable for tests
) -> AsyncIterator[AssistantContentDeltaDict]:
    """Map openai-agents events to ChatLog deltas + populate _StreamResult."""
    role_yielded = False

    async for item in sdk_stream(resp):
        match item:
            case OpenAIInitEvent(session_id=sid):
                state.session_id = sid

            case OpenAIResultEvent(
                input_tokens=ti, output_tokens=to, error=err
            ):
                state.usage_dict = {
                    "input_tokens": ti,
                    "output_tokens": to,
                }
                if err:
                    state.assistant_error = err

            case _ if RawResponsesStreamEvent is not None and isinstance(
                item, RawResponsesStreamEvent
            ):
                # Nested .data is the raw OpenAI Responses streaming event.
                delta = _delta_from_openai_response_event(item.data)
                if delta is None:
                    continue
                if not role_yielded:
                    yield {"role": "assistant"}
                    role_yielded = True
                yield delta

            case _ if RunItemStreamEvent is not None and isinstance(
                item, RunItemStreamEvent
            ):
                # High-level items (tool_called, tool_output,
                # message_output_created). We don't yield deltas here — the
                # text already came via RawResponsesStreamEvent. Logged for
                # visibility.
                _LOGGER.debug(
                    "OpenAI run item: %s",
                    getattr(item, "name", type(item).__name__),
                )

            case _:
                pass


def _delta_from_openai_response_event(data) -> AssistantContentDeltaDict | None:
    """Map an OpenAI Responses streaming event (dict or Pydantic model)
    to a ChatLog delta.

    The `data` field of ``RawResponsesStreamEvent`` is a Responses API
    event object; when it represents an output-text delta, we yield
    ``{"content": str}``. Other event shapes (start, done, tool deltas,
    ...) are ignored — downstream types already cover those channels.
    """
    # Accept both a dict (post-serialization round-trip) and the openai
    # SDK's typed event. Try attribute first, fall back to dict key.
    evt_type = getattr(data, "type", None) or (
        data.get("type") if isinstance(data, dict) else None
    )
    if evt_type == "response.output_text.delta":
        text = getattr(data, "delta", None) or (
            data.get("delta") if isinstance(data, dict) else None
        )
        if isinstance(text, str) and text:
            return {"content": text}
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_integration_openai_stream.py tests/test_integration_stream.py -v
```

Expected: all pass (new openai tests + existing claude tests).

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_claude_agent/conversation.py tests/test_integration_openai_stream.py
git commit -m "feat(integration): route stream consumption per backend"
```

---

## Task 13: Integration — `_ERROR_MESSAGES` additions + openai exception catches (TDD)

**Files:**
- Modify: `custom_components/ha_claude_agent/conversation.py`
- Create: `tests/test_integration_error_mapping_openai.py`

Add five new keys + catch the relevant `openai.*` exception classes in `_async_handle_message`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_integration_error_mapping_openai.py`:

```python
"""Tests for OpenAI exception -> error message mapping."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.ha_claude_agent.conversation import (  # noqa: E402
    _ERROR_MESSAGES,
    _map_openai_exception_to_key,
)


def test_openai_error_keys_present():
    for key in (
        "openai_auth_failed",
        "openai_rate_limit",
        "openai_server_error",
        "openai_invalid_model",
        "openai_connection_error",
    ):
        assert key in _ERROR_MESSAGES, f"missing key: {key}"


def test_auth_error_maps_to_auth_failed():
    import openai

    err = openai.AuthenticationError(
        message="bad key", response=None, body=None
    )
    assert _map_openai_exception_to_key(err) == "openai_auth_failed"


def test_rate_limit_maps():
    import openai

    err = openai.RateLimitError(message="too many", response=None, body=None)
    assert _map_openai_exception_to_key(err) == "openai_rate_limit"


def test_not_found_maps_to_invalid_model():
    import openai

    err = openai.NotFoundError(message="no model", response=None, body=None)
    assert _map_openai_exception_to_key(err) == "openai_invalid_model"


def test_connection_error_maps():
    import openai

    err = openai.APIConnectionError(request=None)
    assert _map_openai_exception_to_key(err) == "openai_connection_error"


def test_api_error_maps_to_server_error():
    import openai

    # openai.APIError is a base class — use a concrete subclass that the
    # SDK actually raises for 5xx responses.
    err = openai.InternalServerError(message="500", response=None, body=None)
    assert _map_openai_exception_to_key(err) == "openai_server_error"


def test_unknown_exception_returns_none():
    assert _map_openai_exception_to_key(ValueError("x")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_integration_error_mapping_openai.py -v
```

Expected: FAIL — `_map_openai_exception_to_key` doesn't exist.

- [ ] **Step 3: Add error keys + mapper + catches**

Edit `custom_components/ha_claude_agent/conversation.py`.

Add imports at top:

```python
import openai
```

Extend `_ERROR_MESSAGES` (add these keys to the existing dict):

```python
    "openai_auth_failed": (
        "OpenAI authentication failed. Check the API key in the add-on settings."
    ),
    "openai_rate_limit": (
        "OpenAI rate limit hit. Please wait a moment and try again."
    ),
    "openai_server_error": (
        "The model provider returned a server error. Please try again."
    ),
    "openai_invalid_model": (
        "The configured model was not accepted by the provider. "
        "Check the model name in the conversation agent settings."
    ),
    "openai_connection_error": (
        "Could not reach the OpenAI-compatible endpoint. "
        "Check the base URL in the add-on settings."
    ),
```

Add a module-level helper:

```python
def _map_openai_exception_to_key(err: BaseException) -> str | None:
    """Return the `_ERROR_MESSAGES` key for an openai.* exception, or None."""
    if isinstance(err, openai.AuthenticationError):
        return "openai_auth_failed"
    if isinstance(err, openai.RateLimitError):
        return "openai_rate_limit"
    if isinstance(err, openai.NotFoundError):
        return "openai_invalid_model"
    if isinstance(err, openai.APIConnectionError):
        return "openai_connection_error"
    # Catch-all for remaining openai.APIError subclasses (InternalServerError,
    # etc.). Keep this check last — the more specific subclasses should match
    # before this catch.
    if isinstance(err, openai.APIError):
        return "openai_server_error"
    return None
```

In `_async_handle_message`'s exception block (after the existing `except ClaudeSDKError` handler), add:

```python
        except openai.APIError as err:
            key = _map_openai_exception_to_key(err) or "openai_server_error"
            _LOGGER.error("OpenAI error (%s): %s", type(err).__name__, err)
            return self._error_response(
                _ERROR_MESSAGES[key], chat_log, user_input.language
            )
```

`openai.APIError` is the common base class for all `openai.*` exceptions listed above, so one catch handles all five.

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run pytest tests/test_integration_error_mapping_openai.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_claude_agent/conversation.py tests/test_integration_error_mapping_openai.py
git commit -m "feat(integration): map openai.* exceptions to user-facing error messages"
```

---

## Task 14: Integration — enable `custom_value=True` on subentry model field

**Files:**
- Modify: `custom_components/ha_claude_agent/config_flow.py`

No new tests — this is a one-line HA selector config change.

- [ ] **Step 1: Update `config_flow.py` model selector**

Edit `custom_components/ha_claude_agent/config_flow.py` — in `_build_schema`, change the `CONF_CHAT_MODEL` field:

```python
                vol.Optional(
                    CONF_CHAT_MODEL,
                    default=defaults.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=MODELS,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
```

The `MODELS` list stays Claude-only (as agreed in the spec). Users typing a non-Claude model just enter the string in the same field.

- [ ] **Step 2: Run existing config_flow tests if any**

Run:

```bash
uv run pytest tests/ -q -k "config"
```

Expected: no regression (no tests may exist for config_flow — acceptable).

- [ ] **Step 3: Commit**

```bash
git add custom_components/ha_claude_agent/config_flow.py
git commit -m "feat(integration): allow custom model strings in subentry dropdown"
```

---

## Task 15: Docs + README updates

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Choosing a backend" section to `README.md`**

Before deciding exact placement, read the current README to find the right section (probably near install / config). Add the following content:

```markdown
## Choosing a backend

The add-on supports two backends, selected via the `backend` option:

- `claude` — uses the Claude Code CLI via `claude-agent-sdk`. Requires
  `claude_auth_token`. Built-in web search and web fetch are available.
- `openai` — uses `openai-agents` against any OpenAI-compatible endpoint.
  Requires `openai_api_key` AND `openai_base_url`. No web search yet.

### Example: Google AI Studio (Gemini)

1. Get an API key from <https://aistudio.google.com/apikey>.
2. In the add-on options, set:
   - `backend: openai`
   - `openai_api_key: <your key>`
   - `openai_base_url: https://generativelanguage.googleapis.com/v1beta/openai/`
3. In the HA conversation agent settings, select "Custom..." in the model
   dropdown and type `gemini-2.0-flash` (or another Gemini model).

### Example: OpenAI

- `openai_base_url: https://api.openai.com/v1`
- Model: any OpenAI model, e.g. `gpt-4.1`, `gpt-5`, typed via Custom...

### Example: OpenRouter

- `openai_base_url: https://openrouter.ai/api/v1`
- Model: `openai/gpt-4.1` or any other OpenRouter model string.

### Conversation history

The OpenAI backend stores conversation history in a SQLite database at
`/data/sessions.db` inside the add-on. Wiping the add-on's `/data` volume
resets all conversations.

### Migration from <=0.6.x

The single `auth_token` option has been split into four fields. On upgrade,
set `backend: claude` and paste your existing token into `claude_auth_token`.
The legacy `auth_token` field is accepted as a fallback for one minor version
(warning logged) but should be migrated.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add backend selection guide with Google AI Studio example"
```

---

## Task 16: Full test sweep + manual verification checklist

**Files:** none

This is the gate before PR.

- [ ] **Step 1: Run the full test suite**

Run:

```bash
uv run pytest tests/ -v
```

Expected: all tests pass. Every file modified by this plan has test coverage. If anything fails, stop and diagnose — don't paper over it.

- [ ] **Step 2: Run ruff / mypy**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy custom_components/ha_claude_agent ha_claude_agent_addon/src
```

Expected: clean.

- [ ] **Step 3: Manual verification against a live Claude backend**

- Install the add-on, set `backend: claude`, paste a real `claude_auth_token`.
- Start the add-on.
- In HA, send a conversation message that triggers a tool call ("turn on the kitchen light"). Confirm the tool call executes.
- Send a follow-up message ("and now turn it off"). Confirm session resume — the assistant should know context.
- Verify the usage sensor records cost and token counts.

- [ ] **Step 4: Manual verification against a live Google AI Studio backend**

- Change add-on options: `backend: openai`, `openai_api_key: <key>`,
  `openai_base_url: https://generativelanguage.googleapis.com/v1beta/openai/`.
- Restart the add-on.
- In the conversation agent subentry, change model to `gemini-2.0-flash` via
  "Custom...".
- Send "turn on the kitchen light"; confirm tool call works.
- Send "turn it off"; confirm history was retained (SQLiteSession).
- Verify the usage sensor records token counts (cost will be 0.0).

- [ ] **Step 5: Commit any lint/mypy fixes made during the sweep**

```bash
git status
git add <files>
git commit -m "chore: lint/format cleanup"
```

- [ ] **Step 6: Tag the add-on release**

When the PR lands and is merged, tag `v0.7.0` on main to trigger the add-on build.

---

## Self-review notes

- **Spec coverage:** all 8 key decisions + every subsection of the spec map to a task above.
  - Dual backend → Tasks 7, 8, 9
  - Global add-on switch → Tasks 9, 10
  - Per-subentry model string + custom_value → Task 14
  - Native events per backend → Tasks 4, 5, 11, 12
  - SQLiteSession session history → Task 8
  - `cost_usd = 0.0` on OpenAI → Task 12 (via `_StreamResult` unchanged)
  - `effort` pass-through → already in `QueryRequest`; no task needed
  - HA tools shared core → Tasks 2, 3, 6
  - Migration (`auth_token` legacy alias) → Task 9 Step 3
  - Error mapping additions → Task 13
  - README + version bump → Tasks 10, 15

- **Risks flagged in spec:** the `SQLiteSession` WAL concern and the Google AI Studio tool-calling quirk concern are both monitoring-only — no task action needed unless they materialize.
