# Cost & Usage Tracking Design

**Date:** 2026-04-11
**Status:** Draft

## Goal

Expose Claude API cost and token usage as Home Assistant sensor entities so users can observe spend over time, diagnose which agent is driving costs, and build their own alerts and automations on top of the data using HA's native long-term statistics, automations, and dashboards.

This is a **pure observability** feature. No budgets, no rate limiting, no automatic agent disabling. Users who want alerting build it from the sensor data using HA's native automation tooling.

## Non-Goals

- **Hard or soft budget enforcement.** The agent never refuses a turn due to cost. Hard cutoffs on a home voice assistant are a poor UX (imagine hitting the cap at 11pm when you ask to turn off the lights). Users who want a budget build it as a HA automation that reads these sensors.
- **Last-turn measurement sensors.** We deliberately expose only cumulative counters. HA's long-term statistics derive per-turn spikes and daily/weekly/monthly rollups from `total_increasing` sensors automatically. A separate `last_turn_*` sensor would double the entity count, suffer from stale-restore semantics (a 12-hour-old "last turn cost" value is misleading), and provide no information the statistics system can't already give you.
- **Per-model or per-subentry cost history across agent deletion.** Per-agent sensors die with the agent they belong to. The integration-level rollup preserves lifetime totals across subentry churn; per-agent history across deletions is out of scope.
- **Survival across full integration uninstall.** `RestoreSensor` handles restart and reload. A full uninstall deletes the config entry and its sensors; lifetime totals reset. A user who fully uninstalls the integration is implicitly opting into losing its state. No persistent external storage is planned.
- **Add-on changes.** All extraction happens in the integration from data the SDK already surfaces on `ResultMessage`. The add-on is untouched.

## Sensor Set

Five metrics per scope, each a cumulative `total_increasing` counter with `RestoreSensor`:

| Metric               | Unit     | Device Class | Source                                     |
| -------------------- | -------- | ------------ | ------------------------------------------ |
| `total_cost_usd`     | USD      | `monetary`   | `ResultMessage.total_cost_usd`             |
| `total_input_tokens` | `tokens` | —            | `ResultMessage.usage["input_tokens"]`      |
| `total_output_tokens`| `tokens` | —            | `ResultMessage.usage["output_tokens"]`     |
| `total_cache_read_tokens`  | `tokens` | —      | `ResultMessage.usage["cache_read_input_tokens"]` |
| `total_cache_write_tokens` | `tokens` | —      | `ResultMessage.usage["cache_creation_input_tokens"]` |

Two **scopes**:

1. **Per-agent** — five sensors attached to each conversation subentry's existing device. Answer "which agent is expensive right now?". Die with the agent they belong to.
2. **Integration-level** — five sensors attached to a new config-entry-level device representing the integration itself. Accumulate across every turn from every subentry. Survive individual subentry deletion. Die only on full config entry uninstall.

Total entity count:

- 1 agent: 10 sensors
- 2 agents: 15 sensors
- N agents: 5 × (N + 1) sensors

Cache-read and cache-write tokens are both included because they are free to extract from the SDK, they are priced very differently from regular tokens (cache reads ~10× cheaper, cache writes ~1.25× more expensive), and having historical data before prompt caching is enabled downstream is more valuable than adding them later from zero.

## Architecture

### New files

- `custom_components/ha_claude_agent/sensor.py` — the sensor platform.

### Modified files

- `custom_components/ha_claude_agent/__init__.py` — add `Platform.SENSOR` to platform forwarding.
- `custom_components/ha_claude_agent/const.py` — add `SIGNAL_USAGE_UPDATED` constant and `UsagePayload` dataclass.
- `custom_components/ha_claude_agent/conversation.py` — extend `_StreamResult` to capture `usage` dict, extract it in the `ResultMessage` match arm, dispatch `SIGNAL_USAGE_UPDATED` after the stream completes.
- `custom_components/ha_claude_agent/strings.json` + `translations/en.json` — five entity translation keys.

### Platform load order

```python
PLATFORMS = [Platform.SENSOR, Platform.CONVERSATION]
```

