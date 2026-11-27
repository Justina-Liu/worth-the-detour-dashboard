"""SQLite storage for reviewed Worth the Detour runs and user decisions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(db_path: Path) -> None:
    with connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                raw_filename TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                log_path TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluations (
                run_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                received_date TEXT NOT NULL,
                candidate_name TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                recommended_option TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                conflicts_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                options_json TEXT NOT NULL,
                reviewer_checks_json TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS decisions (
                decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                option_id TEXT NOT NULL,
                user_decision TEXT NOT NULL,
                decided_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            """
        )


def save_reviewed_run(
    db_path: Path,
    run_id: str,
    raw_filename: str,
    processed_at: str,
    log_path: str,
    case: dict[str, Any],
    planner_output: dict[str, Any],
    reviewer_output: dict[str, Any],
) -> None:
    assessment = planner_output["assessment"]
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
            (run_id, raw_filename, processed_at, "reviewed", log_path),
        )
        connection.execute(
            """
            INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                case["case_id"],
                case["received_date"],
                case.get("candidate_activity", {}).get("name") or case.get("day_title", "Trip decision"),
                assessment["recommendation"],
                assessment["recommended_option"],
                json.dumps(assessment["metrics"]),
                json.dumps(assessment["hard_conflicts"]),
                json.dumps(assessment["warnings"]),
                json.dumps(planner_output["options"]),
                json.dumps(reviewer_output["checks"]),
            ),
        )


def get_latest_evaluation(db_path: Path) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT runs.run_id, runs.raw_filename, runs.processed_at,
                   evaluations.case_id, evaluations.received_date, evaluations.candidate_name,
                   evaluations.recommendation, evaluations.recommended_option,
                   evaluations.metrics_json, evaluations.conflicts_json, evaluations.warnings_json,
                   evaluations.options_json, evaluations.reviewer_checks_json
            FROM evaluations JOIN runs USING (run_id)
            -- processed_at is shown to users at second precision.  rowid preserves
            -- the actual insertion order when several refreshes finish in one second.
            ORDER BY runs.rowid DESC LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    for column in ("metrics_json", "conflicts_json", "warnings_json", "options_json", "reviewer_checks_json"):
        result[column.removesuffix("_json")] = json.loads(result.pop(column))
    return result


def save_human_decision(db_path: Path, run_id: str, option_id: str, user_decision: str, decided_at: str) -> None:
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO decisions (run_id, option_id, user_decision, decided_at) VALUES (?, ?, ?, ?)",
            (run_id, option_id, user_decision, decided_at),
        )

