"""Report the JCC permission catalog to tsuz-api-main."""

from __future__ import annotations

import argparse
import json

from app.clients.main_client import MainClient

DEFAULT_PERMISSIONS = (
    {
        "code": "jcc:data:read",
        "display_name": "JCC data read",
        "description": "Read published JCC structured data",
    },
    {
        "code": "jcc:record:read",
        "display_name": "JCC record read",
        "description": "Read active JCC records",
    },
    {
        "code": "jcc:stats:read",
        "display_name": "JCC statistics read",
        "description": "Read JCC resource statistics",
    },
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report JCC permissions to the main service.")
    parser.add_argument("--permissions", help="JSON file containing a permission item array")
    args = parser.parse_args()
    permissions = DEFAULT_PERMISSIONS
    if args.permissions:
        with open(args.permissions, encoding="utf-8") as stream:
            loaded = json.load(stream)
        if not isinstance(loaded, list) or not all(isinstance(item, dict) for item in loaded):
            raise ValueError("permission file must contain a JSON object array")
        permissions = tuple(loaded)
    result = MainClient().report_permissions(list(permissions))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
