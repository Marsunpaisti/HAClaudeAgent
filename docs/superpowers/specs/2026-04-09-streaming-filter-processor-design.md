# StreamingFilterProcessor — Design Spec

**Date:** 2026-04-09
**Status:** Draft

## Motivation

Claude responses often include content that shouldn't be exposed to downstream consumers verbatim. The immediate example: when Claude performs a web search, it appends a "Sources" section with markdown links that a TTS engine reads aloud — URLs and citation noise that users don't want to hear.

We need a way to filter these sections out of Claude's streaming output **without** breaking streaming delivery. The filter must:

1. Preserve incremental streaming to the chat log (HA's streaming TTS depends on it)
2. Only hold back content while a filter is actively uncertain about it
3. Be extensible — new filter types can be added without rewriting the plumbing

For now we only need a sources-section filter, but the design must make it trivial to add more filter types (e.g., `<NO_TTS>` block stripping, code-block collapsing, regex redaction).

## Architecture

A **stateful streaming pipeline** of composable filters. Each filter is a self-contained transducer that receives text chunks and returns text that's safe to emit. Filters are chained together — the output of one becomes the input of the next, like Unix shell pipes.

```
SDK text deltas → F1.feed() → F2.feed() → ... → chat log deltas
                     ↑            ↑
                  each filter holds its own "uncertain" buffer
```

When any filter is uncertain about content, it internally buffers that content and returns less output (or nothing) from `feed()`. Downstream filters naturally see nothing, so nothing flows to the consumer. When the uncertain filter resolves its state, it releases content on a subsequent `feed()` call (or at end of stream via `flush()`).

**Key properties of this design:**

- Filters are independently testable — no shared state, no processor coupling
- Each filter's buffering strategy is its own concern (line-based, tag-based, character-based)
- Discarding content is trivial — a filter just doesn't return it
- Order matters: earlier filters make decisions first

## Components

All code lives in a new module: `custom_components/ha_claude_agent/stream_filters.py`

### 1. `StreamFilter` (ABC)

The common interface. Every filter implements these two methods.

```python
class StreamFilter(ABC):
    """A stateful text transducer that may buffer uncertain content internally."""

    @abstractmethod
    def feed(self, text: str) -> str:
        """Feed a chunk of text; return whatever is safe to emit now.

        May return an empty string if the filter is currently holding
        uncertain content. May return more text than was fed in if the
        filter was previously holding and now releases.
        """

    @abstractmethod
    def flush(self) -> str:
        """Called at end of stream. Return any remaining content per the
        filter's end-of-stream policy (emit, discard, or partial release).
        """
```

### 2. `StreamingFilterProcessor`

Chains filters in order, piping text through them sequentially.

```python
class StreamingFilterProcessor:
    """Composes multiple StreamFilters into a sequential pipeline."""

    def __init__(self, filters: list[StreamFilter]) -> None:
        self._filters = filters

    def feed(self, text: str) -> str:
        for f in self._filters:
            text = f.feed(text)
        return text

    def flush(self) -> str:
        """Flush all filters in order, passing each filter's flushed
        content through the remaining downstream filters.
        """
        buffer = ""
        for f in self._filters:
            buffer = f.feed(buffer) + f.flush()
        return buffer
```

The `flush()` logic is important: when filter N flushes its held content, that content still needs to pass through filters N+1, N+2, etc. The loop feeds each filter's flushed output into the next filter's `feed()` before that next filter itself flushes.

### 3. `LineBufferedFilter` (helper base class)

Most text filters operate on complete lines. This base class handles the boilerplate of **assembling full lines from partial chunks** so concrete filters only implement their per-line logic.

**Responsibility split:**
- **Base class** owns `_line_buffer` for assembling chunks into complete lines. Once a line is complete, it's passed to `_process_line()` and removed from the buffer.
- **Subclass** is responsible for storing any line it chooses to hold (by returning `None` from `_process_line`). The base class forgets the line once handed off. Subclasses that hold lines maintain their own state (e.g., `SourcesFilter` uses a `_held: list[str]` attribute) and release them via `_process_line` return values or via `_finalize()` at flush.

```python
class LineBufferedFilter(StreamFilter):
    """Base for line-oriented filters. Accumulates text until newlines,
    then calls _process_line() for each complete line. Subclasses implement
    _process_line() and optionally _finalize() for end-of-stream handling.
    """

    def __init__(self) -> None:
        self._line_buffer = ""

    def feed(self, text: str) -> str:
        self._line_buffer += text
        output: list[str] = []
        while (nl := self._line_buffer.find("\n")) != -1:
            line = self._line_buffer[:nl]
            self._line_buffer = self._line_buffer[nl + 1:]
            result = self._process_line(line)
            if result is not None:
                output.append(result + "\n")
        return "".join(output)

    def flush(self) -> str:
        output: list[str] = []
        # Process any trailing partial line (no newline terminator)
        if self._line_buffer:
            result = self._process_line(self._line_buffer)
            if result is not None:
                output.append(result)
            self._line_buffer = ""
        final = self._finalize()
        if final:
            output.append(final)
        return "".join(output)

    @abstractmethod
    def _process_line(self, line: str) -> str | None:
        """Return the line (without trailing newline) to emit it, or None to hold it."""

    def _finalize(self) -> str:
        """Called at end of stream. Override to release held state."""
        return ""
```

### 4. `SourcesFilter` (concrete)

Detects and removes markdown "Sources" sections from streaming text. Subclasses `LineBufferedFilter`.

**State machine:**

Note: the source-header pattern is a subset of the source-line pattern, so any header line also matches as a source line. Header-specific behavior takes precedence in `NORMAL` (to detect section starts); once past `NORMAL`, headers are just treated as source lines.

| State | Meaning | On source-header line | On source-line (non-header) | On other line | On flush |
|---|---|---|---|---|---|
| `NORMAL` | Default | Hold line → `POSSIBLE_HEADER` | Emit | Emit | (nothing held) |
| `POSSIBLE_HEADER` | Saw a header, waiting to confirm | Hold → `IN_SOURCES` | Hold → `IN_SOURCES` | False alarm: emit held + this line → `NORMAL` | Emit held (false alarm) |
| `IN_SOURCES` | Confirmed sources section | Hold | Hold | Discard held, emit this line → `NORMAL` | Discard held |

**Source-header pattern:** case-insensitive match for one of these (optionally wrapped in markdown header `#`/`##`/`###` or bold `**...**`, optionally with trailing `:`):

- English: `Sources`, `References`, `Citations`
- Finnish: `Lähteet`, `Viitteet`

Examples that match: `# Sources`, `## Sources:`, `**Sources**`, `**References:**`, `### Citations`, `# Lähteet`.

**Source-line pattern:** matches lines that are part of a sources block:
- Empty or whitespace-only
- A markdown link: `[text](http://...)` or `[text](https://...)`, optionally prefixed with a list marker (`-`, `*`, `1.`, `2.`, etc.)
- A bare URL (`http://...` or `https://...`), optionally with a list marker
- Another header line matching the source-header pattern (handles e.g. `# Sources` immediately followed by `# References`)

**Regex patterns** are stored as class attributes so they're easy to extend:

```python
class SourcesFilter(LineBufferedFilter):
    _SOURCE_KEYWORDS = ("sources", "references", "citations", "lähteet", "viitteet")
    _HEADER_PATTERN = re.compile(...)  # derived from _SOURCE_KEYWORDS
    _SOURCE_LINE_PATTERN = re.compile(...)
```

To add new languages or keywords, extend `_SOURCE_KEYWORDS` and the derivation is automatic.

## Integration with `conversation.py`

Minimal surface-area change — the processor lives inside `_transform_stream`:

```python
async def _transform_stream(
    resp: aiohttp.ClientResponse,
    state: _StreamState,
) -> AsyncGenerator[AssistantContentDeltaDict]:
    processor = StreamingFilterProcessor([SourcesFilter()])
    role_yielded = False

    async for event_type, data in _parse_sse(resp):
        if event_type == "stream":
            delta = _map_stream_event(data)
            if delta is None:
                continue
            # Only filter text deltas, not thinking deltas
            if "content" in delta:
                filtered = processor.feed(delta["content"])
                if not filtered:
                    continue
                delta = {"content": filtered}
            if not role_yielded:
                yield {"role": "assistant"}
                role_yielded = True
            yield delta

        # ... session/result/error handling unchanged ...

    # Flush any remaining buffered content at end of stream
    final = processor.flush()
    if final:
        if not role_yielded:
            yield {"role": "assistant"}
        yield {"content": final}
```

The `speech = _last_assistant_text(chat_log)` call at the end of `_async_handle_message` is unchanged — it reads the already-filtered text from the chat log.

## Testing

A new test file: `tests/test_stream_filters.py`

**`StreamingFilterProcessor` tests:**
- Empty pipeline passes text through unchanged
- Single filter: feed and flush behavior matches filter
- Two-filter composition: content flows correctly
- Flush order: held content from F1 passes through F2 before F2 flushes its own content
- No duplicate or reordered output

**`LineBufferedFilter` tests:**
- Line accumulation across chunk boundaries (`"hel"`, `"lo\nwo"`, `"rld\n"` → two lines)
- Trailing line without newline is processed at flush
- `_process_line` returning `None` holds the line
- `_finalize` output appears at flush

**`SourcesFilter` tests:**
- Normal text passes through unchanged
- Basic cases: `# Sources` at end, `## References`, `**Citations:**`
- Multilingual: `# Lähteet`
- False alarm: `# Sources` followed by non-source line → header is emitted
- Sources followed by more content: section discarded, following content emitted
- Bare URLs after header
- List items (`- [link]`, `1. [link]`) after header
- Sources section running to end of stream: discarded
- Chunk boundaries: sources header split across two `feed()` calls
- Source line split across two `feed()` calls

## Future filter examples

These are **not** being built now, but the design must accommodate them:

- **`NoTTSBlockFilter`** — strips entire `<NO_TTS>...</NO_TTS>` blocks from the stream (content and markers both removed). Useful if/when the custom TTS provider adds tag-aware filtering.
- **`CodeBlockCollapseFilter`** — replaces triple-backtick code blocks with a placeholder like `(code block elided)` for TTS-friendly output.
- **`RegexRedactFilter`** — configurable regex-based redaction, e.g. stripping email addresses or API keys if the model accidentally echoes them.

Each of these can subclass `LineBufferedFilter` (or directly `StreamFilter` for character-level needs) and just implement the per-line or per-chunk logic.

## Open questions

None — design is complete pending user review.

## Out of scope

- `<NO_TTS>` tag stripping (user is moving this logic into their custom TTS provider)
- Rich chat log with hidden-to-TTS content (ruled out: HA's streaming TTS taps the chat log delta stream directly, so the same content is shown and spoken)
- Configurable sources keywords via UI (can be added later if needed; class attributes are fine for v1)
