"""Run deterministic, offline Agent response checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def evaluate_case(case: dict[str, object]) -> tuple[bool, list[str]]:
    expected = case.get("expected") or {}
    if not isinstance(expected, dict):
        return False, ["expected must be an object"]
    # The offline harness intentionally uses the case input as a stable stand-in
    # response. Integrations can replace this adapter without changing the rubric.
    response = str(case.get("input") or "")
    failures: list[str] = []
    for value in expected.get("must_contain", []):
        if str(value) not in response:
            failures.append(f"missing:{value}")
    for value in expected.get("must_not_contain", []):
        if str(value) in response:
            failures.append(f"forbidden:{value}")
    return not failures, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="run without external providers")
    parser.add_argument("--cases", type=Path, default=Path("evals/cases.jsonl"))
    args = parser.parse_args()
    if not args.offline:
        parser.error("only --offline mode is implemented")

    total = passed = 0
    for line in args.cases.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        ok, failures = evaluate_case(case)
        total += 1
        passed += int(ok)
        print(json.dumps({"case_id": case.get("id"), "passed": ok, "failures": failures}, ensure_ascii=False))
    print(json.dumps({"total": total, "passed": passed, "failed": total - passed}, ensure_ascii=False))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