Sensors are forwarded before the conversation platform. This minimises the startup-race window in which the conversation entity could dispatch a usage signal before the sensors are connected to it. `async_forward_entry_setups` processes the list in order, and while platform setup can run concurrently internally, the intent is clear and documented.

### Dispatcher signal

A single HA dispatcher signal carries usage data. The signal name is constant at module scope so typos can't silently break fan-out:

```python
# const.py
SIGNAL_USAGE_UPDATED = f"{DOMAIN}_usage_updated"
```

The payload is a `(subentry_id: str, usage: UsagePayload)` tuple. Per-agent sensors filter on their own `subentry_id`; integration-level sensors accept every event.

This follows the idiomatic HA core pattern for intra-integration fan-out (used by MQTT, ZHA, and many others). Dispatcher signals sent to absent subscribers are silently dropped, which is the correct behavior for the startup-race edge case: at most one turn's usage could be lost at integration startup, and the alternative (blocking, complex ready-gates) is worse.

## Components

### `UsagePayload` dataclass

Lives in `const.py` (avoids module sprawl for one small dataclass):

```python
@dataclass(frozen=True, slots=True)
class UsagePayload:
    """Per-turn Claude usage, normalised from ResultMessage."""

    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
```

`frozen=True` because it's a message-passing value, not mutable state. `slots=True` to keep it cheap — one instance is created per turn.

### `_usage_from_result(result: ResultMessage) -> UsagePayload`

Pure helper in `conversation.py`. Normalises the SDK's loosely typed output:

- `result.total_cost_usd` can be `None` (SDK paths vary) → defaults to `0.0`.
- `result.usage` can be `None` → defaults to `{}`.
- `usage.get("input_tokens", 0)` and similar — missing keys default to `0`. Forward-compatible with SDK schema changes: unknown keys are ignored.

No validation or warning logs. If the SDK returns `None` that is simply how the SDK is behaving today and we don't want to spam warnings on every turn.

### `HAClaudeAgentUsageCounterSensor`

Single `SensorEntity` + `RestoreSensor` subclass. All instances are this class — there is no per-metric subclass explosion. The class takes constructor parameters that vary per instance:

- `device_info: DeviceInfo` — pre-built by the caller (subentry device for per-agent, the new integration device for integration-level).
- `unique_id: str` — includes `subentry_id` (or `entry_id`) and the metric name.
- `translation_key: str` — one of the five metric translation keys.
- `metric: str` — the `UsagePayload` attribute name to read on each update (`cost_usd`, `input_tokens`, etc.).
- `filter_subentry_id: str | None` — `None` = accept every signal (integration-level), otherwise filter to only that subentry's turns.
- `device_class` / `native_unit_of_measurement` — `MONETARY` + `"USD"` for the cost sensor, `None` + `"tokens"` for the four token sensors.

Fixed class attributes:

- `_attr_state_class = SensorStateClass.TOTAL_INCREASING` — HA statistics derives rollups automatically.
- `_attr_has_entity_name = True` — uses `translation_key` for the UI label.
- `_attr_should_poll = False` — updates are pushed via the dispatcher.

### Sensor setup (`async_setup_entry` in `sensor.py`)

For each conversation subentry, create five per-agent sensors filtering on that subentry's id, attached to that subentry's existing device.

Additionally create one integration-level device (in the device registry) and five integration-level sensors with `filter_subentry_id=None`, attached to it. The integration device is created in `sensor.async_setup_entry` — colocating device-registry writes with the platform that consumes them, keeping `__init__.py` free of platform-specific concerns.

### `async_added_to_hass` flow

1. `await super().async_added_to_hass()`
2. `last_data = await self.async_get_last_sensor_data()` — retrieve the restored value.
3. `self._attr_native_value = last_data.native_value if last_data else 0` (`0.0` for the cost sensor, `0` for token sensors).
4. Subscribe: `unsub = async_dispatcher_connect(hass, SIGNAL_USAGE_UPDATED, self._handle_usage)`
5. `self.async_on_remove(unsub)` — ensures clean teardown on entity removal.

### `_handle_usage(subentry_id, payload)` callback

