import json
from pathlib import Path
from typing import Self
from urllib.error import URLError

import pytest

from scripts.sync_lol_data import (
    CONFIG_URL,
    FIELDS,
    LockUnavailable,
    SyncError,
    acquire_lock,
    fetch_bytes,
    main,
    sync_latest,
)

VERSION = "18.18.test"
SEASON = "S19"
MODE = "18"


def _config_bytes() -> bytes:
    record = {
        "version_start_time": "2026-09-03",
        "version": VERSION,
        "season": SEASON,
        "is_newest_version": 1,
        "mode": MODE,
        "name": "自然之力",
    }
    for name, field in FIELDS.items():
        record[field] = f"/{MODE}/{VERSION}-{SEASON}/{name}.js"
    return b"\xef\xbb\xbf" + json.dumps([record], ensure_ascii=False).encode("utf-8")


def _data_bytes(name: str, *, version: str = VERSION) -> bytes:
    payload = {
        "version": version,
        "season": SEASON,
        "setId": MODE,
        "time": "2026-09-02 19:19:44",
        "data": {f"{name}-1": {"name": name}},
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _lineup_bytes() -> bytes:
    return b'{"lineup_list":[{"lineup_id":"1"}]}'


def _payloads() -> dict[str, bytes]:
    payloads = {"versiondataconfig": _config_bytes()}
    payloads.update({name: _data_bytes(name) for name in FIELDS})
    payloads["lineup_detail_total"] = _lineup_bytes()
    return payloads


def _fetcher_for(payloads: dict[str, bytes], calls: list[str]):
    def fetch(url: str) -> bytes:
        calls.append(url)
        if url == CONFIG_URL:
            return payloads["versiondataconfig"]
        for name in FIELDS:
            if url.endswith(f"/{name}.js"):
                return payloads[name]
        if url.endswith("lineup_detail_total.json"):
            return payloads["lineup_detail_total"]
        raise AssertionError(f"unexpected URL: {url}")

    return fetch


class _Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_fetch_bytes_retries_transient_network_errors() -> None:
    attempts: list[str] = []
    delays: list[float] = []

    def opener(request, timeout: float) -> _Response:
        attempts.append(f"{request.full_url}:{timeout}")
        if len(attempts) < 3:
            raise URLError("temporary outage")
        return _Response(b"payload")

    assert fetch_bytes(
        "https://example.test/data.json",
        timeout=4,
        retries=2,
        opener=opener,
        sleeper=delays.append,
    ) == b"payload"
    assert attempts == [
        "https://example.test/data.json:4",
        "https://example.test/data.json:4",
        "https://example.test/data.json:4",
    ]
    assert delays == [1, 2]


def test_sync_downloads_raw_payloads_and_manifest(tmp_path: Path) -> None:
    payloads = _payloads()
    calls: list[str] = []

    result = sync_latest(tmp_path / "raw", fetcher=_fetcher_for(payloads, calls))

    assert result.status == "updated"
    assert result.directory == tmp_path / "raw" / f"{VERSION}-{SEASON}"
    assert calls[0] == CONFIG_URL
    assert len(calls) == 10
    for name, raw in payloads.items():
        assert (result.directory / f"{name}.json").read_bytes() == raw

    manifest = json.loads((result.directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION
    assert manifest["season"] == SEASON
    assert manifest["set_id"] == MODE
    assert manifest["sources"]["chess"]["record_count"] == 1
    assert manifest["sources"]["lineup_detail_total"]["record_count"] == 1


def test_sync_skips_complete_snapshot_without_redownloading(tmp_path: Path) -> None:
    raw_directory = tmp_path / "raw"
    first_calls: list[str] = []
    first = sync_latest(raw_directory, fetcher=_fetcher_for(_payloads(), first_calls))

    second_calls: list[str] = []
    second = sync_latest(raw_directory, fetcher=_fetcher_for(_payloads(), second_calls))

    assert first.status == "updated"
    assert second.status == "skipped"
    assert second.directory == first.directory
    assert second_calls == [CONFIG_URL]


def test_sync_fails_closed_for_incomplete_existing_snapshot(tmp_path: Path) -> None:
    raw_directory = tmp_path / "raw"
    target = raw_directory / f"{VERSION}-{SEASON}"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text("{}", encoding="utf-8")
    calls: list[str] = []

    with pytest.raises(SyncError, match="incomplete"):
        sync_latest(raw_directory, fetcher=_fetcher_for(_payloads(), calls))

    assert calls == [CONFIG_URL]
    assert list(raw_directory.glob(".sync-*")) == []
    assert (target / "manifest.json").read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("duplicate", "found 2"),
        ("missing", "found 0"),
    ],
)
def test_sync_rejects_ambiguous_current_version(tmp_path: Path, change: str, message: str) -> None:
    payloads = _payloads()
    config = json.loads(payloads["versiondataconfig"].decode("utf-8-sig"))
    if change == "duplicate":
        config.append(config[0].copy())
    else:
        config[0]["is_newest_version"] = 0
    payloads["versiondataconfig"] = json.dumps(config).encode("utf-8")

    with pytest.raises(SyncError, match=message):
        sync_latest(tmp_path / "raw", fetcher=_fetcher_for(payloads, []))

    assert list((tmp_path / "raw").glob(".sync-*")) == []
    assert list((tmp_path / "raw").glob(f"{VERSION}-{SEASON}")) == []


def test_sync_rejects_payload_metadata_mismatch_without_publishing(tmp_path: Path) -> None:
    payloads = _payloads()
    payloads["chess"] = _data_bytes("chess", version="18.18.other")

    with pytest.raises(SyncError, match="version mismatch"):
        sync_latest(tmp_path / "raw", fetcher=_fetcher_for(payloads, []))

    raw_directory = tmp_path / "raw"
    assert list(raw_directory.glob(f"{VERSION}-{SEASON}")) == []
    assert list(raw_directory.glob(".sync-*")) == []


def test_sync_rejects_invalid_lineup_without_publishing(tmp_path: Path) -> None:
    payloads = _payloads()
    payloads["lineup_detail_total"] = b"{}"

    with pytest.raises(SyncError, match="lineup_list"):
        sync_latest(tmp_path / "raw", fetcher=_fetcher_for(payloads, []))

    raw_directory = tmp_path / "raw"
    assert list(raw_directory.glob(f"{VERSION}-{SEASON}")) == []
    assert list(raw_directory.glob(".sync-*")) == []


def test_sync_cleans_temporary_directory_when_fetch_fails(tmp_path: Path) -> None:
    payloads = _payloads()
    calls: list[str] = []
    successful_data = 0

    def failing_fetcher(url: str) -> bytes:
        nonlocal successful_data
        calls.append(url)
        if url == CONFIG_URL:
            return payloads["versiondataconfig"]
        successful_data += 1
        if successful_data == 3:
            raise OSError("temporary CDN outage")
        return _fetcher_for(payloads, [])(url)

    with pytest.raises(OSError, match="outage"):
        sync_latest(tmp_path / "raw", fetcher=failing_fetcher)

    raw_directory = tmp_path / "raw"
    assert list(raw_directory.glob(f"{VERSION}-{SEASON}")) == []
    assert list(raw_directory.glob(".sync-*")) == []


def test_acquire_lock_rejects_second_holder(tmp_path: Path) -> None:
    lock_file = tmp_path / "sync.lock"
    with acquire_lock(lock_file), pytest.raises(LockUnavailable, match="held"), acquire_lock(lock_file):
        pass


def test_cli_treats_lock_conflict_as_successful_skip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lock_file = tmp_path / "sync.lock"
    with acquire_lock(lock_file):
        assert main(["--raw-dir", str(tmp_path / "raw"), "--lock-file", str(lock_file)]) == 0

    assert "sync skipped" in capsys.readouterr().err
