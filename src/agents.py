"""Planner and reviewer roles used by the refresh workflow."""

from __future__ import annotations

from typing import Any

from .evaluator import evaluate_change
from .decision import DECISION_SCHEMAS, plan_decision, review_decision


class PlannerAgent:
    """Produces comparable original and changed-plan options."""

    name = "planner"

    def propose(self, case: dict[str, Any]) -> dict[str, Any]:
        if case.get("schema") in DECISION_SCHEMAS:
            return plan_decision(case)
        assessment = evaluate_change(case)
        metrics = assessment["metrics"]
        candidate = case["candidate_activity"]
        options = [
            {
                "option_id": "keep_original",
                "label": "Keep original plan",
                "time_hours": metrics["base_time_hours"],
                "cost_usd": metrics["base_cost_usd"],
                "rest_hours": metrics["base_rest_hours"],
                "hard_conflicts": [],
                "summary": "No added activity, cost, or travel time.",
            },
            {
                "option_id": "add_candidate",
                "label": f"Add {candidate['name']}",
                "time_hours": metrics["changed_time_hours"],
                "cost_usd": metrics["changed_cost_usd"],
                "rest_hours": metrics["changed_rest_hours"],
                "hard_conflicts": assessment["hard_conflicts"],
                "summary": assessment["recommendation"],
            },
        ]
        return {"assessment": assessment, "options": options}


class ReviewerAgent:
    """Independently verifies calculations before the workflow stores a result."""

    name = "reviewer"

    def review(self, case: dict[str, Any], planner_output: dict[str, Any]) -> dict[str, Any]:
        if case.get("schema") in DECISION_SCHEMAS:
            return review_decision(case, planner_output)
        assessment = planner_output["assessment"]
        metrics = assessment["metrics"]
        candidate = case["candidate_activity"]
        base = case["base_plan"]
        limits = case["constraints"]

        manual_base_time = float(base.get("planned_duration_hours", base.get("transfer_duration_hours")))
        manual_base_cost = float(base.get("planned_cost_usd", base.get("transfer_cost_usd")))
        manual_changed_time = manual_base_time + float(candidate["visit_duration_hours"]) + float(candidate["extra_transport_hours"])
        manual_changed_cost = (
            manual_base_cost
            + float(candidate["extra_transport_cost_usd"])
            + float(candidate.get("extra_activity_cost_usd", 0))
            + float(candidate.get("extra_flight_cost_usd", 0))
        )
        manual_rest = float(limits["available_hours"]) - manual_changed_time
        manual_trip_spend = float(limits.get("committed_spend_usd", 0)) + manual_changed_cost - manual_base_cost

        checks = {
            "time_calculation": metrics["changed_time_hours"] == manual_changed_time,
            "cost_calculation": metrics["changed_cost_usd"] == manual_changed_cost,
            "rest_calculation": metrics["changed_rest_hours"] == manual_rest,
            "total_trip_spend_calculation": metrics["changed_trip_spend_usd"] == manual_trip_spend,
        }
        expected_conflicts = case.get("expected_conflict_codes")
        if expected_conflicts is not None:
            checks["expected_conflicts"] = set(assessment["conflict_codes"]) == set(expected_conflicts)
        return {"checks": checks, "passed": all(checks.values())}

