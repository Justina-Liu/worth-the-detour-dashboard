"""Raw-file-to-dashboard workflow with logs and review gates."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .agents import PlannerAgent, ReviewerAgent
from .database import initialize_database, save_reviewed_run
from .validation import validate_case


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_log(log_path: Path, payload: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_refresh(raw_path: Path, project_root: Path) -> dict[str, Any]:
    """Validate, plan, review, store, and log one raw JSON request."""
    run_id = f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    processed_at = _timestamp()
    log_path = project_root / "work" / "runs" / f"{run_id}.json"
    log: dict[str, Any] = {
        "run_id": run_id,
        "raw_file": raw_path.name,
        "processed_at": processed_at,
        "steps": [],
    }

    try:
        case = json.loads(raw_path.read_text(encoding="utf-8"))
        log["steps"].append({"step": "load_raw", "status": "passed"})
    except (OSError, json.JSONDecodeError) as error:
        log.update({"status": "failed", "error": f"Could not load JSON: {error}"})
        _write_log(log_path, log)
        return log

    errors = validate_case(case)
    if errors:
        log.update({"status": "stopped", "validation_errors": errors})
        log["steps"].append({"step": "validate_raw", "status": "stopped", "details": errors})
        _write_log(log_path, log)
        return log
    log["steps"].append({"step": "validate_raw", "status": "passed"})

    planner_output = PlannerAgent().propose(case)
    log["steps"].append({"step": "planner_agent", "status": "passed"})

    reviewer_output = ReviewerAgent().review(case, planner_output)
    if not reviewer_output["passed"]:
        log.update({"status": "stopped", "reviewer_checks": reviewer_output["checks"]})
        log["steps"].append({"step": "reviewer_agent", "status": "stopped", "details": reviewer_output["checks"]})
        _write_log(log_path, log)
        return log
    log["steps"].append({"step": "reviewer_agent", "status": "passed", "details": reviewer_output["checks"]})

    db_path = project_root / "data" / "worth_the_detour.db"
    initialize_database(db_path)
    save_reviewed_run(
        db_path=db_path,
        run_id=run_id,
        raw_filename=raw_path.name,
        processed_at=processed_at,
        log_path=str(log_path.relative_to(project_root)),
        case=case,
        planner_output=planner_output,
        reviewer_output=reviewer_output,
    )
    log.update({
        "status": "reviewed",
        "recommendation": planner_output["assessment"]["recommendation"],
        "recommended_option": planner_output["assessment"]["recommended_option"],
    })
    log["steps"].append({"step": "store_sqlite", "status": "passed", "database": str(db_path.relative_to(project_root))})
    _write_log(log_path, log)
    return log

