"""Sync official JCC data, preserve raw revisions, and publish structured snapshots."""

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

from app.core.config import settings
from app.jcc_data.canonical_hash import snapshot_content_hash
from app.jcc_data.snapshot_reader import (
    FIELDS,
    REQUIRED_NAMES,
    SnapshotError,
    VersionSpec,
    build_version_spec,
    read_snapshot,
    record_count,
    validate_payload,
)
from app.jcc_data.sync_service import import_raw_snapshot

CONFIG_URL = "https://game.gtimg.cn/images/lol/act/jkzlk/js/config/versiondataconfig.js"
DATA_BASE_URL = "https://game.gtimg.cn/images/lol/act/jkzlk/js"
LINEUP_URL_TEMPLATE = (
    "https://game.gtimg.cn/images/lol/act/jkzlkauto/json/lineupJson/"
    "m{season_number}/11/{mode}/lineup_detail_total.json"
)
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
class SyncResult:
    status: str
    version: str
    season: str
    directory: Path
    mode: str = ""
    revision: int = 1
    content_hash: str = ""


class _RetryableFetchError(Exception):
    pass


def _reject_nonstandard_number(value: str) -> None:
    raise ValueError(f"non-standard JSON number: {value}")


def decode_json(raw: bytes, url: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_nonstandard_number)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SyncError(f"invalid UTF-8 JSON from {url}: {type(exc).__name__}") from exc


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


def _convert_error(exc: Exception) -> SyncError:
    if isinstance(exc, SyncError):
        return exc
    if isinstance(exc, SnapshotError):
        return SyncError(str(exc))
    return SyncError(f"sync validation failed: {type(exc).__name__}")


def _build_manifest(
    spec: VersionSpec,
    parsed: dict[str, Any],
    *,
    revision: int,
    directory_name: str,
    content_hash: str,
) -> dict[str, Any]:
    return {
        "mode": spec.mode,
        "mode_name": spec.mode_name,
        "season": spec.season,
        "version": spec.version,
        "base_version": spec.base_version,
        "revision": revision,
        "raw_directory_name": directory_name,
        "content_hash": content_hash,
        "version_start_time": spec.version_start_time,
        "set_id": spec.mode,
        "source_updated_at": parsed["chess"].get("time"),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sources": {
            name: {
                "url": url,
                "file": f"{name}.json",
                "record_count": record_count(name, parsed[name]),
            }
            for name, url in spec.urls.items()
        },
    }


