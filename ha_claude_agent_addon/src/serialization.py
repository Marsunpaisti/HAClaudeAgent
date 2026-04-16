"""JSON-friendly serialization of SDK dataclasses and exceptions.

The add-on forwards Claude Agent SDK messages to the integration over SSE.
Each SDK message is a `@dataclass` — we walk it recursively, injecting a
`_type` field on every dataclass instance so the integration can reconstruct
the original class on the other side of the wire.

Wire-protocol contract: the `_type` key name is part of the wire format
shared with ``custom_components/ha_claude_agent/stream.py``'s
``from_jsonable``. Do not rename it on one side without updating the other
— the integration will silently stop reconstructing dataclasses and the
consumer will only see raw dicts.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import traceback
from typing import Any

from pydantic import BaseModel

_LOGGER = logging.getLogger(__name__)


# Dedupe set for field-level drop logs. The log-storm risk is specifically
# for recurring SDK message types (e.g. StreamEvent) where a single bad
# field would emit one log line per message. Keyed on (cls_name, field_name)
# so a brand-new drop location still warns once, and subsequent occurrences
# fall to DEBUG to avoid flooding the add-on log.
_field_drop_logged: set[tuple[str, str]] = set()


def _reset_drop_log_dedupe_for_tests() -> None:
    """Clear the field-drop dedupe set. Test-only helper."""
    _field_drop_logged.clear()


def _log_field_drop(cls_name: str, field_name: str, message: str, *args: Any) -> None:
    """Log a dataclass-field drop with per-(class, field) deduplication.

    The first occurrence of a drop at a given location logs at WARNING so
    operators see it; subsequent occurrences of the *same* location fall
    to DEBUG to prevent log storms when the same bad field recurs on every
    message of a recurring type (e.g. every ``StreamEvent`` in a turn).
    """
    key = (cls_name, field_name)
    if key in _field_drop_logged:
        _LOGGER.debug(message, *args)
        return
    _field_drop_logged.add(key)
    _LOGGER.warning(message, *args)


class _UnserializableValue(Exception):
    """Sentinel raised when a leaf value cannot be JSON-serialized.

    Caught by the recursive walker's parent frame (dataclass, dict, or
    list) so the offending field/entry/element is dropped while the rest
    of the payload survives. Not part of the public API — callers of
    ``to_jsonable`` never see this exception.
    """


def to_jsonable(obj: Any) -> Any:
    """Recursively convert an object to JSON-serializable form.

    Dataclass instances are emitted as dicts with a `_type` key containing
    the class name. Plain dicts and primitives pass through unchanged.
    Lists and tuples are both serialized as JSON arrays — tuple-ness is
    not preserved (no SDK type uses tuples, so this is acceptable). Nested
    dataclasses (e.g. `AssistantMessage.content[0]` being a `TextBlock`)
    are walked recursively so every level carries its type tag.

    Raises ValueError if a dataclass has a field literally named `_type`,
    which would collide with the type discriminator key. Nested ``_type``
    collisions propagate through all container walkers uncaught — the
    drop-and-log machinery only catches ``_UnserializableValue``.

    Resilience: non-JSON-native leaf values (``bytes``, ``datetime``, sets,
    custom objects, ...) and dict entries with non-JSON keys are *dropped*
    from the output with a WARNING-level log, rather than aborting the
    whole payload with a ``TypeError`` at ``json.dumps`` time. This keeps
    one bad field in one SDK message from crashing an entire turn.
    Field-level drops are deduplicated per (class, field) so a recurring
    bad field (e.g. a new SDK version adds a ``datetime`` to every
    ``StreamEvent``) logs once, not once per message.

    Exceptions raised by ``getattr`` while reading a dataclass field
    (e.g. a broken property/descriptor) are also treated as drops rather
    than aborts, so the resilience guarantee holds for any dataclass
    whose attribute-access machinery can fail.

    Side effect: a dropped field may cause the integration's dataclass
    reconstruction in ``from_jsonable`` to fall back to a raw dict (if the
    dropped field was required by ``cls.__init__``), which
    ``_deltas_from_sdk_stream`` then silently ignores via its ``case _:``
    arm. The "Dropping unserializable ..." WARNING is the operator's
    signal that a wire format update is needed — treat it as a bug
    report, not routine noise.

    NaN and infinity floats are allowed as leaf *values* to match the
    default ``json.dumps`` behavior used in ``server._sse_event``, but
    NaN/infinity floats as dict *keys* are dropped: ``json.dumps`` would
    coerce them to the strings ``"NaN"``/``"Infinity"``, silently mutating
    the key type on the wire. If we ever tighten the wire format to
    strict (RFC 8259) JSON, update both call sites together.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        cls_name = type(obj).__name__
        result: dict[str, Any] = {"_type": cls_name}
        for f in dataclasses.fields(obj):
            if f.name == "_type":
                raise ValueError(
                    f"{cls_name} has a field named '_type', "
                    "which collides with the type discriminator key"
                )
            # Read the attribute first so a broken property/descriptor
            # drops just this field instead of aborting the whole
            # payload. `_UnserializableValue` is not caught here — only
            # `to_jsonable` below can raise it, and only from a real
            # unserializable leaf, not from an attribute-access failure.
            try:
                value = getattr(obj, f.name)
            except Exception as err:  # noqa: BLE001
                _log_field_drop(
                    cls_name,
                    f.name,
                    "Dropping field %s.%s: getattr raised %s: %s",
                    cls_name,
                    f.name,
                    type(err).__name__,
                    err,
                )
                continue
            # Recurse. A nested `_type`-collision ``ValueError`` propagates
            # out uncaught; only the sentinel is trapped as a drop.
            try:
                result[f.name] = to_jsonable(value)
            except _UnserializableValue as err:
                _log_field_drop(
                    cls_name,
                    f.name,
                    "Dropping unserializable field %s.%s from wire payload: %s",
                    cls_name,
                    f.name,
                    err,
                )
        return result
    if isinstance(obj, BaseModel):
        cls_name = type(obj).__name__
        # Walk the model's fields via getattr (not model_dump), so nested
        # pydantic/dataclass children get their own _type injected by the
        # recursive to_jsonable call — model_dump would flatten them.
        result = {"_type": cls_name}
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
    if isinstance(obj, dict):
        result_dict: dict[Any, Any] = {}
        for k, v in obj.items():
            # json.dumps natively accepts str/int/float/bool/None as dict
            # keys (non-string keys get coerced to strings). Anything else
            # — bytes, tuples, custom objects — would raise TypeError at
            # dump time. NaN/infinity floats are also rejected explicitly
            # because json.dumps would stringify them to "NaN"/"Infinity"
            # and silently mutate the key type on the wire.
            if not (isinstance(k, (str, int, float)) or k is None):
                _LOGGER.warning(
                    "Dropping dict entry with non-JSON key of type %s: %r",
                    type(k).__name__,
                    k,
                )
                continue
            if isinstance(k, float) and not math.isfinite(k):
                _LOGGER.warning(
                    "Dropping dict entry with non-finite float key: %r",
                    k,
                )
                continue
            try:
                result_dict[k] = to_jsonable(v)
            except _UnserializableValue as err:
                _LOGGER.warning(
                    "Dropping dict entry %r with unserializable value: %s",
                    k,
                    err,
                )
        return result_dict
    if isinstance(obj, (list, tuple)):
        result_list: list[Any] = []
        for i, x in enumerate(obj):
            try:
                result_list.append(to_jsonable(x))
            except _UnserializableValue as err:
                _LOGGER.warning(
                    "Dropping list element at index %d with unserializable value: %s",
                    i,
                    err,
                )
        return result_list
    # Leaf value: confirm JSON-serializability. Use default ``json.dumps``
    # settings (``allow_nan=True``) so NaN/infinity floats continue to
    # survive the wire — matching ``server._sse_event``'s call.
    try:
        json.dumps(obj)
    except (TypeError, ValueError) as err:
        raise _UnserializableValue(
            f"leaf of type {type(obj).__name__} is not JSON-serializable: {err}"
        ) from err
    return obj


