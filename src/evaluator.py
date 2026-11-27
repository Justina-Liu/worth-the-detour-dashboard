"""Deterministic time, cost, and conflict calculations."""

from __future__ import annotations

from typing import Any


def _base_metric(base: dict[str, Any], planned_key: str, transfer_key: str) -> float:
    return float(base.get(planned_key, base.get(transfer_key, 0)))


def evaluate_change(case: dict[str, Any]) -> dict[str, Any]:
    base = case["base_plan"]
    candidate = case["candidate_activity"]
    limits = case["constraints"]

    base_time = _base_metric(base, "planned_duration_hours", "transfer_duration_hours")
    base_cost = _base_metric(base, "planned_cost_usd", "transfer_cost_usd")
    activity_cost = float(candidate.get("extra_activity_cost_usd", 0))
    flight_cost = float(candidate.get("extra_flight_cost_usd", 0))
    transport_addition = float(candidate["extra_transport_cost_usd"])
    changed_time = base_time + float(candidate["visit_duration_hours"]) + float(candidate["extra_transport_hours"])
    changed_cost = base_cost + transport_addition + activity_cost + flight_cost
    base_rest = float(limits["available_hours"]) - base_time
    changed_rest = float(limits["available_hours"]) - changed_time
    committed_spend = float(limits.get("committed_spend_usd", 0))
    trip_budget = float(limits.get("trip_budget_usd", 0))
    base_trip_spend = committed_spend
    changed_trip_spend = committed_spend + transport_addition + activity_cost + flight_cost

    conflict_codes: list[str] = []
    hard_conflicts: list[str] = []
    warnings: list[str] = []

    if changed_rest < float(limits["minimum_rest_hours"]):
        conflict_codes.append("rest")
        hard_conflicts.append("The changed plan leaves less rest time than the stated minimum.")
    if changed_cost > float(limits["transport_budget_usd"]):
        conflict_codes.append("budget")
        hard_conflicts.append("The changed plan exceeds the transportation budget.")
    if trip_budget and changed_trip_spend > trip_budget:
        conflict_codes.append("total_budget")
        hard_conflicts.append("The changed plan exceeds the total trip budget.")

    fixed_start = limits.get("fixed_activity_start_hour")
    candidate_start = candidate.get("start_hour")
    if fixed_start is not None and candidate_start is not None:
        candidate_end = float(candidate_start) + float(candidate["visit_duration_hours"]) + float(candidate["extra_transport_hours"])
        if candidate_end > float(fixed_start):
            conflict_codes.append("fixed_time")
            hard_conflicts.append(
                f"The candidate activity ends at {candidate_end:.1f}, after the fixed activity starts at {float(fixed_start):.1f}."
            )

    if candidate.get("luggage_storage_required", False):
        warnings.append("Confirm luggage storage before adding the activity.")
    if changed_rest < base_rest - 2:
        warnings.append("The change materially reduces rest before the following fixed activities.")

    if hard_conflicts:
        recommendation = "Keep the original plan unless the user changes a stated constraint."
        recommended_option = "keep_original"
    elif warnings:
        recommendation = "The added activity is feasible only after the listed warning is resolved."
        recommended_option = "keep_original"
    else:
        recommendation = "The added activity fits the stated constraints."
        recommended_option = "add_candidate"

    return {
        "metrics": {
            "base_time_hours": base_time,
            "changed_time_hours": changed_time,
            "added_time_hours": changed_time - base_time,
            "base_cost_usd": base_cost,
            "changed_cost_usd": changed_cost,
            "added_cost_usd": changed_cost - base_cost,
            "added_transport_cost_usd": transport_addition,
            "added_activity_cost_usd": activity_cost,
            "added_flight_cost_usd": flight_cost,
            "base_rest_hours": base_rest,
            "changed_rest_hours": changed_rest,
            "lost_rest_hours": base_rest - changed_rest,
            "trip_budget_usd": trip_budget,
            "base_trip_spend_usd": base_trip_spend,
            "changed_trip_spend_usd": changed_trip_spend,
            "base_trip_budget_remaining_usd": trip_budget - base_trip_spend if trip_budget else 0,
            "changed_trip_budget_remaining_usd": trip_budget - changed_trip_spend if trip_budget else 0,
        },
        "conflict_codes": conflict_codes,
        "hard_conflicts": hard_conflicts,
        "warnings": warnings,
        "recommendation": recommendation,
        "recommended_option": recommended_option,
    }

