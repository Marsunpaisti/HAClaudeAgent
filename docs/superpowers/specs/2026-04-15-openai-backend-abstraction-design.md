# OpenAI-compatible backend abstraction

Date: 2026-04-15

## Goal

Add a second agent backend to the HA Claude Agent add-on so users can point
the integration at any OpenAI-compatible endpoint (OpenAI, Google AI Studio,
OpenRouter, LiteLLM, Ollama, …) in addition to the existing claude-agent-sdk
path. The integration side stays single, the add-on gains a backend switch,
and the wire protocol carries each backend's native event types.

## Non-goals

- Dropping claude-agent-sdk. Both backends ship side-by-side.
- Web search on the OpenAI backend. Deferred to a later iteration; the
  Claude backend keeps its built-in WebSearch/WebFetch.
- Per-subentry credentials or base URL. Credentials stay in add-on options.
- Provider-specific UI flavor (logos, per-provider help text). One subentry
  form shape serves both backends.

## Key decisions

| Decision | Choice |
|---|---|
| Replace or dual-backend | Dual — both SDKs ship |
| Backend selection | Global, in add-on options (explicit `backend` field) |
| Model selection | Per-subentry string; dropdown with `custom_value=True` |
| Wire protocol | Native events per backend; integration branches on class |
| OpenAI conversation history | Add-on-side `SQLiteSession` on `/data`, keyed by `session_id` |
| Cost tracking on OpenAI | Tokens only (`cost_usd = 0.0`); no pricing table |
| `effort` parameter | Passed through to both; maps to `reasoning_effort` on OpenAI |
| HA tools | Shared core logic, two decorator wrappers |

## Architecture

### Add-on: backend abstraction

New module `ha_claude_agent_addon/src/backend.py`:

```python
class Backend(Protocol):
    name: str  # "claude" | "openai"
    async def stream_query(
        self,
        req: QueryRequest,
        ha_client: HAClient,
    ) -> AsyncGenerator[str]:  # yields pre-formatted SSE strings
        ...
```

Two concrete implementations:

- **`ClaudeBackend`** — lift-and-shift of the current `_stream_query` logic
  from `server.py`. No behavior change; existing `to_jsonable()`
  serialization path preserved. Still streams `claude_agent_sdk`
  dataclasses verbatim.
- **`OpenAIBackend`** — wraps `openai-agents`. Per request:
  1. Constructs `AsyncOpenAI(base_url=options.openai_base_url,
     api_key=options.openai_api_key)` and registers it via
     `set_default_openai_client`.
  2. Calls `create_ha_tools_openai(ha_client, exposed_entities)` to get the
     three `@function_tool` functions.
  3. Constructs `Agent(name="ha_assistant", instructions=req.system_prompt,
     model=req.model, tools=[...])`.
  4. Opens a `SQLiteSession(session_id=req.session_id or uuid.uuid4().hex,
     db_path="/data/sessions.db")` for history. On first turn
     (`req.session_id is None`) the generated UUID is echoed back to the
     integration via `OpenAIInitEvent` (see wire protocol below) so the
     integration's existing session LRU cache can store it.
  5. Runs `Runner.run_streamed(agent, req.prompt, session=session,
     max_turns=req.max_turns)`, iterating `result.stream_events()`.
  6. Emits a leading `OpenAIInitEvent(session_id=...)`, forwards each
     stream event via the new `to_jsonable_pydantic()` serializer, then
     emits a terminal `OpenAIResultEvent(input_tokens, output_tokens,
     error)` after the stream exits.
  7. Maps `openai.AuthenticationError` / `RateLimitError` / `APIError` /
     `NotFoundError` / `APIConnectionError` through `exception_to_dict()`.

`server.py` becomes a thin FastAPI shell:

1. Startup reads options, validates required fields per backend, constructs
   the chosen `Backend` instance into `app.state.backend`.
2. `POST /query` routes into `app.state.backend.stream_query(body, ha_client)`.
3. `GET /health` returns `{"status": "ok", "api_version": 3, "backend": name}`.

### Wire protocol

Today the SSE stream carries `claude-agent-sdk` dataclasses reconstructed via
`getattr(claude_agent_sdk, cls_name)`. The OpenAI path adds a second class
namespace.

**Add-on side (`serialization.py`):** new walker `to_jsonable_pydantic(obj)`
that uses `obj.model_dump(mode="json")` for Pydantic `BaseModel` instances
and injects the same `_type` tag. Dataclass and Pydantic paths coexist —
`to_jsonable` dispatches by instance type.

**Two new Pydantic models** in the add-on's local `openai_events.py`:

```python
class OpenAIInitEvent(BaseModel):
    session_id: str

class OpenAIResultEvent(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
```

`OpenAIInitEvent` is emitted once at stream start. `OpenAIResultEvent` is
emitted once at stream end. Neither reuses Claude SDK dataclasses — the
integration's router matches on the openai-agents-specific types directly.

**Integration side (`stream.py`):** `from_jsonable()` extends its class
lookup: try `claude_agent_sdk`, then `agents` (openai-agents public
package), then a new `custom_components/ha_claude_agent/openai_events.py`
(mirror of the add-on file). Unknown `_type` values still fall through to a
raw dict, preserving today's graceful-degradation behavior.

