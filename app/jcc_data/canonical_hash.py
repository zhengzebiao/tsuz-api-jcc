"""Deterministic hashing for official JCC data snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

HASH_RESOURCE_NAMES = (
    "adventure",
    "chess",
    "equip",
    "galaxy",
    "hex",
    "job",
    "race",
    "trait",
    "versiondataconfig",
)


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically while preserving array order."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def json_content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def snapshot_content_hash(payloads: Mapping[str, Any]) -> str:
    file_hashes: list[str] = []
    for name in HASH_RESOURCE_NAMES:
        if name not in payloads:
            raise ValueError(f"missing hash resource: {name}")
        file_hashes.append(f"{name}.json\0{json_content_hash(payloads[name])}\n")
    return hashlib.sha256("".join(file_hashes).encode("utf-8")).hexdigest()