```python
@callback
def _handle_usage(self, subentry_id: str, payload: UsagePayload) -> None:
    if self._filter_subentry_id is not None and subentry_id != self._filter_subentry_id:
        return
    self._attr_native_value += getattr(payload, self._metric)
    self.async_write_ha_state()
```

Synchronous callback decorated with `@callback` — no I/O, just arithmetic and a state write.

## Data Flow

```
User message
  ↓
conversation._async_handle_message
  ↓
POST /query → add-on → SDK → add-on SSE stream
  ↓
_deltas_from_sdk_stream consumes sdk_stream(resp)
  ↓
On ResultMessage: record total_cost_usd, num_turns, and (new) usage dict onto _StreamResult
  ↓
Stream completes → _async_handle_message has final _StreamResult
  ↓
usage_payload = _usage_from_result(result_state)
async_dispatcher_send(hass, SIGNAL_USAGE_UPDATED, subentry_id, usage_payload)
  ↓
Fan-out to all subscribed sensors:
  • Per-agent sensors for this subentry_id → increment their metric
  • Integration-level sensors → increment their metric (no filter)
  ↓
Each sensor: async_write_ha_state() → HA recorder → long-term statistics
```

### When to dispatch

Dispatch happens **after** the stream completes and **before** returning the `ConversationResult`. It happens in these cases:

- **Success path** — the ordinary case.
- **Soft error paths where a `ResultMessage` was received** — e.g. `error_max_turns` still bills for the partial work. You want to see that cost.
- **`assistant_error` paths where a `ResultMessage` was also received** — same reasoning.

It does **not** happen in these cases:

- **Addon unreachable / connection error before stream starts** — no turn ran, no billing event.
- **Stream interrupted mid-response, no `ResultMessage` received** — we have no usage data to record.
- **CLI / process errors raised as exceptions from `sdk_stream`** — no usage data.

In other words: if `result_state.cost_usd` is `None` (the field was never populated), skip the dispatch. If it's been populated (even alongside an error subtype), dispatch.

## Persistence & Lifecycle

| Event                        | Per-agent sensors                  | Integration-level sensors |
| ---------------------------- | ---------------------------------- | ------------------------- |
| HA restart                   | Restored via `RestoreSensor`       | Restored via `RestoreSensor` |
| Integration reload           | Restored                           | Restored                  |
| Subentry deletion            | **Deleted with the subentry**      | Preserved                 |
| Subentry recreation          | Fresh counters from 0              | Unaffected (keep ticking) |
| Full config entry uninstall  | Deleted                            | **Deleted**               |

`state_class = total_increasing` is lenient about the counter jumping backwards: HA treats a decrease as a reset and starts a new accumulation bucket in the statistics system. So a corrupted or missing restore manifests as a visible notch in the long-term graph rather than broken statistics.

## Error Handling

Narrow and boring:

- `ResultMessage.total_cost_usd = None` → treated as `0.0`. No log.
- `ResultMessage.usage = None` → treated as `{}`. No log.
- Missing or unknown keys in `usage` dict → defaulted to `0` via `.get`. Forward-compatible.
- Dispatch to absent subscribers (startup race) → silent drop. Up to one turn's usage potentially lost at integration startup, mitigated by the `[SENSOR, CONVERSATION]` forward order. Documented, not engineered around.
- Sensor restore failure (corrupted state) → starts at `0`, produces a visible notch in stats. No data corruption.
- Corrupt or absurd SDK values (negative cost, etc.) → trusted; no validation layer. If the SDK is lying to us, we have bigger problems than the cost sensor.

## Testing

The existing test suite (`tests/test_integration_stream.py`, `tests/test_addon_serialization.py`, `tests/test_models_sync.py`) is all pure-Python and does **not** use HA's `hass` test fixture — there is no `pytest-homeassistant-custom-component` dependency today. Introducing that infrastructure is its own project and explicitly out of scope for this spec.

Consequently the testing strategy is scoped to what can be verified with pure-Python unit tests. End-to-end verification (the full `conversation.async_process` path firing the dispatcher and all ten sensors incrementing) will be validated manually during implementation review and observed in production use.

