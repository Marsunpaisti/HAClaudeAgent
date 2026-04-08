# Streaming Responses Design

**Date:** 2026-04-09
**Status:** Approved

## Goal

Reduce time-to-first-audio in the HA voice pipeline by streaming Claude's response tokens through the add-on to the integration, which yields them into HA's `ChatLog` delta API. This enables the voice pipeline's TTS streaming (available since HA 2025.6) to synthesize audio incrementally rather than waiting for the complete response.

## Architecture Overview

```
Claude SDK (token-level streaming)
    → Add-on server.py (SSE stream over HTTP)
        → Integration conversation.py (async SSE reader)
            → ChatLog.async_add_delta_content_stream()
                → Voice pipeline TTS streaming / Frontend chat UI
```

The request format is unchanged (`QueryRequest` JSON POST body). Only the response changes from a single JSON object to an SSE event stream.

## SSE Event Schema

The add-on returns `Content-Type: text/event-stream`. Four event types:

### `stream` — forwarded SDK StreamEvent

Raw `StreamEvent.event` dict from the claude-agent-sdk, forwarded as-is. These follow the Anthropic streaming format:

- `content_block_start` — new content block (type: `text`, `thinking`, or `tool_use`)
- `content_block_delta` — incremental content:
  - `delta.type: "text_delta"`, `delta.text: "..."` — response text token
  - `delta.type: "thinking_delta"`, `delta.thinking: "..."` — thinking token
  - `delta.type: "input_json_delta"`, `delta.partial_json: "..."` — tool input
- `content_block_stop` — content block finished
- `message_start`, `message_delta`, `message_stop` — message lifecycle

```
event: stream
data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello, "}}
```

### `session` — session ID

Emitted from `SystemMessage(subtype="init")`. Always the first event.

```
event: session
data: {"session_id": "abc123"}
```

### `result` — final metadata

Emitted from `ResultMessage`. Always the last event (unless `error` replaces it).

```
event: result
data: {"session_id": "abc123", "cost_usd": 0.012, "num_turns": 3, "error_code": null}
```

### `error` — failure mid-stream

Emitted when an exception occurs. Last event before stream closes.

```
event: error
data: {"error_code": "process_error", "message": "Claude CLI process exited unexpectedly"}
```

Error codes are the same as today: `cli_not_found`, `process_error`, `cli_connection_error`, `parse_error`, `internal_error`.

## Add-on Changes (server.py)

1. **Enable token-level streaming** — set `include_partial_messages=True` on `ClaudeAgentOptions`

2. **SSE generator** — async generator that iterates `query()` and yields SSE-formatted strings:
   - `SystemMessage(subtype="init")` → yield `event: session`
   - `StreamEvent` → yield `event: stream` with `message.event` dict as data
   - `ResultMessage` → yield `event: result` with metadata
   - `AssistantMessage` → skip (content already streamed via StreamEvents)
   - Exceptions → yield `event: error`, then return

3. **Return `StreamingResponse`** — `StreamingResponse(sse_generator(), media_type="text/event-stream")`

4. **SSE formatting helper** — function that takes event type + data dict, returns `event: {type}\ndata: {json}\n\n`

## Integration Changes (conversation.py)

1. **Set `_attr_supports_streaming = True`**

2. **`_async_handle_message` flow:**
   - Build `QueryRequest` (unchanged)
   - POST to `/query` with 300-second timeout
   - Read response as SSE stream via `_transform_stream()` async generator
   - Feed into `chat_log.async_add_delta_content_stream(self.entity_id, stream)`
   - After stream completes, use stashed metadata to update session map and build `ConversationResult`

3. **`_transform_stream(response)` async generator:**
   - Reads SSE lines from the aiohttp response
   - Parses `event:` / `data:` pairs
   - Mapping:
     - `stream` with `content_block_delta` + `text_delta` → yield `AssistantContentDeltaDict(content=delta["text"])`
     - `stream` with `content_block_delta` + `thinking_delta` → yield `AssistantContentDeltaDict(thinking_content=delta["thinking"])`
     - `session` → stash `session_id` (do not yield)
     - `result` → stash metadata (do not yield)
     - `error` → stash error info (do not yield)
     - All other `stream` events → skip (tool use, message lifecycle)
   - Yields `{"role": "assistant"}` as the first delta to signal message start

4. **Error handling:**
   - `error` SSE event → return error `ConversationResult` with user-friendly message
   - Connection drop (`ClientPayloadError`) → return generic connection error
   - Timeout → return timeout error

## Model Changes (models.py)

- `QueryRequest` — unchanged
- `QueryResponse` — removed (data now delivered via SSE events)
- Both copies (integration + add-on) updated identically

## Files Changed

| File | Change |
|---|---|
| `ha_claude_agent_addon/src/server.py` | SSE streaming response, `include_partial_messages=True` |
| `custom_components/ha_claude_agent/conversation.py` | SSE consumer, ChatLog delta streaming, `supports_streaming = True` |
| `custom_components/ha_claude_agent/models.py` | Remove `QueryResponse` |
| `ha_claude_agent_addon/src/models.py` | Remove `QueryResponse` |

## Files NOT Changed

- `tools.py` — tool execution is unchanged
- `ha_client.py` — HA REST API client is unchanged
- `helpers.py` — system prompt building is unchanged
- `config_flow.py` — no new configuration needed
- `const.py` — no new constants needed

## Edge Cases

- **Error mid-stream after partial text:** Voice pipeline may have already spoken partial text. Acceptable — partial response is better than nothing. Error surfaces in chat UI.
- **Empty response:** `result` event arrives with no preceding `text_delta`. Handled same as today.
- **Long tool execution gaps:** Normal — no events during tool execution. Timeout covers the pathological case.
- **Connection drop without error event:** `aiohttp` raises `ClientPayloadError`, caught and mapped to connection error.
