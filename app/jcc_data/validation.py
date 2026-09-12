"""Validation and scalar conversion for JCC source records."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.jcc_data.snapshot_reader import SnapshotError


class DataValidationError(SnapshotError):
    """Raised when a source record cannot be represented safely."""


def required_string(record: dict[str, Any], key: str, context: str) -> str:
    value = record.get(key)
    if value is None or isinstance(value, (dict, list, tuple, set)):
        raise DataValidationError(f"{context}: missing required field {key}")
    result = str(value)
    if not result or not result.isprintable():
        raise DataValidationError(f"{context}: invalid field {key}")
    return result


def optional_string(record: dict[str, Any], key: str, context: str) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        raise DataValidationError(f"{context}: invalid field {key}")
    result = str(value)
    if not result.isprintable():
        raise DataValidationError(f"{context}: invalid field {key}")
    return result or None


def field_string(record: dict[str, Any], keys: tuple[str, ...], context: str) -> str | None:
    for key in keys:
        if key in record:
            return optional_string(record, key, context)
    return None


def required_id(record: dict[str, Any], key: str, context: str) -> str:
    value = required_string(record, key, context)
    if value in {"", "0", "-1"} and key in {"id", "adventureId"}:
        # 0/-1 are valid source sentinels for relations, but not entity IDs.
        raise DataValidationError(f"{context}: invalid entity id")
    return value


def integer(value: Any, context: str, *, required: bool = True) -> int | None:
    if value is None or value == "":
        if required:
            raise DataValidationError(f"{context}: missing integer")
        return None
    if isinstance(value, bool):
        raise DataValidationError(f"{context}: invalid integer")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DataValidationError(f"{context}: invalid integer") from exc
    if decimal_value != decimal_value.to_integral_value():
        raise DataValidationError(f"{context}: invalid integer")
    return int(decimal_value)


def decimal(value: Any, context: str, *, required: bool = True) -> Decimal | None:
    if value is None or value == "":
        if required:
            raise DataValidationError(f"{context}: missing decimal")
        return None
    if isinstance(value, bool):
        raise DataValidationError(f"{context}: invalid decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DataValidationError(f"{context}: invalid decimal") from exc
    if not parsed.is_finite():
        raise DataValidationError(f"{context}: invalid decimal")
    return parsed


def split_ids(value: Any, context: str, *, sentinels: frozenset[str] = frozenset({"0", "-1"})) -> list[str]:
    if value is None:
        raise DataValidationError(f"{context}: missing relationship")
    if not isinstance(value, str):
        value = str(value)
    parts = [part.strip() for part in value.split("|")]
    if any(not part for part in parts):
        raise DataValidationError(f"{context}: empty relationship id")
    if len(parts) != len(set(parts)):
        raise DataValidationError(f"{context}: duplicate relationship id")
    result: list[str] = []
    for part in parts:
        if part in sentinels:
            continue
        result.append(part)
    return result


def split_ints(value: Any, context: str) -> list[int]:
    parts = split_ids(value, context, sentinels=frozenset())
    result: list[int] = []
    for part in parts:
        parsed = integer(part, context)
        if parsed is None:
            raise DataValidationError(f"{context}: missing integer")
        result.append(parsed)
    return result


def ensure_key_matches_id(key: Any, record: dict[str, Any], id_key: str, context: str) -> str:
    external_id = required_id(record, id_key, context)
    if str(key) != external_id:
        raise DataValidationError(f"{context}: object key does not match {id_key}")
    return external_id
