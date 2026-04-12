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

from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_USAGE_UPDATED
from .usage import UsagePayload


class HAClaudeAgentUsageCounterSensor(RestoreSensor, SensorEntity):
    """Cumulative cost or token counter for one metric, one scope.

    A single class serves both per-agent and integration-level sensors.
    The difference is `filter_subentry_id`:
      - ``str`` — filters; only events matching this subentry accumulate.
      - ``None`` — accepts every event (integration-level rollup).
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.TOTAL

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
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    async_add_entities(
        _build_counter_set(
            device_info=integration_device,
            unique_id_prefix=f"{config_entry.entry_id}_totals",
            filter_subentry_id=None,
        ),
    )