### Unit — `_usage_from_result` (pure function)

Lives in `tests/test_usage.py`. Cases:

- Normal: all fields present, happy path.
- `total_cost_usd=None` → `cost_usd == 0.0`.
- `usage=None` → all token fields == 0.
- `usage={}` → all token fields == 0.
- Missing individual keys (e.g. only `input_tokens`, no output) → missing keys == 0.
- Extra unknown keys → ignored, no error.

### Unit — sensor callback logic (no `hass` fixture)

The callback logic on `HAClaudeAgentUsageCounterSensor` can be exercised without a full `hass` fixture by:

1. Constructing the sensor with a minimal `DeviceInfo` and the relevant parameters.
2. Monkey-patching `async_write_ha_state` to a no-op (or a spy).
3. Setting `_attr_native_value` to an initial state directly.
4. Calling `_handle_usage(subentry_id, payload)` as a plain method and asserting on `_attr_native_value`.

This doesn't exercise `async_added_to_hass` / `RestoreSensor` / real dispatcher wiring, but it does exercise the logic that's actually new and feature-specific (filter, metric extraction, addition). Cases:

- **Filter behavior**: per-agent sensor filtering on `subentry_id="A"` receives a signal for `"A"` → value increments; receives a signal for `"B"` → no change.
- **Integration-level accumulation**: `filter_subentry_id=None` → all signals accumulate regardless of subentry.
- **Per-metric extraction**: parametrised over all five metrics — dispatching a payload to a sensor configured for one metric increments by that metric's value only.

### Manual / observational verification

The following aspects rely on manual verification during the integration-review phase (running against a real HA instance):

- `RestoreSensor` correctly restoring values across reload and restart.
- Platform forward ordering correctly creating sensors before the conversation entity accepts messages.
- Dispatcher wiring and end-to-end flow from `ResultMessage` to sensor state write.
- Device registry: per-agent sensors appearing on the right subentry device and integration-level sensors on the new integration device.
- Long-term statistics rollups appearing in the HA UI after several turns.

If we later adopt `pytest-homeassistant-custom-component` in this repo, these manual checks become automatable — that's a separate infrastructure project and not blocking this one.

### No add-on test changes

The add-on is unmodified — no add-on test changes required.

## Files Changed

### New

- `custom_components/ha_claude_agent/sensor.py` — the sensor platform and the `HAClaudeAgentUsageCounterSensor` class.
- `tests/test_usage.py` — unit tests for `_usage_from_result` and the sensor callback logic (no `hass` fixture required).

### Modified

- `custom_components/ha_claude_agent/__init__.py` — `PLATFORMS = [Platform.SENSOR, Platform.CONVERSATION]`
- `custom_components/ha_claude_agent/const.py` — `SIGNAL_USAGE_UPDATED`, `UsagePayload`
- `custom_components/ha_claude_agent/conversation.py` — extend `_StreamResult` with usage fields, extract usage dict in the `ResultMessage` match arm, dispatch `SIGNAL_USAGE_UPDATED` after stream completes (only when usage/cost was received)
- `custom_components/ha_claude_agent/strings.json` — five new entity translation keys under a new `sensor` section
- `custom_components/ha_claude_agent/translations/en.json` — English text for the five keys

No changes to:

- `ha_claude_agent_addon/` — add-on is unaffected.
- `pyproject.toml` / `uv.lock` — no new dependencies.
- `manifest.json` — no new requirements.
- Existing test files — new test module is added; existing ones are left alone.

## Known Limits

1. **No cross-uninstall durability.** Full integration uninstall drops lifetime totals. Intentional.
2. **Up to one turn's usage may be lost during integration startup** if a request lands before `SENSOR` platform setup completes. Mitigated by forward order; acceptable given the narrowness of the window and the alternatives (ready gates, async coordination) being disproportionate.
3. **No per-agent cross-deletion history.** Deleting and recreating an agent resets its per-agent counters to zero. The integration-level counters preserve the lifetime view.
4. **Currency is fixed to USD.** The SDK reports cost in USD; we don't attempt conversion. Users in other currencies can build a template sensor if they want a converted view.
