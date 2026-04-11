# Cost & Usage Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Claude API cost and token usage as five `total_increasing` Home Assistant sensors per conversation agent, plus a matching set of integration-level rollup sensors that survive subentry deletion. Observability only — no budgets or rate limiting.

**Architecture:** After each conversation turn, `conversation.py` extracts `total_cost_usd` and the `usage` dict from the SDK's `ResultMessage` into a `UsagePayload` and broadcasts a `SIGNAL_USAGE_UPDATED` dispatcher signal with `(subentry_id, payload)`. A new `sensor.py` platform subscribes with two scopes: per-agent sensors filter on their own subentry id; integration-level sensors accept every event. Each sensor inherits `RestoreSensor` so cumulative counters survive restarts and reloads.

**Tech Stack:** Python 3.13, Home Assistant custom component APIs (`SensorEntity`, `RestoreSensor`, `SensorStateClass.TOTAL_INCREASING`, `async_dispatcher_send`/`async_dispatcher_connect`, device registry), `claude-agent-sdk`'s `ResultMessage`. Tests use `pytest` + `pytest-asyncio`, pure-Python only (no `hass` fixture).

**Spec reference:** `docs/superpowers/specs/2026-04-11-cost-usage-tracking-design.md`

---

## File Structure

### New files

- **`custom_components/ha_claude_agent/usage.py`** — `UsagePayload` frozen dataclass and `_usage_from_result` pure helper. No HA imports; only `dataclasses` and `claude_agent_sdk.ResultMessage`. Unit-testable without a `hass` fixture.
- **`custom_components/ha_claude_agent/sensor.py`** — `HAClaudeAgentUsageCounterSensor` class, `async_setup_entry` that creates per-agent and integration-level sensors, and an `_ensure_integration_device` helper that registers the config-entry-level rollup device.
- **`tests/test_usage.py`** — pure-Python unit tests for `_usage_from_result` and for the sensor callback logic (monkey-patching `async_write_ha_state`).

### Modified files

- **`custom_components/ha_claude_agent/const.py`** — add `SIGNAL_USAGE_UPDATED` constant.
- **`custom_components/ha_claude_agent/conversation.py`** — extend `_StreamResult` with `usage_dict`, capture it in the `ResultMessage` match arm, dispatch the signal after stream completion when usage was received.
- **`custom_components/ha_claude_agent/__init__.py`** — add `Platform.SENSOR` (first) to `PLATFORMS`.
- **`custom_components/ha_claude_agent/strings.json`** — add a top-level `entity.sensor.*` section with five translation keys.
- **`custom_components/ha_claude_agent/translations/en.json`** — mirror the `strings.json` additions with English text.

