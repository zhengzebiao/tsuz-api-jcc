"""Read and validate immutable JCC raw snapshots."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.jcc_data.canonical_hash import snapshot_content_hash

CONFIG_URL = "https://game.gtimg.cn/images/lol/act/jkzlk/js/config/versiondataconfig.js"
DATA_BASE_URL = "https://game.gtimg.cn/images/lol/act/jkzlk/js"
LINEUP_URL_TEMPLATE = (
    "https://game.gtimg.cn/images/lol/act/jkzlkauto/json/lineupJson/"
    "m{season_number}/11/{mode}/lineup_detail_total.json"
)
FIELDS = {
    "chess": "herourl",
    "race": "raceurl",
    "job": "joburl",
    "trait": "traiturl",
    "equip": "equipurl",
    "hex": "hexurl",
    "adventure": "adventureurl",
    "galaxy": "galaxyurl",
}
REQUIRED_NAMES = ("versiondataconfig", *FIELDS, "lineup_detail_total")


class SnapshotError(ValueError):
    """Raised when a raw snapshot is not a complete, coherent snapshot."""


@dataclass(frozen=True)
class VersionSpec:
    version: str
    season: str
    mode: str
    mode_name: str
    version_start_time: Any
    urls: dict[str, str]

    @property
    def base_version(self) -> str:
        return f"{self.version}-{self.season}"


@dataclass(frozen=True)
class RawSnapshot:
    directory: Path
    manifest: dict[str, Any]
    payloads: dict[str, Any]
    revision: int
    content_hash: str
    spec: VersionSpec


def _reject_nonstandard_number(value: str) -> None:
    raise ValueError(f"non-standard JSON number: {value}")


def decode_json(raw: bytes, source: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_nonstandard_number)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SnapshotError(f"invalid JSON in {source}: {type(exc).__name__}") from exc


def read_json(path: Path) -> Any:
    if path.is_symlink():
        raise SnapshotError(f"snapshot file must not be a symlink: {path.name}")
    try:
        return decode_json(path.read_bytes(), str(path))
    except OSError as exc:
        raise SnapshotError(f"cannot read snapshot file: {path.name}") from exc


def _required_scalar(record: dict[str, Any], key: str, context: str) -> str:
    value = record.get(key)
    if value is None or isinstance(value, (dict, list, tuple, set)):
        raise SnapshotError(f"{context}: missing required field {key}")
    result = str(value)
    if not result or not result.isprintable():
        raise SnapshotError(f"{context}: invalid field {key}")
    return result


def _safe_path_component(value: str, field: str) -> None:
    if value in {".", ".."} or not value.isprintable() or any(char in value for char in "/\\\x00"):
        raise SnapshotError(f"unsafe path component in {field}")


def build_version_spec(
    versions: Any,
    *,
    expected_mode: str = "18",
    expected_mode_name: str = "自然之力",
) -> VersionSpec:
    if not isinstance(versions, list):
        raise SnapshotError("versiondataconfig: expected array")
    candidates = [
        item
        for item in versions
        if isinstance(item, dict)
        and str(item.get("mode")) == expected_mode
        and item.get("name") == expected_mode_name
        and str(item.get("is_newest_version")) == "1"
    ]
    if len(candidates) != 1:
        raise SnapshotError(
            f"expected exactly one current mode={expected_mode} name={expected_mode_name} version, "
            f"found {len(candidates)}"
        )

    current = candidates[0]
    version = _required_scalar(current, "version", "versiondataconfig")
    season = _required_scalar(current, "season", "versiondataconfig")
    mode = _required_scalar(current, "mode", "versiondataconfig")
    mode_name = _required_scalar(current, "name", "versiondataconfig")
    for field, value in (("version", version), ("season", season), ("mode", mode)):
        _safe_path_component(value, field)

    urls = {"versiondataconfig": CONFIG_URL}
    for name, config_field in FIELDS.items():
        path = current.get(config_field)
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise SnapshotError(f"versiondataconfig: invalid resource path {config_field}")
        urls[name] = DATA_BASE_URL + path

    season_number = season.removeprefix("S")
    if not season_number.isdigit():
        raise SnapshotError(f"versiondataconfig: invalid season {season}")
    urls["lineup_detail_total"] = LINEUP_URL_TEMPLATE.format(season_number=season_number, mode=mode)
    return VersionSpec(version, season, mode, mode_name, current.get("version_start_time"), urls)


def validate_payload(name: str, data: Any, spec: VersionSpec) -> None:
    if name == "versiondataconfig":
        if build_version_spec(
            data,
            expected_mode=spec.mode,
            expected_mode_name=spec.mode_name,
        ) != spec:
            raise SnapshotError("versiondataconfig does not match snapshot metadata")
        return
    if not isinstance(data, dict):
        raise SnapshotError(f"{name}: expected object")
    if name == "lineup_detail_total":
        if not isinstance(data.get("lineup_list"), list):
            raise SnapshotError("lineup_detail_total: missing lineup_list")
        return
    for key in ("version", "season", "setId", "data"):
        if key not in data:
            raise SnapshotError(f"{name}: missing required key {key}")
    if str(data["version"]) != spec.version:
        raise SnapshotError(f"{name}: version mismatch")
    if str(data["season"]) != spec.season:
        raise SnapshotError(f"{name}: season mismatch")
    if str(data["setId"]) != spec.mode:
        raise SnapshotError(f"{name}: setId mismatch")
    if not isinstance(data["data"], dict):
        raise SnapshotError(f"{name}: data must be object")


def record_count(name: str, data: Any) -> int:
    if name == "versiondataconfig":
        return len(data)
    if name == "lineup_detail_total":
        return len(data["lineup_list"])
    return len(data["data"])


def _directory_revision(directory: Path, spec: VersionSpec) -> int:
    if directory.name == spec.base_version:
        return 1
    match = re.fullmatch(re.escape(spec.base_version) + r"-r([1-9][0-9]*)", directory.name)
    if match is None:
        raise SnapshotError(f"invalid snapshot directory name: {directory.name}")
    revision = int(match.group(1))
    if revision == 1:
        raise SnapshotError(f"revision 1 must use base directory: {directory.name}")
    return revision


def _manifest_for(directory: Path, spec: VersionSpec, payloads: dict[str, Any]) -> tuple[dict[str, Any], int, str]:
    manifest = read_json(directory / "manifest.json")
    if not isinstance(manifest, dict):
        raise SnapshotError("manifest must be an object")
    expected = {
        "mode": spec.mode,
        "mode_name": spec.mode_name,
        "season": spec.season,
        "version": spec.version,
        "set_id": spec.mode,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise SnapshotError("manifest metadata mismatch")
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or set(sources) != set(REQUIRED_NAMES):
        raise SnapshotError("manifest sources mismatch")
    for name in REQUIRED_NAMES:
        source = sources[name]
        if not isinstance(source, dict) or source.get("url") != spec.urls[name] or source.get("file") != f"{name}.json":
            raise SnapshotError(f"manifest source mismatch: {name}")
        if source.get("record_count") != record_count(name, payloads[name]):
            raise SnapshotError(f"manifest record count mismatch: {name}")

    revision = manifest.get("revision", _directory_revision(directory, spec))
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise SnapshotError("manifest revision must be a positive integer")
    if revision != _directory_revision(directory, spec):
        raise SnapshotError("manifest revision does not match directory")
    content_hash = snapshot_content_hash(payloads)
    declared_hash = manifest.get("content_hash", content_hash)
    if declared_hash != content_hash:
        raise SnapshotError("manifest content hash mismatch")
    normalized = dict(manifest)
    declared_base_version = manifest.get("base_version", spec.base_version)
    if declared_base_version != spec.base_version:
        raise SnapshotError("manifest base version mismatch")
    declared_directory_name = manifest.get("raw_directory_name", directory.name)
    if declared_directory_name != directory.name:
        raise SnapshotError("manifest directory name mismatch")
    normalized.setdefault("base_version", spec.base_version)
    normalized.setdefault("revision", revision)
    normalized.setdefault("raw_directory_name", directory.name)
    normalized.setdefault("content_hash", content_hash)
    return normalized, revision, content_hash


def read_snapshot(
    directory: Path,
    *,
    expected_mode: str = "18",
    expected_mode_name: str = "自然之力",
) -> RawSnapshot:
    directory = Path(directory)
    if directory.is_symlink():
        raise SnapshotError(f"snapshot directory must not be a symlink: {directory.name}")
    if not directory.is_dir():
        raise SnapshotError(f"snapshot directory does not exist: {directory}")
    payloads: dict[str, Any] = {}
    for name in REQUIRED_NAMES:
        payloads[name] = read_json(directory / f"{name}.json")
    spec = build_version_spec(
        payloads["versiondataconfig"],
        expected_mode=expected_mode,
        expected_mode_name=expected_mode_name,
    )
    validate_payload("versiondataconfig", payloads["versiondataconfig"], spec)
    for name in (*FIELDS, "lineup_detail_total"):
        validate_payload(name, payloads[name], spec)
    manifest, revision, content_hash = _manifest_for(directory, spec, payloads)
    return RawSnapshot(directory, manifest, payloads, revision, content_hash, spec)
