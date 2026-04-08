# Pydantic Shared Models + FastAPI Migration

**Date:** 2026-04-08
**Status:** Approved
**Scope:** Add-on HTTP API layer only — no changes to MCP tools, HA client, or config flow

## Problem

The add-on server (`server.py`) and the integration (`conversation.py`) communicate via HTTP
with a JSON request/response contract. Today both sides build and parse raw dicts by convention —
no validation, no shared type definitions. This is fragile and hard to maintain.

## Decision

1. Define the API contract as Pydantic models in a shared `models.py`
2. Migrate the add-on server from aiohttp to FastAPI (native Pydantic integration)
3. Update the integration to use the same models for request construction and response parsing

## Shared Models (`models.py`)

Two Pydantic models define the complete HTTP API contract:

```python
from pydantic import BaseModel, Field

class QueryRequest(BaseModel):
    prompt: str
    model: str
    system_prompt: str
    max_turns: int = 10
    effort: str = "medium"
    session_id: str | None = None
    exposed_entities: list[str] = Field(default_factory=list)

class QueryResponse(BaseModel):
    result_text: str | None = None
    session_id: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    error_code: str | None = None
```

### Field decisions

- **Request defaults match the add-on's current fallbacks** (`max_turns=10`, `effort="medium"`) —
  if the integration omits them, behavior is unchanged.
- **`exposed_entities` uses `Field(default_factory=list)`** to avoid the mutable default gotcha.
- **No separate `error_detail` field** — `result_text` already carries error text when `error_code`
  is set. A separate field would mean the integration checks two places.
- **`cost_usd` stays optional** — not always available (e.g., on CLI errors).

### Sharing strategy

The HA add-on builder uses the add-on directory as Docker build context, and HACS installs only
`custom_components/ha_claude_agent/`. These are isolated distribution paths that never see each
other's files at install time. A single shared file is not possible without changing the build
pipeline.

**Solution:** Identical copies in both locations, with a dev test enforcing sync.

- `custom_components/ha_claude_agent/models.py` — imported by the integration
- `ha_claude_agent_addon/src/models.py` — identical copy, imported by the FastAPI server
- `tests/test_models_sync.py` — asserts the two files are byte-identical

The models file is small (~20 lines) and stable — once the API contract is set, it rarely changes.
The sync test catches any drift in CI or local `pytest`.

## FastAPI Server Migration

Replace aiohttp with FastAPI + uvicorn in `ha_claude_agent_addon/src/server.py`.

### Endpoints

```
GET  /health  →  {"status": "ok", "api_version": 1}
POST /query   →  QueryRequest in, QueryResponse out
```

FastAPI auto-validates the request body against `QueryRequest`. Malformed payloads get a 422
with details — the manual `try/except KeyError` block is removed.

### Lifecycle

Startup and shutdown use FastAPI's `lifespan` context manager. Same logic as current
`on_startup`/`on_cleanup`:

- **Startup:** Read auth token from `/data/options.json`, validate `SUPERVISOR_TOKEN`, create
  shared `HAClient` instance. Store on `app.state`.
- **Shutdown:** Close the `HAClient` session.

### Entry point

Keep `python /app/server.py` with `uvicorn.run(app, host="0.0.0.0", port=8099)` at the bottom.
Same pattern as current aiohttp — no need to change the s6 run script.

### What stays identical

All Claude SDK query loop logic, MCP tool creation, auth env building, session ID extraction,
text accumulation, error handling hierarchy. None of that changes — it just sits inside a FastAPI
endpoint instead of an aiohttp handler.

## Integration Changes (`conversation.py`)

Minimal changes — replace hand-built dicts with Pydantic models:

- **Request:** Build `QueryRequest(...)`, post with `json=request.model_dump(exclude_none=True)`
- **Response:** Parse with `QueryResponse.model_validate(data)`, access typed fields

No new dependencies for the integration — pydantic ships with Home Assistant.
`manifest.json` `requirements` stays empty.

## Dependency Changes

| File | Change |
|---|---|
| `ha_claude_agent_addon/requirements.txt` | Add `fastapi`, `uvicorn[standard]`. Keep `aiohttp` (used by `ha_client.py`). |
| `requirements_dev.txt` | Add `pydantic`, `fastapi`, `pytest` |
| `custom_components/ha_claude_agent/manifest.json` | No change |
| `ha_claude_agent_addon/Dockerfile` | No change |

## Files Changed

| File | Action | Description |
|---|---|---|
| `custom_components/ha_claude_agent/models.py` | Create | Pydantic `QueryRequest` + `QueryResponse` |
| `ha_claude_agent_addon/src/models.py` | Create | Identical copy |
| `ha_claude_agent_addon/src/server.py` | Rewrite | aiohttp → FastAPI + uvicorn, use models |
| `custom_components/ha_claude_agent/conversation.py` | Edit | Use models for HTTP calls |
| `ha_claude_agent_addon/requirements.txt` | Edit | Add `fastapi`, `uvicorn[standard]` |
| `requirements_dev.txt` | Edit | Add `pydantic`, `fastapi`, `pytest` |
| `tests/test_models_sync.py` | Create | Assert both `models.py` copies are identical |

## Files Not Touched

`ha_client.py`, `tools.py`, `config_flow.py`, `const.py`, `helpers.py`, `__init__.py`,
`Dockerfile`, s6 scripts, `manifest.json` — unaffected by the API layer change.
