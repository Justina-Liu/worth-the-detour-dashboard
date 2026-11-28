"""Validation for untrusted raw travel-change requests."""

from __future__ import annotations

from typing import Any
from .decision import DECISION_SCHEMAS, validate_decision


REQUIRED_TOP_LEVEL = {"case_id", "received_date", "base_plan", "candidate_activity", "constraints"}
INSTRUCTION_PATTERNS = (
    "ignore previous instructions",
    "ignore all instructions",
    "skip checks",
    "system message",
    "developer message",
)


def _all_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_all_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_all_text(item) for item in value)
    return value if isinstance(value, str) else ""


def validate_case(case: dict[str, Any]) -> list[str]:
    """Return user-readable errors. An empty list means the case is safe to evaluate."""
    if not isinstance(case, dict):
        return ["The case must be a JSON object."]
    # Apply the shared untrusted-input policy before any schema-specific return.
    raw_text = _all_text(case).lower()
    if any(pattern in raw_text for pattern in INSTRUCTION_PATTERNS):
        return ["Unsafe instruction-like text found in raw data. The run stopped without processing it."]
    if case.get("schema") in DECISION_SCHEMAS:
        return validate_decision(case)
    errors: list[str] = []
    missing = sorted(REQUIRED_TOP_LEVEL - set(case))
    if missing:
        errors.append(f"Missing required fields: {', '.join(missing)}.")
        return errors

    base = case["base_plan"]
    candidate = case["candidate_activity"]
    constraints = case["constraints"]

    base_time = base.get("planned_duration_hours", base.get("transfer_duration_hours"))
    base_cost = base.get("planned_cost_usd", base.get("transfer_cost_usd"))
    numeric_values = {
        "base duration": base_time,
        "base cost": base_cost,
        "candidate visit duration": candidate.get("visit_duration_hours"),
        "candidate transport duration": candidate.get("extra_transport_hours"),
        "candidate transport cost": candidate.get("extra_transport_cost_usd"),
        "available hours": constraints.get("available_hours"),
        "minimum rest hours": constraints.get("minimum_rest_hours"),
        "transport budget": constraints.get("transport_budget_usd"),
    }
    optional_numeric_values = {
        "candidate activity cost": candidate.get("extra_activity_cost_usd", 0),
        "candidate flight cost": candidate.get("extra_flight_cost_usd", 0),
        "total trip budget": constraints.get("trip_budget_usd", 0),
        "committed trip spend": constraints.get("committed_spend_usd", 0),
    }
    numeric_values.update(optional_numeric_values)
    for label, value in numeric_values.items():
        if not isinstance(value, (int, float)):
            errors.append(f"{label.capitalize()} must be a number.")
        elif value < 0:
            errors.append(f"{label.capitalize()} cannot be negative.")

    return errors