**Session-id flow on OpenAI:** `OpenAIBackend` uses the integration's
supplied `req.session_id` as the SQLiteSession key; if absent (first turn),
it generates a UUID. It emits an `OpenAIInitEvent(session_id: str)`
Pydantic model at stream start, carrying whichever session id is in play.
The integration's existing session LRU cache picks it up the same way it
consumes `SystemMessage(subtype="init")` today. `QueryRequest` is
unchanged.

### Integration: stream consumption

`conversation.py` — `_deltas_from_sdk_stream` becomes a router:

```python
async def _deltas_from_sdk_stream(resp, state):
    first = await _peek_first_message(resp)
    if _is_openai_event(first):
        async for delta in _deltas_from_openai(first, resp, state): yield delta
    else:
        async for delta in _deltas_from_claude(first, resp, state): yield delta
```

- `_deltas_from_claude` — current logic verbatim (renamed from the inline
  body). Matches `StreamEvent`, `SystemMessage`, `ResultMessage`,
  `AssistantMessage`, `RateLimitEvent`.
- `_deltas_from_openai` — new. Matches `RawResponsesStreamEvent` (for text
  deltas out of the nested `data` field), `RunItemStreamEvent` (for tool
  call / tool output visibility; content can be logged but is not yielded
  as deltas because the message-complete event already yields the text),
  `OpenAIInitEvent` (populates `state.session_id`), `OpenAIResultEvent`
  (populates `state.usage_dict`, leaves `state.cost_usd = 0.0`).

Both paths feed the same `_StreamResult` accumulator so the downstream
`conversation.py` code (cost dispatch to sensor, error message mapping,
session storage) stays unified.

**New `_ERROR_MESSAGES` keys:**

- `openai_auth_failed` — from `openai.AuthenticationError`
- `openai_rate_limit` — from `openai.RateLimitError`
- `openai_server_error` — from `openai.APIError` / `InternalServerError`
- `openai_invalid_model` — from `openai.NotFoundError` (model not found)
- `openai_connection_error` — from `openai.APIConnectionError`

Messages reference the add-on base URL / api key as appropriate so the user
knows which config to fix.

### HA tools

Three operations, two backends. Extract core logic from the current
closures into pure async functions.

**New `tool_logic.py`:**

```python
class ToolBlocked(Exception): ...
class ToolInvalidArgs(Exception): ...
class ToolNotFound(Exception): ...

async def call_service_logic(
    ha_client, exposed_set, domain, service, entity_id, service_data: str
) -> str: ...

async def get_entity_state_logic(
    ha_client, exposed_set, entity_id
) -> str: ...

async def list_entities_logic(
    ha_client, exposed_set, domain_filter
) -> str: ...
```

Each returns a human-readable string or raises one of the sentinels.

**`tools_claude.py`** (renames current `tools.py`) — each `@tool(...)`
closure calls the core function, wraps the string in `{"content": [{"type":
"text", "text": ...}]}`, sets `is_error: True` on sentinel exceptions.

**`tools_openai.py`** — each `@function_tool` function has explicit typed
parameters (openai-agents reads the signature for schema generation), calls
the core function, returns either the success string or `"Error: {msg}"`
for sentinels. Keeps `service_data: str` (JSON-encoded) for schema parity
with the Claude wrappers.

Both wrapper modules use identical tool names, descriptions, and the same
exposed-entities security filter. `create_ha_tools_claude(ha_client,
exposed_entities)` and `create_ha_tools_openai(ha_client, exposed_entities)`
are the two factory functions, called per request by their respective
backends.

## Config

### Add-on options (`ha_claude_agent_addon/config.yaml`)

```yaml
options:
  backend: "claude"
  claude_auth_token: ""
  openai_api_key: ""
  openai_base_url: ""
schema:
  backend: list(claude|openai)
  claude_auth_token: password
  openai_api_key: password
  openai_base_url: url?
```

**Startup validation** (`server.py` lifespan):

