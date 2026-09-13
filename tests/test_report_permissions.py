from __future__ import annotations

import json
import sys
from typing import ClassVar

from scripts import report_permissions


class FakeMainClient:
    response: ClassVar[dict[str, bool]] = {"accepted": True}
    reported_permissions: ClassVar[list[dict[str, str]] | None] = None

    def __init__(self) -> None:
        pass

    def report_permissions(self, permissions: list[dict[str, str]]) -> dict[str, bool]:
        self.reported_permissions = permissions
        type(self).reported_permissions = permissions
        return self.response


def test_default_permissions_include_all_jcc_scopes(monkeypatch, capsys) -> None:
    FakeMainClient.reported_permissions = None
    monkeypatch.setattr(report_permissions, "MainClient", FakeMainClient)
    monkeypatch.setattr(sys, "argv", ["report_permissions"])

    assert report_permissions.main() == 0

    assert [item["code"] for item in FakeMainClient.reported_permissions or []] == [
        "jcc:data:read",
        "jcc:record:read",
        "jcc:stats:read",
    ]
    assert json.loads(capsys.readouterr().out) == {"accepted": True}


def test_permissions_file_overrides_default_catalog(monkeypatch, capsys, tmp_path) -> None:
    custom_permissions = [
        {
            "code": "jcc:custom:read",
            "display_name": "Custom read",
            "description": "Read custom data",
        }
    ]
    permissions_file = tmp_path / "permissions.json"
    permissions_file.write_text(json.dumps(custom_permissions), encoding="utf-8")
    FakeMainClient.reported_permissions = None
    monkeypatch.setattr(report_permissions, "MainClient", FakeMainClient)
    monkeypatch.setattr(sys, "argv", ["report_permissions", "--permissions", str(permissions_file)])

    assert report_permissions.main() == 0

    assert FakeMainClient.reported_permissions == custom_permissions
    assert json.loads(capsys.readouterr().out) == {"accepted": True}
