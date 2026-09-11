"""Download and atomically publish the latest official TFT data snapshot."""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import shutil
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
HEADERS = {"User-Agent": "tsuz-api-jcc-data-sync/1.0"}
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 2
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

Fetcher = Callable[[str], bytes]


class SyncError(RuntimeError):
    """Raised when a snapshot cannot be safely downloaded or published."""


class LockUnavailable(SyncError):
    """Raised when another sync process already owns the lock."""


class FetchError(SyncError):
    """Raised when an HTTP resource cannot be fetched."""


@dataclass(frozen=True)
class VersionSpec:
    version: str
    season: str
    mode: str
    mode_name: str
    version_start_time: Any
    urls: dict[str, str]


@dataclass(frozen=True)
class SyncResult:
    status: str
    version: str
    season: str
    directory: Path


def decode_json(raw: bytes, url: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyncError(f"Invalid UTF-8 JSON from {url}: {type(exc).__name__}") from exc


def _is_retryable_status(status: int) -> bool:
    return status in RETRYABLE_STATUS_CODES


def fetch_bytes(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    opener: Callable[..., Any] = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> bytes:
    """Fetch one URL with bounded retries and without logging response contents."""
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if retries < 0:
        raise ValueError("retries must not be negative")

    attempts = retries + 1
    last_reason = "unknown error"
    for attempt in range(attempts):
        try:
            request = Request(url, headers=HEADERS)
            with opener(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    if _is_retryable_status(status):
                        last_reason = f"HTTP {status}"
                        raise _RetryableFetchError
                    raise FetchError(f"GET {url} returned HTTP {status}")
                return response.read()
        except HTTPError as exc:
            if not _is_retryable_status(exc.code):
                raise FetchError(f"GET {url} returned HTTP {exc.code}") from exc
            last_reason = f"HTTP {exc.code}"
        except _RetryableFetchError:
            pass
        except (TimeoutError, URLError, OSError) as exc:
            last_reason = type(exc).__name__

        if attempt < retries:
            sleeper(min(2**attempt, 8))

    raise FetchError(f"GET {url} failed after {attempts} attempts ({last_reason})")


class _RetryableFetchError(Exception):
    pass


def _required_scalar(record: dict[str, Any], key: str, context: str) -> str:
    value = record.get(key)
    if value is None or isinstance(value, (dict, list, tuple, set)):
        raise SyncError(f"{context}: missing required field {key}")
    value_as_string = str(value)
    if not value_as_string or not value_as_string.isprintable():
        raise SyncError(f"{context}: missing required field {key}")
    return value_as_string


def _safe_path_component(value: str, field: str) -> None:
    if (
        value in {".", ".."}
        or not value.isprintable()
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise SyncError(f"version config field {field} is not a safe path component")


def build_version_spec(versions: Any) -> VersionSpec:
    if not isinstance(versions, list):
        raise SyncError("versiondataconfig: expected a JSON array")

    candidates = [
        item
        for item in versions
        if isinstance(item, dict)
        and item.get("name") == "自然之力"
        and str(item.get("is_newest_version")) == "1"
    ]
    if len(candidates) != 1:
        raise SyncError(f"Expected exactly one current 自然之力 version, found {len(candidates)}")

    current = candidates[0]
    version = _required_scalar(current, "version", "versiondataconfig")
    season = _required_scalar(current, "season", "versiondataconfig")
    mode = _required_scalar(current, "mode", "versiondataconfig")
    mode_name = _required_scalar(current, "name", "versiondataconfig")
    _safe_path_component(version, "version")
    _safe_path_component(season, "season")
    _safe_path_component(mode, "mode")

    urls = {"versiondataconfig": CONFIG_URL}
    for name, config_field in FIELDS.items():
        path = current.get(config_field)
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise SyncError(f"versiondataconfig: invalid resource path {config_field}")
        urls[name] = DATA_BASE_URL + path

    season_number = season.removeprefix("S")
    if not season_number.isdigit():
        raise SyncError(f"versiondataconfig: invalid season {season}")
    urls["lineup_detail_total"] = LINEUP_URL_TEMPLATE.format(
        season_number=season_number,
        mode=mode,
    )
    return VersionSpec(
        version=version,
        season=season,
        mode=mode,
        mode_name=mode_name,
        version_start_time=current.get("version_start_time"),
        urls=urls,
    )


def _validate_payload(name: str, data: Any, spec: VersionSpec) -> None:
    if name == "versiondataconfig":
        if not isinstance(data, list):
            raise SyncError("versiondataconfig: expected a JSON array")
        config_spec = build_version_spec(data)
        if config_spec != spec:
            raise SyncError("versiondataconfig: current version does not match snapshot")
        return

    if not isinstance(data, dict):
        raise SyncError(f"{name}: expected a JSON object")
    if name == "lineup_detail_total":
        if not isinstance(data.get("lineup_list"), list):
            raise SyncError("lineup_detail_total: missing lineup_list array")
        return

    for key in ("version", "season", "setId", "data"):
        if key not in data:
            raise SyncError(f"{name}: missing required key {key}")
    if str(data["version"]) != spec.version:
        raise SyncError(f"{name}: version mismatch {data['version']} != {spec.version}")
    if str(data["season"]) != spec.season:
        raise SyncError(f"{name}: season mismatch {data['season']} != {spec.season}")
    if str(data["setId"]) != spec.mode:
        raise SyncError(f"{name}: setId mismatch {data['setId']} != {spec.mode}")
    if not isinstance(data["data"], dict):
        raise SyncError(f"{name}: data must be a JSON object")


def _record_count(name: str, data: Any) -> int:
    if name == "versiondataconfig":
        return len(data)
    if name == "lineup_detail_total":
        return len(data["lineup_list"])
    return len(data["data"])


def _build_manifest(
    spec: VersionSpec,
    parsed: dict[str, Any],
) -> dict[str, Any]:
    return {
        "mode": spec.mode,
        "mode_name": spec.mode_name,
        "season": spec.season,
        "version": spec.version,
        "version_start_time": spec.version_start_time,
        "set_id": spec.mode,
        "source_updated_at": parsed["chess"].get("time"),
        "sources": {
            name: {
                "url": url,
                "file": f"{name}.json",
                "record_count": _record_count(name, parsed[name]),
            }
            for name, url in spec.urls.items()
        },
    }


def _read_json_file(path: Path) -> Any:
    return decode_json(path.read_bytes(), str(path))


def _snapshot_is_complete(directory: Path, spec: VersionSpec) -> bool:
    if not directory.is_dir():
        return False
    try:
        manifest = _read_json_file(directory / "manifest.json")
        if not isinstance(manifest, dict):
            return False
        if any(
            manifest.get(key) != value
            for key, value in {
                "mode": spec.mode,
                "mode_name": spec.mode_name,
                "season": spec.season,
                "version": spec.version,
                "set_id": spec.mode,
            }.items()
        ):
            return False
        sources = manifest.get("sources")
        if not isinstance(sources, dict) or set(sources) != set(REQUIRED_NAMES):
            return False
        for name in REQUIRED_NAMES:
            source = sources[name]
            expected_file = f"{name}.json"
            if not isinstance(source, dict):
                return False
            if source.get("url") != spec.urls[name] or source.get("file") != expected_file:
                return False
            data = _read_json_file(directory / expected_file)
            _validate_payload(name, data, spec)
            if source.get("record_count") != _record_count(name, data):
                return False
    except (OSError, SyncError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return True


def _write_snapshot(
    temporary_directory: Path,
    raw_by_name: dict[str, bytes],
    manifest: dict[str, Any],
) -> None:
    for name in REQUIRED_NAMES:
        (temporary_directory / f"{name}.json").write_bytes(raw_by_name[name])
    (temporary_directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _publish_snapshot(temporary_directory: Path, target_directory: Path) -> None:
    if target_directory.exists():
        raise SyncError(f"snapshot directory already exists: {target_directory}")
    try:
        os.rename(temporary_directory, target_directory)
    except FileExistsError as exc:
        raise SyncError(f"snapshot directory appeared during publish: {target_directory}") from exc


def _sync_unlocked(raw_directory: Path, fetcher: Fetcher) -> SyncResult:
    config_raw = fetcher(CONFIG_URL)
    versions = decode_json(config_raw, CONFIG_URL)
    spec = build_version_spec(versions)
    target_directory = raw_directory / f"{spec.version}-{spec.season}"

    if target_directory.exists():
        if _snapshot_is_complete(target_directory, spec):
            return SyncResult("skipped", spec.version, spec.season, target_directory)
        raise SyncError(f"existing snapshot is incomplete: {target_directory}")

    raw_by_name = {"versiondataconfig": config_raw}
    parsed = {"versiondataconfig": versions}
    for name in FIELDS:
        url = spec.urls[name]
        raw = fetcher(url)
        data = decode_json(raw, url)
        _validate_payload(name, data, spec)
        raw_by_name[name] = raw
        parsed[name] = data

    lineup_url = spec.urls["lineup_detail_total"]
    lineup_raw = fetcher(lineup_url)
    lineup_data = decode_json(lineup_raw, lineup_url)
    _validate_payload("lineup_detail_total", lineup_data, spec)
    raw_by_name["lineup_detail_total"] = lineup_raw
    parsed["lineup_detail_total"] = lineup_data

    temporary_directory = raw_directory / f".sync-{uuid.uuid4().hex}"
    temporary_directory.mkdir()
    try:
        _write_snapshot(temporary_directory, raw_by_name, _build_manifest(spec, parsed))
        _publish_snapshot(temporary_directory, target_directory)
    finally:
        if temporary_directory.exists():
            shutil.rmtree(temporary_directory, ignore_errors=True)

    return SyncResult("updated", spec.version, spec.season, target_directory)


@contextmanager
def acquire_lock(lock_file: Path) -> Iterator[BinaryIO]:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_file.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            raise LockUnavailable(f"sync lock is held: {lock_file}") from exc
        yield handle
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def sync_latest(
    raw_directory: Path,
    *,
    lock_file: Path | None = None,
    fetcher: Fetcher | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> SyncResult:
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if retries < 0:
        raise ValueError("retries must not be negative")

    raw_directory = Path(raw_directory)
    raw_directory.mkdir(parents=True, exist_ok=True)
    lock_file = Path(lock_file) if lock_file is not None else raw_directory / ".sync.lock"
    if fetcher is None:
        fetcher = lambda url: fetch_bytes(url, timeout=timeout, retries=retries)

    with acquire_lock(lock_file):
        return _sync_unlocked(raw_directory, fetcher)


def _default_raw_directory() -> Path:
    return Path(__file__).resolve().parents[1] / "raw"


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=_default_raw_directory())
    parser.add_argument("--lock-file", type=Path, default=None)
    parser.add_argument("--timeout", type=_positive_float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=_nonnegative_int, default=DEFAULT_RETRIES)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = sync_latest(
            args.raw_dir,
            lock_file=args.lock_file,
            timeout=args.timeout,
            retries=args.retries,
        )
    except LockUnavailable as exc:
        print(f"sync skipped: {exc}", file=sys.stderr)
        return 0
    except (SyncError, OSError, ValueError) as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1

    print(f"sync {result.status}: version={result.version} directory={result.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