def exception_to_dict(err: BaseException) -> dict[str, Any]:
    """Capture an exception as a JSON-serializable dict.

    Serializes all public instance attributes (anything in `vars(err)` that
    doesn't start with an underscore). Attributes that can't be serialized
    to valid JSON fall back to their `repr()` form — this preserves
    debugging information for things like `CLIJSONDecodeError.original_error`
    which is itself an Exception. NaN and infinity float values also fall
    back to their `repr()` form because they are not valid JSON.

    The function is designed to be called from inside an exception handler
    and never raises — even if the exception's `vars()` or an attribute's
    `__repr__` itself misbehaves, a placeholder string is substituted.

    Note: attributes stored on `__slots__` (rather than `__dict__`) are
    invisible to this function. None of the SDK exceptions currently use
    `__slots__`, so this is acceptable as a known limitation.

    The formatted traceback is included as a string so the integration can
    log the add-on-side stack trace when re-raising.
    """
    safe_attrs: dict[str, Any] = {}
    try:
        attrs_source = vars(err)
    except Exception:  # noqa: BLE001
        attrs_source = {}

    for key, value in attrs_source.items():
        if key.startswith("_"):
            continue
        try:
            json.dumps(value, allow_nan=False)
            safe_attrs[key] = value
        except (TypeError, ValueError):
            try:
                safe_attrs[key] = repr(value)
            except Exception:  # noqa: BLE001
                safe_attrs[key] = f"<unrepresentable {type(value).__name__}>"

    return {
        "_type": type(err).__name__,
        "module": type(err).__module__,
        "message": str(err),
        "attrs": safe_attrs,
        "traceback": "".join(
            traceback.format_exception(type(err), err, err.__traceback__)
        ),
    }