Files that change together:
- `usage.py` + `test_usage.py` (Task 1) — pure logic and its tests.
- `sensor.py` + `test_usage.py` (Task 2) — class plus its callback tests.
- `const.py` + `strings.json` + `translations/en.json` (Task 3) — constants and UI text.
- `conversation.py` (Task 4) — pipeline connection; no new tests (conversation.py isn't importable in the test environment).
- `__init__.py` + `sensor.py` (Task 5) — wiring (platform forward + setup_entry).

---

## Task 1: `usage.py` — `UsagePayload` + `_usage_from_result`

**Files:**
- Create: `custom_components/ha_claude_agent/usage.py`
- Test: `tests/test_usage.py`

### - [ ] Step 1.1: Write failing tests for `_usage_from_result`

Create `tests/test_usage.py` with this exact content:

```python
"""Unit tests for usage.py — pure-Python, no hass fixture required."""

from __future__ import annotations

from claude_agent_sdk import ResultMessage

from custom_components.ha_claude_agent.usage import (
    UsagePayload,
    _usage_from_result,
)


def _make_result(
    *,
    total_cost_usd: float | None = 0.012,
    usage: dict | None = None,
) -> ResultMessage:
    """Construct a minimal ResultMessage for tests.

    ResultMessage has many required fields; we pass reasonable defaults
    for the ones that aren't under test.
    """
    return ResultMessage(
        subtype="success",
        duration_ms=1234,
        duration_api_ms=1000,
        is_error=False,
        num_turns=1,
        session_id="sess_test",
        total_cost_usd=total_cost_usd,
        usage=usage,
    )


def test_usage_from_result_happy_path() -> None:
    result = _make_result(
        total_cost_usd=0.0123,
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 200,
            "cache_creation_input_tokens": 10,
        },
    )
    payload = _usage_from_result(result)
    assert payload == UsagePayload(
        cost_usd=0.0123,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=200,
        cache_write_tokens=10,
    )


def test_usage_from_result_none_cost_becomes_zero() -> None:
    result = _make_result(total_cost_usd=None, usage={"input_tokens": 5})
    payload = _usage_from_result(result)
    assert payload.cost_usd == 0.0
    assert payload.input_tokens == 5


def test_usage_from_result_none_usage_becomes_zero_tokens() -> None:
    result = _make_result(total_cost_usd=0.01, usage=None)
    payload = _usage_from_result(result)
    assert payload.cost_usd == 0.01
    assert payload.input_tokens == 0
    assert payload.output_tokens == 0
    assert payload.cache_read_tokens == 0
    assert payload.cache_write_tokens == 0


def test_usage_from_result_empty_usage_dict() -> None:
    result = _make_result(total_cost_usd=0.0, usage={})
    payload = _usage_from_result(result)
    assert payload.cost_usd == 0.0
    assert payload.input_tokens == 0
    assert payload.output_tokens == 0
    assert payload.cache_read_tokens == 0
    assert payload.cache_write_tokens == 0


def test_usage_from_result_missing_individual_keys() -> None:
    result = _make_result(
        total_cost_usd=0.02,
        usage={"input_tokens": 42},  # no output_tokens or cache fields
    )
    payload = _usage_from_result(result)
    assert payload.input_tokens == 42
    assert payload.output_tokens == 0
    assert payload.cache_read_tokens == 0
    assert payload.cache_write_tokens == 0


def test_usage_from_result_extra_unknown_keys_ignored() -> None:
    result = _make_result(
        total_cost_usd=0.01,
        usage={
            "input_tokens": 1,
            "output_tokens": 2,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 4,
            "some_future_token_type": 9999,
        },
    )
    payload = _usage_from_result(result)
    assert payload.input_tokens == 1
    assert payload.output_tokens == 2
    assert payload.cache_read_tokens == 3
    assert payload.cache_write_tokens == 4
    # Unknown keys are silently ignored — no error, no warning.
```

### - [ ] Step 1.2: Run the test to verify it fails

Run:

```bash
uv run pytest tests/test_usage.py -v
```

Expected: all tests FAIL with `ModuleNotFoundError: No module named 'custom_components.ha_claude_agent.usage'` or similar import error.

### - [ ] Step 1.3: Create `usage.py` with the dataclass and helper

Create `custom_components/ha_claude_agent/usage.py` with this exact content:

```python
"""Per-turn Claude usage extraction.

Pure module with no Home Assistant imports — allows unit testing
without a hass fixture, and keeps the `_usage_from_result` helper
decoupled from the conversation entity.
"""

from __future__ import annotations

from dataclasses import dataclass

from claude_agent_sdk import ResultMessage


@dataclass(frozen=True, slots=True)
class UsagePayload:
    """Per-turn Claude usage, normalised from ResultMessage.

    All fields are non-optional and default to zero when the SDK
    returns None or missing data. This keeps downstream code
    (sensors, dispatcher callbacks) from having to null-check.
    """

    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


def _usage_from_result(result: ResultMessage) -> UsagePayload:
    """Extract a UsagePayload from a ResultMessage.

    Handles None values and missing usage-dict keys by defaulting
    to zero. Forward-compatible: unknown keys in ``result.usage``
    are silently ignored.
    """
    cost = result.total_cost_usd if result.total_cost_usd is not None else 0.0
    usage = result.usage or {}
    return UsagePayload(
        cost_usd=float(cost),
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
    )
```

Note the `or 0` inside `int(...)` calls: the SDK could return `None` as a dict value (not just a missing key), and `int(None)` would raise. Belt and braces.

### - [ ] Step 1.4: Run the test to verify it passes

Run:

```bash
uv run pytest tests/test_usage.py -v
```

Expected: all six tests PASS.

If any test fails, do NOT modify the test — the implementation is wrong. Re-check the `_usage_from_result` body against the test expectations.

### - [ ] Step 1.5: Run lint and type-check

Run both in sequence:

```bash
uv run ruff check custom_components/ha_claude_agent/usage.py tests/test_usage.py
uv run ruff format --check custom_components/ha_claude_agent/usage.py tests/test_usage.py
uv run mypy custom_components/ha_claude_agent/usage.py
```

Expected: no warnings or errors. Fix any style issues with `uv run ruff format custom_components/ha_claude_agent/usage.py tests/test_usage.py` before continuing.

### - [ ] Step 1.6: Commit

```bash
git add custom_components/ha_claude_agent/usage.py tests/test_usage.py
git commit -m "feat(integration): add UsagePayload and _usage_from_result helper

Pure module for normalising ResultMessage cost + usage into a frozen
dataclass. Handles None values and missing/extra usage-dict keys.
Unit-tested without a hass fixture."
```

---

## Task 2: `SIGNAL_USAGE_UPDATED` constant

**Files:**
- Modify: `custom_components/ha_claude_agent/const.py`

### - [ ] Step 2.1: Add the constant

Append to the bottom of `custom_components/ha_claude_agent/const.py` (after `QUERY_TIMEOUT_SECONDS = 300`):

```python

# Dispatcher signal fired by conversation.py after each turn with
# (subentry_id: str, payload: UsagePayload). Subscribed to by
# sensor.py for cost/usage counter updates.
SIGNAL_USAGE_UPDATED = f"{DOMAIN}_usage_updated"
```

### - [ ] Step 2.2: Verify nothing broke

Run:

```bash
uv run ruff check custom_components/ha_claude_agent/const.py
uv run mypy custom_components/ha_claude_agent/const.py
uv run pytest tests/ -v
```

Expected: all checks pass, all existing tests still pass.

### - [ ] Step 2.3: Commit

```bash
git add custom_components/ha_claude_agent/const.py
git commit -m "feat(integration): add SIGNAL_USAGE_UPDATED dispatcher signal name"
```

---

## Task 3: `sensor.py` — sensor class with TDD

**Files:**
- Create: `custom_components/ha_claude_agent/sensor.py` (partial — class only in this task)
- Test: `tests/test_usage.py` (append)

### - [ ] Step 3.1: Write failing tests for sensor callback logic

Append to `tests/test_usage.py`:

```python
# --------------------------------------------------------------------
# Sensor callback logic tests
# --------------------------------------------------------------------
# These tests exercise HAClaudeAgentUsageCounterSensor._handle_usage
# without a hass fixture by:
#   1. Constructing the sensor directly
#   2. Monkey-patching async_write_ha_state to a no-op
#   3. Setting _attr_native_value manually
#   4. Calling _handle_usage and asserting on _attr_native_value
# This does not exercise async_added_to_hass / RestoreSensor / real
# dispatcher wiring — those are verified manually on a live HA.
# --------------------------------------------------------------------

from homeassistant.helpers.entity import DeviceInfo

from custom_components.ha_claude_agent.const import DOMAIN
from custom_components.ha_claude_agent.sensor import (
    HAClaudeAgentUsageCounterSensor,
)


def _make_sensor(
    *,
    metric: str,
    filter_subentry_id: str | None,
    initial_value: float | int = 0,
    unit: str = "USD",
) -> HAClaudeAgentUsageCounterSensor:
    sensor = HAClaudeAgentUsageCounterSensor(
        device_info=DeviceInfo(identifiers={(DOMAIN, "test")}),
        unique_id=f"test_{metric}_{filter_subentry_id or 'all'}",
        translation_key=f"total_{metric}",
        metric=metric,
        filter_subentry_id=filter_subentry_id,
        native_unit_of_measurement=unit,
        device_class=None,
    )
    sensor._attr_native_value = initial_value
    # Stub out the HA state write — we're testing logic, not HA wiring.
    sensor.async_write_ha_state = lambda: None  # type: ignore[method-assign]
    return sensor


def _make_payload(
    *,
    cost_usd: float = 0.01,
    input_tokens: int = 10,
    output_tokens: int = 20,
    cache_read_tokens: int = 30,
    cache_write_tokens: int = 40,
) -> UsagePayload:
    return UsagePayload(
        cost_usd=cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )


def test_sensor_per_agent_filter_accepts_matching_subentry() -> None:
    sensor = _make_sensor(metric="cost_usd", filter_subentry_id="A")
    sensor._handle_usage("A", _make_payload(cost_usd=0.05))
    assert sensor._attr_native_value == 0.05


def test_sensor_per_agent_filter_rejects_other_subentry() -> None:
    sensor = _make_sensor(
        metric="cost_usd", filter_subentry_id="A", initial_value=1.23
    )
    sensor._handle_usage("B", _make_payload(cost_usd=0.05))
    assert sensor._attr_native_value == 1.23  # unchanged


def test_sensor_integration_accepts_any_subentry() -> None:
    sensor = _make_sensor(metric="cost_usd", filter_subentry_id=None)
    sensor._handle_usage("A", _make_payload(cost_usd=0.01))
    sensor._handle_usage("B", _make_payload(cost_usd=0.02))
    assert sensor._attr_native_value == 0.03


def test_sensor_accumulates_across_calls() -> None:
    sensor = _make_sensor(metric="input_tokens", filter_subentry_id="A", unit="tokens")
    sensor._handle_usage("A", _make_payload(input_tokens=100))
    sensor._handle_usage("A", _make_payload(input_tokens=50))
    assert sensor._attr_native_value == 150


def test_sensor_reads_correct_metric_field() -> None:
    """Each metric-specific sensor only accumulates its own field."""
    payload = _make_payload(
        cost_usd=0.01,
        input_tokens=100,
        output_tokens=200,
        cache_read_tokens=300,
        cache_write_tokens=400,
    )
    for metric, expected, unit in [
        ("cost_usd", 0.01, "USD"),
        ("input_tokens", 100, "tokens"),
        ("output_tokens", 200, "tokens"),
        ("cache_read_tokens", 300, "tokens"),
        ("cache_write_tokens", 400, "tokens"),
    ]:
        sensor = _make_sensor(
            metric=metric, filter_subentry_id="A", unit=unit
        )
        sensor._handle_usage("A", payload)
        assert sensor._attr_native_value == expected, f"metric={metric}"
```

### - [ ] Step 3.2: Run the test to verify it fails

Run:

```bash
uv run pytest tests/test_usage.py -v
```

Expected: the six `_usage_from_result` tests still pass; the five new sensor tests FAIL with `ModuleNotFoundError: No module named 'custom_components.ha_claude_agent.sensor'`.

### - [ ] Step 3.3: Create `sensor.py` with the sensor class

Create `custom_components/ha_claude_agent/sensor.py` with this exact content:

```python
"""Sensor platform — cost and usage counters per agent and integration-wide.

Two scopes of sensors, both sharing the HAClaudeAgentUsageCounterSensor
class:

1. **Per-agent** — filtered by subentry_id, attached to each conversation
   subentry's existing device. Dies with the agent; diagnostic only.
2. **Integration-level** — accepts every signal, attached to a new
   config-entry-level device. Survives subentry churn. Lifetime totals.

All sensors are `total_increasing` cumulative counters and inherit
`RestoreSensor` for restart/reload durability.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_USAGE_UPDATED
from .usage import UsagePayload

_LOGGER = logging.getLogger(__name__)


class HAClaudeAgentUsageCounterSensor(RestoreSensor, SensorEntity):
    """Cumulative cost or token counter for one metric, one scope.

    A single class serves both per-agent and integration-level sensors.
    The difference is `filter_subentry_id`:
      - ``str`` — filters; only events matching this subentry accumulate.
      - ``None`` — accepts every event (integration-level rollup).
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        *,
        device_info: DeviceInfo,
        unique_id: str,
        translation_key: str,
        metric: str,
        filter_subentry_id: str | None,
        native_unit_of_measurement: str,
        device_class: SensorDeviceClass | None,
    ) -> None:
        """Initialise a usage counter sensor.

        Parameters
        ----------
        device_info:
            Pre-built DeviceInfo pointing at either the subentry device
            (per-agent) or the integration-level device (rollup).
        unique_id:
            Globally unique ID; typically includes subentry_id or
            entry_id + the metric name.
        translation_key:
            Frontend translation key under ``entity.sensor.*``.
        metric:
            The ``UsagePayload`` attribute name to read on each update
            (e.g. ``"cost_usd"``, ``"input_tokens"``).
        filter_subentry_id:
            ``None`` to accept every signal (integration-level), or a
            specific subentry_id string to filter on.
        native_unit_of_measurement:
            e.g. ``"USD"`` for cost, ``"tokens"`` for token counters.
        device_class:
            ``SensorDeviceClass.MONETARY`` for cost, ``None`` for tokens.
        """
        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_translation_key = translation_key
        self._attr_native_unit_of_measurement = native_unit_of_measurement
        self._attr_device_class = device_class
        self._metric = metric
        self._filter_subentry_id = filter_subentry_id
        # Initial native value — may be overwritten by restored state
        # in async_added_to_hass. 0.0 works as a starting point for
        # both float and int accumulation.
        self._attr_native_value = 0.0 if metric == "cost_usd" else 0

    async def async_added_to_hass(self) -> None:
        """Restore prior value, then subscribe to the usage dispatcher."""
        await super().async_added_to_hass()

        last_data = await self.async_get_last_sensor_data()
        if last_data is not None and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value  # type: ignore[assignment]

        unsub = async_dispatcher_connect(
            self.hass, SIGNAL_USAGE_UPDATED, self._handle_usage
        )
        self.async_on_remove(unsub)

    @callback
    def _handle_usage(self, subentry_id: str, payload: UsagePayload) -> None:
        """Apply a usage update to this sensor's counter."""
        if (
            self._filter_subentry_id is not None
            and subentry_id != self._filter_subentry_id
        ):
            return
        delta: Any = getattr(payload, self._metric)
        # Defensive: if the sensor was constructed with a zero int and the
        # metric is cost_usd (shouldn't happen with correct factory use,
        # but harmless), coerce to float to avoid TypeError on int += float.
        if isinstance(delta, float) and not isinstance(self._attr_native_value, float):
            self._attr_native_value = float(self._attr_native_value)
        self._attr_native_value += delta
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up usage counter sensors from a config entry.

    Creates, for each conversation subentry, five per-agent sensors
    filtered on that subentry. Also creates five integration-level
    sensors (filter_subentry_id=None) attached to a new integration
    device at the config entry level.
    """
    # Per-agent sensors — five per subentry, attached to the subentry's
    # existing device.
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue
        subentry_device = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
        )
        async_add_entities(
            _build_counter_set(
                device_info=subentry_device,
                unique_id_prefix=f"{subentry.subentry_id}",
                filter_subentry_id=subentry.subentry_id,
            ),
            config_subentry_id=subentry.subentry_id,
        )

    # Integration-level sensors — one set per config entry, attached to
    # a new config-entry-level "totals" device.
    integration_device = DeviceInfo(
        identifiers={(DOMAIN, config_entry.entry_id, "totals")},
        name="HA Claude Agent (Totals)",
        manufacturer="Anthropic",
        entry_type=None,  # Service-type; HA derives from DeviceEntryType default
    )
    # Explicit DeviceEntryType.SERVICE for consistency with the per-agent
    # devices defined in conversation.py.
    from homeassistant.helpers import device_registry as dr

    integration_device["entry_type"] = dr.DeviceEntryType.SERVICE  # type: ignore[typeddict-item]

    async_add_entities(
        _build_counter_set(
            device_info=integration_device,
            unique_id_prefix=f"{config_entry.entry_id}_totals",
            filter_subentry_id=None,
        ),
    )


_METRIC_SPECS: list[tuple[str, str, str, SensorDeviceClass | None]] = [
    # (metric, translation_key, unit, device_class)
    ("cost_usd", "total_cost_usd", "USD", SensorDeviceClass.MONETARY),
    ("input_tokens", "total_input_tokens", "tokens", None),
    ("output_tokens", "total_output_tokens", "tokens", None),
    ("cache_read_tokens", "total_cache_read_tokens", "tokens", None),
    ("cache_write_tokens", "total_cache_write_tokens", "tokens", None),
]


def _build_counter_set(
    *,
    device_info: DeviceInfo,
    unique_id_prefix: str,
    filter_subentry_id: str | None,
) -> list[HAClaudeAgentUsageCounterSensor]:
    """Build the five-sensor set for one scope (per-agent or integration)."""
    return [
        HAClaudeAgentUsageCounterSensor(
            device_info=device_info,
            unique_id=f"{unique_id_prefix}_{translation_key}",
            translation_key=translation_key,
            metric=metric,
            filter_subentry_id=filter_subentry_id,
            native_unit_of_measurement=unit,
            device_class=device_class,
        )
        for metric, translation_key, unit, device_class in _METRIC_SPECS
    ]
```

### - [ ] Step 3.4: Run the test to verify it passes

Run:

```bash
uv run pytest tests/test_usage.py -v
```

Expected: all eleven tests PASS (six helper + five sensor).

### - [ ] Step 3.5: Run lint and type-check

```bash
uv run ruff check custom_components/ha_claude_agent/sensor.py tests/test_usage.py
uv run ruff format --check custom_components/ha_claude_agent/sensor.py tests/test_usage.py
uv run mypy custom_components/ha_claude_agent/sensor.py
```

Expected: clean. Run `uv run ruff format custom_components/ha_claude_agent/sensor.py tests/test_usage.py` to auto-fix formatting if needed.

If mypy complains about `integration_device["entry_type"]` assignment: the alternative is to construct the `DeviceInfo` dict literal in one expression including `entry_type=dr.DeviceEntryType.SERVICE`. Prefer that if it type-checks cleanly.

### - [ ] Step 3.6: Commit

```bash
git add custom_components/ha_claude_agent/sensor.py tests/test_usage.py
git commit -m "feat(integration): add usage counter sensor platform

New sensor.py exposes HAClaudeAgentUsageCounterSensor — one class
serving both per-agent (filtered by subentry_id) and integration-level
(unfiltered) scopes. Five metrics each: cost_usd, input/output/cache-
read/cache-write tokens. All total_increasing + RestoreSensor.

Callback logic is unit-tested without a hass fixture by monkey-
patching async_write_ha_state. async_added_to_hass / RestoreSensor /
dispatcher wiring are verified manually on a live HA."
```

---

## Task 4: Translation keys in `strings.json` and `en.json`

**Files:**
- Modify: `custom_components/ha_claude_agent/strings.json`
- Modify: `custom_components/ha_claude_agent/translations/en.json`

### - [ ] Step 4.1: Add entity translation section to `strings.json`

Open `custom_components/ha_claude_agent/strings.json`. Find the closing brace of the top-level object (the `}` on the last line). Immediately before it, and after the closing brace of the existing `"config_subentries"` block, add a comma and then this new section:

```json
  "entity": {
    "sensor": {
      "total_cost_usd": {
        "name": "Total cost"
      },
      "total_input_tokens": {
        "name": "Total input tokens"
      },
      "total_output_tokens": {
        "name": "Total output tokens"
      },
      "total_cache_read_tokens": {
        "name": "Total cache-read tokens"
      },
      "total_cache_write_tokens": {
        "name": "Total cache-write tokens"
      }
    }
  }
```

Result — the end of the file should look like:

```json
        }
      }
    }
  },
  "entity": {
    "sensor": {
      "total_cost_usd": {
        "name": "Total cost"
      },
      "total_input_tokens": {
        "name": "Total input tokens"
      },
      "total_output_tokens": {
        "name": "Total output tokens"
      },
      "total_cache_read_tokens": {
        "name": "Total cache-read tokens"
      },
      "total_cache_write_tokens": {
        "name": "Total cache-write tokens"
      }
    }
  }
}
```

Note the comma after `"config_subentries": { ... }` — JSON requires it because `entity` is a sibling key. If this was forgotten the file is invalid JSON.

### - [ ] Step 4.2: Mirror the additions in `translations/en.json`

Apply the exact same change to `custom_components/ha_claude_agent/translations/en.json` — the file should have the same content as `strings.json`. Open it and add the same `"entity": { ... }` block at the same position. The two files should differ only in the content of future non-English translations, which we don't have yet.

### - [ ] Step 4.3: Verify both files are valid JSON

```bash
uv run python -c "import json; json.load(open('custom_components/ha_claude_agent/strings.json'))"
uv run python -c "import json; json.load(open('custom_components/ha_claude_agent/translations/en.json'))"
```

Expected: no output (success). If you see `JSONDecodeError`, check for missing commas, trailing commas, or mismatched braces.

### - [ ] Step 4.4: Commit

```bash
git add custom_components/ha_claude_agent/strings.json custom_components/ha_claude_agent/translations/en.json
git commit -m "feat(integration): add sensor entity translation keys"
```

---

## Task 5: `conversation.py` — capture usage and dispatch the signal

**Files:**
- Modify: `custom_components/ha_claude_agent/conversation.py`

This task does not have unit tests — `conversation.py` is not importable in the test environment (it transitively imports `homeassistant.components.conversation`, which requires `hassil`, not in `uv.lock`). End-to-end verification is deferred to the manual live-HA check in Task 7.

### - [ ] Step 5.1: Extend imports

Open `custom_components/ha_claude_agent/conversation.py`. Find the existing imports block (`from homeassistant.helpers import intent` at the end of the HA imports). Add a new import for the dispatcher helper and for `UsagePayload` + `_usage_from_result`:

Find:

```python
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
```

Replace with:

```python
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
```

Find:

```python
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
```

Replace with:

```python
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
    SIGNAL_USAGE_UPDATED,
)
from .helpers import build_system_prompt
from .models import QueryRequest
from .stream import sdk_stream
from .usage import UsagePayload, _usage_from_result
```

### - [ ] Step 5.2: Extend `_StreamResult` with a usage-dict field

Find:

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

Replace with:

```python
@dataclass
class _StreamResult:
    """Mutable holder for stream side-effects consumed by the delta adapter."""

    session_id: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    result_error_subtype: str | None = None  # ResultMessage.subtype if != "success"
    assistant_error: str | None = None  # AssistantMessage.error if set
    usage_dict: dict[str, int] | None = None  # Raw ResultMessage.usage dict
```

### - [ ] Step 5.3: Capture `usage` in the `ResultMessage` match arm

Find the existing match arm for `ResultMessage` in `_deltas_from_sdk_stream`:

```python
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
```

Replace with:

```python
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
```

### - [ ] Step 5.4: Dispatch `SIGNAL_USAGE_UPDATED` after stream completes

The signal must be dispatched after the stream finishes (and after the session-mapping is stored, so session state is consistent) but before any error-response early return consumes the `result_state`. Critically it must also fire on soft-error paths when usage data was received (e.g. `error_max_turns` still bills us).

Find this block in `_async_handle_message`:

```python
        _LOGGER.info(
            "Stream complete: session=%s, cost=$%s, turns=%s, "
            "result_error=%s, assistant_error=%s",
            result_state.session_id,
            result_state.cost_usd,
            result_state.num_turns,
            result_state.result_error_subtype,
            result_state.assistant_error,
        )

        # Store session mapping BEFORE returning error responses — even on
        # soft errors, we want the user's prior conversation context to be
        # preserved for retry. The session is still valid on Claude's side;
        # it's only the current turn that failed.
        if result_state.session_id:
            runtime_data.sessions[chat_log.conversation_id] = result_state.session_id
```

Replace with:

```python
        _LOGGER.info(
            "Stream complete: session=%s, cost=$%s, turns=%s, "
            "result_error=%s, assistant_error=%s",
            result_state.session_id,
            result_state.cost_usd,
            result_state.num_turns,
            result_state.result_error_subtype,
            result_state.assistant_error,
        )

        # Store session mapping BEFORE returning error responses — even on
        # soft errors, we want the user's prior conversation context to be
        # preserved for retry. The session is still valid on Claude's side;
        # it's only the current turn that failed.
        if result_state.session_id:
            runtime_data.sessions[chat_log.conversation_id] = result_state.session_id

        # Dispatch usage signal to sensor platform. Fires even on soft errors
        # (error_max_turns, assistant_error) — the API still billed for those.
        # cost_usd is the presence proxy: if it is None, no ResultMessage was
        # received, so there is nothing to record.
        if result_state.cost_usd is not None:
            usage_payload: UsagePayload = _usage_from_result_from_state(result_state)
            async_dispatcher_send(
                self.hass,
                SIGNAL_USAGE_UPDATED,
                self.subentry.subentry_id,
                usage_payload,
            )
```

### - [ ] Step 5.5: Add the `_usage_from_result_from_state` helper

`_usage_from_result` in `usage.py` takes a `ResultMessage`; at this point in `conversation.py` we have a `_StreamResult` with the already-extracted fields. Add a tiny bridge helper at the bottom of `conversation.py` (below `_delta_from_anthropic_event`):

```python


def _usage_from_result_from_state(state: _StreamResult) -> UsagePayload:
    """Build a UsagePayload from the already-captured _StreamResult.

    Kept as a small bridge because the pure helper in ``usage.py`` takes
    a ``ResultMessage``, but at dispatch time we only have the extracted
    fields. Mirrors the default-handling semantics of ``_usage_from_result``.
    """
    usage = state.usage_dict or {}
    cost = state.cost_usd if state.cost_usd is not None else 0.0
    return UsagePayload(
        cost_usd=float(cost),
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
    )
```

Note: we *could* reuse `_usage_from_result` from `usage.py` if we kept a `ResultMessage` around instead of exploding it into `_StreamResult` fields, but that would require restructuring the stream consumer. This bridge helper is the smaller change and documents the duplication honestly.

Mark this tiny duplication with a `# noqa` only if lint complains; otherwise leave it.

### - [ ] Step 5.6: Remove the `_usage_from_result` import if unused

`conversation.py` now only uses `UsagePayload` from `usage.py`. Update the import at the top of the file:

Find:

```python
from .usage import UsagePayload, _usage_from_result
```

Replace with:

```python
from .usage import UsagePayload
```

(`_usage_from_result_from_state` is local to `conversation.py`.)

### - [ ] Step 5.7: Run existing tests and lint

```bash
uv run ruff check custom_components/ha_claude_agent/conversation.py
uv run ruff format --check custom_components/ha_claude_agent/conversation.py
uv run mypy custom_components/ha_claude_agent/conversation.py
uv run pytest tests/ -v
```

Expected: all existing tests still pass (none reference `_StreamResult` structure directly), no type errors, clean lint. If the lint reports unused imports, remove them.

### - [ ] Step 5.8: Commit

```bash
git add custom_components/ha_claude_agent/conversation.py
git commit -m "feat(integration): dispatch SIGNAL_USAGE_UPDATED after each turn

Captures ResultMessage.usage into _StreamResult and, after the stream
completes with cost data present, fires the usage dispatcher signal
consumed by sensor.py. Fires on soft-error paths too (error_max_turns,
assistant_error) because those still bill for partial work."
```

---

## Task 6: Forward `Platform.SENSOR` in `__init__.py`

**Files:**
- Modify: `custom_components/ha_claude_agent/__init__.py`

### - [ ] Step 6.1: Update `PLATFORMS`

Find:

```python
PLATFORMS = [Platform.CONVERSATION]
```

Replace with:

```python
# SENSOR is first so its async_setup_entry runs before CONVERSATION
# attempts to dispatch usage signals — minimises the startup-race window
# where a turn could complete before sensors are subscribed.
PLATFORMS = [Platform.SENSOR, Platform.CONVERSATION]
```

### - [ ] Step 6.2: Verify lint and existing tests

```bash
uv run ruff check custom_components/ha_claude_agent/__init__.py
uv run mypy custom_components/ha_claude_agent/__init__.py
uv run pytest tests/ -v
```

Expected: clean, all tests pass.

### - [ ] Step 6.3: Run the full verification suite

Run the same commands the repo's CI runs, to make sure nothing else is broken:

```bash
uv run ruff check custom_components/ ha_claude_agent_addon/src/ tests/
uv run ruff format --check custom_components/ ha_claude_agent_addon/src/ tests/
uv run mypy custom_components/ha_claude_agent/ ha_claude_agent_addon/src/
uv run pytest tests/ -v
```

Expected: fully clean. If pre-commit hooks are installed, also run `uv run pre-commit run --all-files` for parity with the new CI workflow (see commit `dffb0d2`).

### - [ ] Step 6.4: Commit

```bash
git add custom_components/ha_claude_agent/__init__.py
git commit -m "feat(integration): forward Platform.SENSOR for usage counter sensors

SENSOR is ordered before CONVERSATION so sensor subscriptions are wired
up before the conversation entity can emit usage-updated signals. At
most one turn's usage could be lost during a narrow startup window if
a request lands before SENSOR platform setup completes — acceptable."
```

---

## Task 7: Manual live-HA verification

**Files:** none (manual verification)

The spec explicitly scopes these checks to manual verification on a live HA instance, because the repo does not use `pytest-homeassistant-custom-component`:

### - [ ] Step 7.1: Install the integration in a test HA instance

Deploy the branch to a test HA (dev add-on + HACS install from local repo or by pushing to a test branch and pointing HACS at it). Restart HA, confirm the integration loads without errors.

### - [ ] Step 7.2: Verify sensor entities exist in the UI

Navigate to **Settings → Devices & services → HA Claude Agent**. Expected:

- Each conversation subentry device now has **five new sensors** under its "Sensors" section: Total cost, Total input tokens, Total output tokens, Total cache-read tokens, Total cache-write tokens.
- A **new "HA Claude Agent (Totals)" device** appears alongside the per-agent devices, with the same five sensors under it.

Record the initial values: all should be `0` or `0.0`.

### - [ ] Step 7.3: Fire a conversation turn and verify accumulation

Use the conversation panel to send a trivial message (e.g. "Hello"). After the response completes:

- The per-agent sensors for the subentry you used should show non-zero values for at least `Total cost` and `Total input/output tokens`.
- The integration-level (Totals) sensors should show the same values (since there is only one agent).
- Cache-read/cache-write may be 0 unless prompt caching is enabled.

### - [ ] Step 7.4: Fire a second turn and verify monotonic increase

Send another message. Both the per-agent and integration-level counters should strictly increase (or stay the same for cache fields).

### - [ ] Step 7.5: Reload the integration — verify restore

Go to the integration page → overflow menu → Reload. After reload:

- All sensor values should be the same as before the reload (restored from HA's state store).
- Send another message: counters should resume accumulation from the restored value, not restart from zero.

### - [ ] Step 7.6: Restart Home Assistant — verify restart durability

Full HA restart. After restart, repeat Step 7.5 checks. Values should survive the restart.

### - [ ] Step 7.7: Delete the subentry — verify integration-level counters persist

Open the subentry, delete it. The per-agent sensors should disappear with it. **The integration-level "Totals" sensors should retain their values unchanged.** Create a new subentry: its per-agent sensors should start at 0, and the integration-level counters should continue accumulating from their retained values on the next turn.

### - [ ] Step 7.8: Verify long-term statistics

Navigate to **Settings → Analytics → Long-term statistics** (or Developer Tools → Statistics). The five `total_cost_usd` / `total_*_tokens` sensors should appear as statistics-enabled entities with `total_increasing` state class. After a few turns, daily statistics should begin to populate.

### - [ ] Step 7.9: If any step fails, fix and re-verify

File a bug back against this plan if behaviour deviates from expectations. Common failure modes to check first:

- Translation keys missing → sensor names show as raw `total_cost_usd`. Check `strings.json` / `en.json`.
- Sensors showing `unknown` after reload → `RestoreSensor` wiring issue, check `async_added_to_hass`.
- Integration-level device not appearing → identifier collision with per-agent devices; check the `(DOMAIN, entry.entry_id, "totals")` tuple.
- Counters not incrementing → dispatcher signal not received; check that `async_dispatcher_send` fires (add a temporary log in the subscriber), check platform load order, check that `result_state.cost_usd is not None`.

### - [ ] Step 7.10: Open the PR

Only after all manual checks pass. Push the branch and open a PR targeting `main`. The PR description should link the spec and summarise:

- New sensor platform with five counters per agent + five integration-level totals
- Observability only, no budget enforcement
- Test coverage limited to pure-Python unit tests (see spec testing section for rationale)
- Manual verification completed

---

## Known Deviations from the Spec

None. The spec was updated during plan-writing to reflect the `usage.py` module refinement that was forced by the test environment's missing `hassil` dependency. No other deviations exist at plan-time.

## Self-Review Summary

After writing the plan, I re-read the spec against the tasks:

- **Sensor set table** (five metrics × two scopes) → Task 3 (`_METRIC_SPECS` list, `_build_counter_set`).
- **`UsagePayload` dataclass + `_usage_from_result`** → Task 1.
- **`SIGNAL_USAGE_UPDATED` constant** → Task 2.
- **`HAClaudeAgentUsageCounterSensor` class, `_handle_usage` callback, filter/metric/restore logic** → Task 3.
- **`async_setup_entry` creating per-agent and integration-level sensors, integration device registration** → Task 3.
- **`_StreamResult` usage field + `ResultMessage.usage` extraction** → Task 5.
- **Dispatch after successful turn and soft-error paths (cost present)** → Task 5.
- **Skip dispatch when no `ResultMessage` received** → Task 5 (the `if result_state.cost_usd is not None` guard).
- **`RestoreSensor` for restart durability** → Task 3 (class inherits `RestoreSensor`, `async_added_to_hass` calls `async_get_last_sensor_data`).
- **Platform forwarding with SENSOR before CONVERSATION** → Task 6.
- **Translation keys for five sensors** → Task 4.
- **Pure-Python test coverage for helper and sensor callback** → Tasks 1 and 3.
- **Manual verification for RestoreSensor, dispatcher wiring, device registry, statistics** → Task 7.
- **No add-on changes** → no task.
- **No new dependencies** → no task.

All spec requirements have a corresponding task. No placeholders remain in the plan.
