import json
from pathlib import Path
from typing import Self
from urllib.error import URLError

import pytest

import scripts.sync_jcc_data as sync_module
from app.jcc_data.canonical_hash import canonical_json_bytes, snapshot_content_hash
from scripts.sync_jcc_data import (
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


def test_canonical_hash_ignores_object_key_order_but_preserves_array_order() -> None:
    first = {name: {"value": {"b": 2, "a": 1}, "array": [1, 2]} for name in ("versiondataconfig", *FIELDS)}
    reordered = {name: {"array": [1, 2], "value": {"a": 1, "b": 2}} for name in ("versiondataconfig", *FIELDS)}
    array_changed = {name: {"value": {"a": 1, "b": 2}, "array": [2, 1]} for name in ("versiondataconfig", *FIELDS)}

    assert canonical_json_bytes(first["chess"]) == canonical_json_bytes(reordered["chess"])
    assert snapshot_content_hash(first) == snapshot_content_hash(reordered)
    assert snapshot_content_hash(first) != snapshot_content_hash(array_changed)


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


def test_hash_ignores_lineup_changes_but_sync_preserves_lineup_file(tmp_path: Path) -> None:
    raw_directory = tmp_path / "raw"
    first = sync_latest(raw_directory, fetcher=_fetcher_for(_payloads(), []))
    changed = _payloads()
    changed["lineup_detail_total"] = b'{"lineup_list":[{"lineup_id":"changed"}]}'

    second = sync_latest(
        raw_directory,
        fetcher=_fetcher_for(changed, []),
        force_refresh=True,
    )

    assert second.status == "matched"
    assert second.content_hash == first.content_hash
    assert (first.directory / "lineup_detail_total.json").read_bytes() == _lineup_bytes()


def test_force_refresh_matches_existing_revision_without_publishing(tmp_path: Path) -> None:
    raw_directory = tmp_path / "raw"
    first = sync_latest(raw_directory, fetcher=_fetcher_for(_payloads(), []))
    calls: list[str] = []

    second = sync_latest(
        raw_directory,
        fetcher=_fetcher_for(_payloads(), calls),
        force_refresh=True,
    )

    assert first.status == "updated"
    assert second.status == "matched"
    assert second.directory == first.directory
    assert second.revision == 1
    assert len(calls) == 10
    assert sorted(path.name for path in raw_directory.iterdir() if path.is_dir()) == [f"{VERSION}-{SEASON}"]


def test_force_refresh_publishes_next_revision_for_changed_content(tmp_path: Path) -> None:
    raw_directory = tmp_path / "raw"
    first = sync_latest(raw_directory, fetcher=_fetcher_for(_payloads(), []))
    changed = _payloads()
    changed_payload = json.loads(changed["hex"].decode("utf-8"))
    changed_payload["data"]["hex-1"]["name"] = "changed"
    changed["hex"] = json.dumps(changed_payload).encode("utf-8")

    second = sync_latest(
        raw_directory,
        fetcher=_fetcher_for(changed, []),
        force_refresh=True,
    )

    assert first.content_hash != second.content_hash
    assert second.status == "updated"
    assert second.revision == 2
    assert second.directory.name == f"{VERSION}-{SEASON}-r2"
    assert first.directory.exists()
    manifest = json.loads((second.directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["base_version"] == f"{VERSION}-{SEASON}"
    assert manifest["revision"] == 2
    assert manifest["raw_directory_name"] == second.directory.name
    assert manifest["content_hash"] == second.content_hash


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


def test_existing_higher_revision_is_selected_numerically(tmp_path: Path) -> None:
    raw_directory = tmp_path / "raw"
    current = sync_latest(raw_directory, fetcher=_fetcher_for(_payloads(), []))
    source = current.directory
    for revision in (2, 10):
        target = raw_directory / f"{VERSION}-{SEASON}-r{revision}"
        target.mkdir()
        for path in source.iterdir():
            if path.name == "manifest.json":
                continue
            (target / path.name).write_bytes(path.read_bytes())
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        manifest["revision"] = revision
        manifest["raw_directory_name"] = target.name
        (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    selected = sync_latest(raw_directory, fetcher=_fetcher_for(_payloads(), []))

    assert selected.revision == 10
    assert selected.directory.name.endswith("-r10")


def test_sync_rejects_unexpected_configured_mode_name(tmp_path: Path) -> None:
    with pytest.raises(SyncError, match="expected exactly one current mode=18 name=其他模式"):
        sync_latest(
            tmp_path / "raw",
            fetcher=_fetcher_for(_payloads(), []),
            expected_mode_name="其他模式",
        )


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


def test_existing_legacy_manifest_is_read_without_rewriting(tmp_path: Path) -> None:
    raw_directory = tmp_path / "raw"
    first = sync_latest(raw_directory, fetcher=_fetcher_for(_payloads(), []))
    manifest_path = first.directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in ("base_version", "revision", "raw_directory_name", "content_hash", "generated_at"):
        manifest.pop(key)
    original = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    manifest_path.write_text(original, encoding="utf-8")

    second = sync_latest(raw_directory, fetcher=_fetcher_for(_payloads(), []))

    assert second.status == "skipped"
    assert second.revision == 1
    assert len(second.content_hash) == 64
    assert manifest_path.read_text(encoding="utf-8") == original


def test_acquire_lock_rejects_second_holder(tmp_path: Path) -> None:
    lock_file = tmp_path / "sync.lock"
    with acquire_lock(lock_file), pytest.raises(LockUnavailable, match="held"), acquire_lock(lock_file):
        pass


def test_cli_reuses_raw_and_runs_structured_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_directory = tmp_path / "raw"
    synced = sync_latest(raw_directory, fetcher=_fetcher_for(_payloads(), []))
    seen: list[Path] = []

    class Imported:
        status = "updated"

    monkeypatch.setattr(sync_module, "sync_latest", lambda *args, **kwargs: synced)
    monkeypatch.setattr(sync_module, "_import_result", lambda result: seen.append(result.directory) or Imported())

    assert main(["--raw-dir", str(raw_directory)]) == 0
    assert seen == [synced.directory]
    assert "sync updated" in capsys.readouterr().out


def test_cli_reports_database_failure_without_removing_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_directory = tmp_path / "raw"
    synced = sync_latest(raw_directory, fetcher=_fetcher_for(_payloads(), []))
    monkeypatch.setattr(sync_module, "sync_latest", lambda *args, **kwargs: synced)

    def fail_import(_result):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(sync_module, "_import_result", fail_import)

    assert main(["--raw-dir", str(raw_directory)]) == 1
    assert synced.directory.exists()
    assert "sync failed" in capsys.readouterr().err


def test_cli_treats_lock_conflict_as_successful_skip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lock_file = tmp_path / "sync.lock"
    with acquire_lock(lock_file):
        assert main(["--raw-dir", str(tmp_path / "raw"), "--lock-file", str(lock_file)]) == 0

    assert "sync skipped" in capsys.readouterr().err