- `backend=claude` requires non-empty `claude_auth_token`.
- `backend=openai` requires non-empty `openai_api_key` AND non-empty
  `openai_base_url`. (No default URL — there is no "right" one; every
  provider's endpoint is different.)
- Failure of either check logs fatal and the add-on exits with a clear
  message.

**Legacy migration:** if the old single `auth_token` field is present in
`/data/options.json` and `claude_auth_token` is empty, accept it as a fall-
back when `backend=claude`, log a one-time warning. Removed in the next
minor version.

### Integration (`config_flow.py`)

No structural changes. Add-on discovery + health check stay identical.
`/health` response gains a `backend` field but the integration doesn't need
to branch on it at config time — mismatches surface as query errors.

### Subentry form (`config_flow.py` `_build_schema`)

Two changes to the model field:

1. `MODELS` list stays Claude-only (project's primary audience):
   - `claude-sonnet-4-6`, `claude-opus-4-6`, `claude-haiku-4-5-20251001`.
2. `SelectSelector` gets `custom_value=True`. Users of any non-Claude
   backend type their own model string (`gemini-2.0-flash`,
   `openai/gpt-4.1` on OpenRouter, local Ollama model name, etc.).

No per-subentry credentials, base URL, or backend field. No schema version
bump needed — adding `custom_value` is non-breaking for stored entries.

## Dependencies

- **Add-on** (`pyproject.toml`, `uv.lock`, `requirements.txt`): add
  `openai-agents`. Keep `claude-agent-sdk`.
- **Integration** (`manifest.json` `requirements` array): add
  `openai-agents`. Used for class reconstruction and match patterns only.

Both packages install on HAOS Python 3.13. Size impact of `openai-agents`
is acceptable; if it becomes a concern later, integration-side imports can
move behind lazy paths.

## Testing

### New tests

- `tests/addon/test_tool_logic.py` — unit tests for the extracted pure
  functions. Mocks `ha_client`. Covers exposed-entity blocking, JSON parse
  failures, not-found cases, state read-back.
- `tests/addon/test_tools_claude.py` — smoke tests confirming the
  `@tool`-decorated wrappers format `{"content": [...], "is_error": bool}`
  envelopes correctly.
- `tests/addon/test_tools_openai.py` — same shape for `@function_tool`
  wrappers: success → plain string, sentinels → `"Error: ..."`.
- `tests/addon/test_openai_backend.py` — runs `OpenAIBackend.stream_query`
  against a stubbed `Runner` yielding canned event sequences. Asserts SSE
  framing, round-trip reconstruction via `from_jsonable`, and
  `SQLiteSession` persistence across two calls with the same session_id
  using a tmp `/data` directory.
- `tests/addon/test_serialization_pydantic.py` — round-trips Pydantic
  models through `to_jsonable_pydantic` → JSON → `from_jsonable`.
  Mirrors the existing `test_serialization.py` coverage shape.
- `tests/integration/test_openai_stream.py` — feeds canned openai-agents
  events into `_deltas_from_openai`, asserts `_StreamResult` accumulates
  correctly and the yielded deltas match expectations.
- `tests/integration/test_error_mapping_openai.py` — verifies each
  `openai.*` exception class maps to its intended `_ERROR_MESSAGES` key.

### Existing tests

Expected to keep passing unchanged. The refactor in sections 1–3 is
additive; `_stream_query` logic moves into `ClaudeBackend` but keeps the
same signature/shape. Any test importing `server._stream_query` directly
gets a one-line path update to `backend.ClaudeBackend.stream_query`.

### Manual verification checklist for the PR

- Claude backend: one turn + one resume against a real Anthropic API key.
  Confirms tool calls work (`turn on light.living_room`) and session
  resume works.
- OpenAI backend: one turn + one resume against Google AI Studio with
  `gemini-2.0-flash` as the model, base_url
  `https://generativelanguage.googleapis.com/v1beta/openai/`. Confirms
  tool calls work end-to-end and `SQLiteSession` persists history.

No live-API CI tests — flaky, secret-dependent, and expensive.

## Rollout

- Add-on version bump `0.6.2 → 0.7.0`. Breaking config schema change.
- Integration version unchanged unless HACS requires it for the `manifest.json`
  dependency update.
- `/health` `api_version` bumps `2 → 3` (new `backend` field).
- Release notes must cover:
  1. Config schema changed — users must re-enter auth in add-on options
     and pick a `backend` value.
  2. Legacy `auth_token` accepted for one minor version as a fallback
     when `backend=claude` (warning logged).
  3. Concrete example for Google AI Studio: `backend=openai`, API key
     from `https://aistudio.google.com/apikey`, `openai_base_url =
     https://generativelanguage.googleapis.com/v1beta/openai/`, model
     `gemini-2.0-flash` typed via the subentry "Custom..." option.
  4. Web search remains Claude-only for now.
- README: add a "Choosing a backend" section with the Google AI Studio
  example.

## Risks and open questions

- **openai-agents install size on HAOS.** Larger than claude-agent-sdk's
  wheel. If this bloats the integration install unacceptably, we can move
  to lazy imports inside `stream.py` — acceptable because openai-agents
  types are only touched when consuming an OpenAI-backend stream.
- **SQLite file corruption / concurrent access.** SQLiteSession writes to
  `/data/sessions.db`. The add-on is single-process (uvicorn default), so
  concurrency within the add-on is async-coroutine-level, which SQLite
  handles fine with its default locking. If a future multi-worker setup is
  ever introduced, SQLite WAL mode or a per-session file would be needed.
- **Google AI Studio tool calling quirks.** Their OpenAI-compatible
  endpoint supports function calling, but behavior may drift from OpenAI
  proper. Manual verification on each release is the mitigation; if a
  specific quirk bites, we handle it in `OpenAIBackend` (e.g. stripping
  unsupported params from `ModelSettings`) rather than in tool code.
- **Model/backend mismatch.** A user with `backend=openai` who leaves a
  Claude model string in their subentry will get a clear error on the
  first query (mapped to `openai_invalid_model`). No pre-flight check at
  config-flow time; not worth the round-trip.