def _write_snapshot(directory: Path, raw_by_name: dict[str, bytes], manifest: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    for name in REQUIRED_NAMES:
        (directory / f"{name}.json").write_bytes(raw_by_name[name])
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _publish_snapshot(temporary_directory: Path, target_directory: Path) -> None:
    if target_directory.exists():
        raise SyncError(f"snapshot directory already exists: {target_directory.name}")
    try:
        os.rename(temporary_directory, target_directory)
    except FileExistsError as exc:
        raise SyncError(f"snapshot directory appeared during publish: {target_directory.name}") from exc


def _matching_directories(raw_directory: Path, spec: VersionSpec) -> list[Path]:
    candidates = [
        path
        for path in raw_directory.iterdir()
        if path.is_dir() and (path.name == spec.base_version or path.name.startswith(f"{spec.base_version}-r"))
    ]
    return sorted(candidates, key=lambda path: (path.name != spec.base_version, path.name))


def _read_existing(raw_directory: Path, spec: VersionSpec) -> list[Any]:
    snapshots = []
    for directory in _matching_directories(raw_directory, spec):
        try:
            snapshots.append(
                read_snapshot(
                    directory,
                    expected_mode=spec.mode,
                    expected_mode_name=spec.mode_name,
                )
            )
        except (SnapshotError, OSError, TypeError, KeyError) as exc:
            raise SyncError(f"existing snapshot is incomplete: {directory.name}") from exc
    return snapshots


def _download_payloads(spec: VersionSpec, config_raw: bytes, fetcher: Fetcher) -> tuple[dict[str, bytes], dict[str, Any]]:
    raw_by_name = {"versiondataconfig": config_raw}
    parsed: dict[str, Any] = {"versiondataconfig": decode_json(config_raw, CONFIG_URL)}
    try:
        validate_payload("versiondataconfig", parsed["versiondataconfig"], spec)
        for name in FIELDS:
            raw = fetcher(spec.urls[name])
            data = decode_json(raw, spec.urls[name])
            validate_payload(name, data, spec)
            raw_by_name[name] = raw
            parsed[name] = data
        lineup_raw = fetcher(spec.urls["lineup_detail_total"])
        lineup_data = decode_json(lineup_raw, spec.urls["lineup_detail_total"])
        validate_payload("lineup_detail_total", lineup_data, spec)
        raw_by_name["lineup_detail_total"] = lineup_raw
        parsed["lineup_detail_total"] = lineup_data
    except (SnapshotError, SyncError) as exc:
        raise _convert_error(exc) from exc
    return raw_by_name, parsed


def _sync_unlocked(
    raw_directory: Path,
    fetcher: Fetcher,
    *,
    force_refresh: bool,
    expected_mode: str,
    expected_mode_name: str,
) -> SyncResult:
    config_raw = fetcher(CONFIG_URL)
    try:
        versions = decode_json(config_raw, CONFIG_URL)
        spec = build_version_spec(
            versions,
            expected_mode=expected_mode,
            expected_mode_name=expected_mode_name,
        )
    except (SnapshotError, SyncError) as exc:
        raise _convert_error(exc) from exc

    existing = _read_existing(raw_directory, spec)
    if existing and not force_refresh:
        selected = max(existing, key=lambda snapshot: snapshot.revision)
        return SyncResult(
            "skipped",
            spec.version,
            spec.season,
            selected.directory,
            spec.mode,
            selected.revision,
            selected.content_hash,
        )

    raw_by_name, parsed = _download_payloads(spec, config_raw, fetcher)
    hash_payloads = {name: parsed[name] for name in ("versiondataconfig", *FIELDS)}
    content_hash = snapshot_content_hash(hash_payloads)
    matching_snapshot = next(
        (snapshot for snapshot in existing if snapshot.content_hash == content_hash),
        None,
    )
    if matching_snapshot is not None:
        return SyncResult(
            "matched",
            spec.version,
            spec.season,
            matching_snapshot.directory,
            spec.mode,
            matching_snapshot.revision,
            content_hash,
        )

    used_revisions = {snapshot.revision for snapshot in existing}
    revision = max(used_revisions, default=0) + 1
    directory_name = spec.base_version if revision == 1 else f"{spec.base_version}-r{revision}"
    target = raw_directory / directory_name
    temporary = raw_directory / f".sync-{uuid.uuid4().hex}"
    manifest = _build_manifest(
        spec,
        parsed,
        revision=revision,
        directory_name=directory_name,
        content_hash=content_hash,
    )
    try:
        _write_snapshot(temporary, raw_by_name, manifest)
        _publish_snapshot(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
    return SyncResult("updated", spec.version, spec.season, target, spec.mode, revision, content_hash)


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
    force_refresh: bool = False,
    expected_mode: str = "18",
    expected_mode_name: str = "自然之力",
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
        return _sync_unlocked(
            raw_directory,
            fetcher,
            force_refresh=force_refresh,
            expected_mode=expected_mode,
            expected_mode_name=expected_mode_name,
        )


def _default_raw_directory() -> Path:
    configured = Path(settings.jcc_data_raw_dir)
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parents[1] / configured


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
    parser.add_argument("--timeout", type=_positive_float, default=settings.jcc_data_sync_timeout_seconds)
    parser.add_argument("--retries", type=_nonnegative_int, default=settings.jcc_data_sync_retries)
    parser.add_argument("--force-refresh", action="store_true")
    return parser


def _import_result(result: SyncResult) -> Any:
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        return import_raw_snapshot(
            db,
            result.directory,
            expected_mode=settings.jcc_data_mode,
            expected_mode_name=settings.jcc_data_mode_name,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = sync_latest(
            args.raw_dir,
            lock_file=args.lock_file,
            timeout=args.timeout,
            retries=args.retries,
            force_refresh=args.force_refresh,
            expected_mode=settings.jcc_data_mode,
            expected_mode_name=settings.jcc_data_mode_name,
        )
        imported = _import_result(result)
    except LockUnavailable as exc:
        print(f"sync skipped: {exc}", file=sys.stderr)
        return 0
    except (SyncError, SnapshotError, OSError, ValueError) as exc:
        print(f"sync failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - command boundary must redact unexpected DB errors
        print(f"sync failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    print(
        "sync "
        f"{imported.status}: mode={result.mode} version={result.version} "
        f"revision={result.revision} hash={result.content_hash[:12]} directory={result.directory}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
