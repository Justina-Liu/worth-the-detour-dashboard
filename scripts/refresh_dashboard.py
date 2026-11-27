"""Run one anonymized raw change request through the workflow."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.workflow import run_refresh  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/refresh_dashboard.py data/raw/<file>.json")
        return 2
    raw_path = (PROJECT_ROOT / sys.argv[1]).resolve()
    raw_directory = (PROJECT_ROOT / "data" / "raw").resolve()
    if raw_directory not in raw_path.parents or raw_path.suffix != ".json":
        print("Safety stop: provide one JSON file inside data/raw/.")
        return 2
    result = run_refresh(raw_path, PROJECT_ROOT)
    print(f"Run status: {result['status']}")
    print(f"Run log: work/runs/{result['run_id']}.json")
    if result["status"] != "reviewed":
        print(result.get("validation_errors") or result.get("error") or result.get("reviewer_checks"))
        return 1
    print(f"Recommendation: {result['recommendation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

