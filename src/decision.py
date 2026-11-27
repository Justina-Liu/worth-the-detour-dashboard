"""Shared scoring and confirmation rules for one decision on one day."""

from math import isclose, isfinite

from .routing import TRANSPORT, transport_label


LEGACY_DIMENSIONS = ("Route fit", "Transport cost", "Time cost")
DIMENSIONS = ("Worth visiting", "Route fit", "Time cost")
DEFAULT_WEIGHTS = [45, 25, 30]
DECISION_SCHEMA = "day-decision-v2"
DECISION_SCHEMAS = ("day-decision-v1", DECISION_SCHEMA)
REPUTATION_SHARE = 0.30
LOW_SCORE = 65


def _numeric(value):
    return type(value) in (int, float) and isfinite(value)


def _minute(value):
    return _numeric(value) and int(value) == value


def validate_decision(case):
    if not all(isinstance(case.get(key), str) and case[key].strip() for key in ("case_id", "received_date")):
        return ["Enter the case ID and data date."]
    options = case.get("candidates")
    limits = case.get("limits")
    locks = case.get("locks", [])
    weights = case.get("weights")
    if not isinstance(options, list) or not 1 <= len(options) <= 3 or not all(isinstance(option, dict) for option in options):
        return ["Enter one to three alternatives."]
    if not isinstance(limits, dict) or not isinstance(locks, list):
        return ["Add valid day limits and locked times."]
    if not isinstance(weights, list) or len(weights) != 3 or not all(_numeric(w) and w >= 0 for w in weights) or sum(weights) == 0:
        return ["Give at least one score dimension a positive weight."]

    errors = []
    current = case.get("schema") == DECISION_SCHEMA
    if current and case.get("transport_mode") not in tuple(TRANSPORT):
        errors.append("Choose a valid default transport mode.")
    for field in ("trip_budget", "baseline_spend", "extra_transport_cap", "available_minutes", "baseline_minutes", "minimum_rest"):
        value = limits.get(field)
        if value is None and (field == "trip_budget" or (current and field in ("extra_transport_cap", "baseline_spend"))):
            continue
        if not _numeric(value) or value < 0:
            errors.append(f"Enter a valid {field.replace('_', ' ')}.")
    if _numeric(limits.get("available_minutes")) and limits["available_minutes"] > 1440:
        errors.append("Available time cannot exceed 24 hours.")
    if current and limits.get("trip_budget") is not None and limits.get("baseline_spend") is None:
        errors.append("Enter the original trip estimate to check the total budget.")
    for lock in locks:
        if (not isinstance(lock, dict) or not isinstance(lock.get("text"), str)
                or not _minute(lock.get("start")) or not 0 <= lock["start"] < 1440):
            errors.append("A locked activity has an invalid start time.")
            continue
        if lock.get("end") is not None and (not _minute(lock["end"]) or not lock["start"] < lock["end"] <= 1440):
            errors.append("A locked activity has an invalid end time.")
    for index, option in enumerate(options, start=1):
        prefix = f"Option {index}"
        if not isinstance(option.get("name"), str) or not option["name"].strip():
            errors.append(f"{prefix} needs a name.")
        for field in ("visit_minutes", "travel_minutes", "transport_cost", "activity_cost"):
            value = option.get(field)
            if not _numeric(value) or value < 0 or (field == "visit_minutes" and value == 0):
                errors.append(f"{prefix} needs a valid {field.replace('_', ' ')}.")
        if not _minute(option.get("start")) or not 0 <= option["start"] < 1440:
            errors.append(f"{prefix} needs a valid start time.")
        if current:
            if option.get("transport_mode") not in tuple(TRANSPORT):
                errors.append(f"{prefix} needs a valid transport mode.")
            for field, label in (("interest", "personal interest"), ("route_fit", "route fit")):
                if not _numeric(option.get(field)) or not 1 <= option[field] <= 5:
                    errors.append(f"{prefix} needs a 1–5 {label} rating.")
            reputation = option.get("reputation")
            source = option.get("reputation_source")
            if reputation is not None and (not _numeric(reputation) or not 1 <= reputation <= 5
                                           or not isinstance(source, str) or not source.strip()):
                errors.append(f"{prefix} needs a 1–5 reputation rating with a source.")
            replacement = option.get("replaces")
            if replacement:
                flexible = case.get("flexible_stops", [])
                if not isinstance(replacement, str) or not isinstance(flexible, list) or flexible.count(replacement) != 1:
                    errors.append(f"{prefix} can only replace one matching flexible stop from this day.")
                released = option.get("replaced_minutes")
                if not _minute(released) or not 0 < released <= 1440:
                    errors.append(f"{prefix} needs the original stop’s duration in minutes.")
                recoverable = option.get("replaced_activity_cost")
                if not _numeric(recoverable) or recoverable < 0:
                    errors.append(f"{prefix} needs the recoverable cost of the original stop. Enter zero if there is none.")
                elif _numeric(limits.get("baseline_spend")) and recoverable > limits["baseline_spend"]:
                    errors.append(f"{prefix} cannot recover more than the original trip estimate.")
    return errors


def _within_limit(value, cap):
    if cap == 0:
        return 100.0 if value <= 0 else 0.0
    return 100 * min(1.0, max(0.0, 1 - value / cap))


def _conflicts(limits, option, locks, finish, rest, spend):
    conflicts = []
    if rest < 0 or finish > 1440:
        conflicts.append("Insufficient time in the day.")
    if rest < limits["minimum_rest"]:
        conflicts.append("Leaves less than your minimum rest.")
    if limits.get("extra_transport_cap") is not None and option["transport_cost"] > limits["extra_transport_cap"]:
        conflicts.append("Exceeds your extra transport spending limit.")
    if limits["trip_budget"] is not None and spend is not None and spend > limits["trip_budget"] + 1e-8:
        conflicts.append("Exceeds your trip budget.")
    for lock in locks:
        start, end = lock["start"], lock.get("end")
        overlap = option["start"] < end and finish > start if end is not None else option["start"] <= start < finish
        if overlap:
            conflicts.append(f'Overlaps locked activity: {lock["text"]}')
    return conflicts


def plan_decision(case):
    limits, candidates = case["limits"], case["candidates"]
    locks, weights = case.get("locks", []), case["weights"]
    base_rest = limits["available_minutes"] - limits["baseline_minutes"]
    budget = limits["trip_budget"]
    baseline_spend = limits.get("baseline_spend")
    current = case.get("schema") == DECISION_SCHEMA
    use_reputation = current and all(option.get("reputation") is not None for option in candidates)
    dimensions = DIMENSIONS if current else LEGACY_DIMENSIONS
    baseline_mode = case.get("transport_mode") if current else None
    rows = [{
        "option_id": "keep_original", "label": "Keep original plan", "score": None, "extra_cost": 0,
        "extra_minutes": 0, "rest_minutes": base_rest, "trip_spend": baseline_spend,
        "budget_left": None if budget is None or baseline_spend is None else budget - baseline_spend,
        "hard_conflicts": [], "notes": [],
        "transport_mode": baseline_mode, "transport": transport_label(baseline_mode),
    }]
    for index, option in enumerate(candidates, start=1):
        replaces = option.get("replaces") if current else None
        duration = option["visit_minutes"] + option["travel_minutes"]
        added_minutes = duration - (option["replaced_minutes"] if replaces else 0)
        recovered_cost = option["replaced_activity_cost"] if replaces else 0
        cost = round(option["transport_cost"] + option["activity_cost"] - recovered_cost, 2)
        rest = base_rest - added_minutes
        finish = option["start"] + duration
        spend = None if baseline_spend is None else round(baseline_spend + cost, 2)
        time_cost = _within_limit(added_minutes, max(0, base_rest - limits["minimum_rest"]))
        if current:
            interest = option["interest"]
            worth = interest + REPUTATION_SHARE * (option["reputation"] - interest) if use_reputation else interest
            values = [worth * 20, option["route_fit"] * 20, time_cost]
        else:
            values = [100 * option["visit_minutes"] / added_minutes,
                      _within_limit(option["transport_cost"], limits["extra_transport_cap"]), time_cost]
        score = round(sum(w * value for w, value in zip(weights, values)) / sum(weights), 1)
        notes = [note.strip() for note in str(option.get("notes", "")).splitlines() if note.strip()]
        if any(lock.get("end") is None and lock["start"] < option["start"] for lock in locks):
            notes.append("Verify the previous event's end time and the transfer buffer.")
        if option.get("luggage"):
            notes.append("Confirm luggage storage before adding this stop.")
        mode = (option.get("transport_mode") or baseline_mode) if current else None
        rows.append({
            "option_id": f"option_{index}", "label": option["name"], "score": score,
            "components": {name: round(value, 1) for name, value in zip(dimensions, values)},
            "extra_cost": cost, "extra_minutes": added_minutes, "rest_minutes": rest, "trip_spend": spend,
            "budget_left": None if budget is None or spend is None else budget - spend,
            "start": option["start"], "end": finish, "notes": notes,
            "replaces": replaces,
            "transport_mode": mode, "transport": transport_label(mode),
            "hard_conflicts": _conflicts(limits, option, locks, finish, rest, spend),
        })
    eligible = [row for row in rows[1:] if not row["hard_conflicts"] and row["score"] >= LOW_SCORE]
    best_score = max((row["score"] for row in eligible), default=None)
    leaders = [row for row in eligible if row["score"] == best_score]
    if len(leaders) > 1:
        recommendation = "The highest-scoring options are tied. Compare their costs and choose."
        recommended = "keep_original"
    elif leaders:
        winner = leaders[0]
        mode_text = f' ({winner["transport"]})' if winner["transport_mode"] else ""
        recommendation = f'Consider {winner["label"]}{mode_text}. The traveler makes the final decision.'
        recommended = leaders[0]["option_id"]
    else:
        recommendation = "Keep the original plan for now. Review the scores and any conflicts below."
        recommended = "keep_original"
    return {
        "assessment": {"recommendation": recommendation, "recommended_option": recommended,
                       "metrics": {"trip_budget": budget, "reputation_in_score": use_reputation},
                       "hard_conflicts": [], "warnings": []},
        "options": rows,
    }


def review_decision(case, proposal):
    """Independently check each proposal's arithmetic, constraints, and score."""
    options = proposal["options"]
    limits = case["limits"]
    candidates = case["candidates"]
    weights = case["weights"]
    current = case.get("schema") == DECISION_SCHEMA
    use_reputation = current and all(option.get("reputation") is not None for option in candidates)
    dimensions = DIMENSIONS if current else LEGACY_DIMENSIONS
    baseline = options[0]
    original_spend = limits.get("baseline_spend")
    checks = {
        "option_count": len(options) == len(candidates) + 1,
        "review_evidence": proposal["assessment"]["metrics"].get("reputation_in_score", False) == use_reputation,
        "baseline": baseline["option_id"] == "keep_original" and baseline["extra_cost"] == 0
        and baseline["trip_spend"] == original_spend
        and baseline["rest_minutes"] == limits["available_minutes"] - limits["baseline_minutes"],
    }
    for i, (candidate, row) in enumerate(zip(candidates, options[1:]), start=1):
        duration = candidate["travel_minutes"] + candidate["visit_minutes"]
        finish = candidate["start"] + duration
        replaces = candidate.get("replaces") if current else None
        released = candidate["replaced_minutes"] if replaces else 0
        rest = limits["available_minutes"] - (limits["baseline_minutes"] - released + duration)
        extra = round(sum(candidate[key] for key in ("transport_cost", "activity_cost"))
                      - (candidate["replaced_activity_cost"] if replaces else 0), 2)
        spend = None if original_spend is None else round(sum([original_spend, extra]), 2)
        budget = limits["trip_budget"]
        cap = limits.get("extra_transport_cap")
        blocked = (finish > 1440 or rest < 0 or rest < limits["minimum_rest"]
                   or (cap is not None and candidate["transport_cost"] > cap)
                   or (budget is not None and spend is not None and spend - budget > 1e-8))
        for lock in case.get("locks", []):
            if lock["end"] is None:
                blocked |= candidate["start"] <= lock["start"] < finish
            else:
                blocked |= max(candidate["start"], lock["start"]) < min(finish, lock["end"])
        time_cost = _within_limit(duration - released, max(0, limits["available_minutes"] - limits["baseline_minutes"] - limits["minimum_rest"]))
        if current:
            worth_points = 20 * candidate["interest"]
            if use_reputation:
                worth_points = ((1 - REPUTATION_SHARE) * worth_points
                                + REPUTATION_SHARE * 20 * candidate["reputation"])
            components = [worth_points, 20 * candidate["route_fit"], time_cost]
            mode = candidate.get("transport_mode") or case.get("transport_mode")
            checks[f"option_{i}_transport"] = row["transport_mode"] == mode and row["transport"] == transport_label(mode)
        else:
            components = [100 - 100 * candidate["travel_minutes"] / duration,
                          _within_limit(candidate["transport_cost"], limits["extra_transport_cap"]), time_cost]
        expected_score = sum(weight * value for weight, value in zip(weights, components)) / sum(weights)
        checks[f"option_{i}_arithmetic"] = (
            row["option_id"] == f"option_{i}" and row["label"] == candidate["name"]
            and row["extra_minutes"] == duration - released and row["start"] == candidate["start"]
            and row["replaces"] == replaces
            and row["end"] == finish and isclose(row["rest_minutes"], rest, abs_tol=1e-8)
            and row["extra_cost"] == extra and row["trip_spend"] == spend
            and row["budget_left"] == (None if budget is None or spend is None else budget - spend)
        )
        checks[f"option_{i}_constraints"] = bool(row["hard_conflicts"]) == blocked
        checks[f"option_{i}_score"] = (
            abs(row["score"] - expected_score) <= 0.05000001
            and all(abs(row["components"][name] - value) <= 0.05000001
                    for name, value in zip(dimensions, components))
        )
    selectable = [row for row in options[1:] if not row["hard_conflicts"] and row["score"] >= LOW_SCORE]
    highest = [row for row in selectable if all(row["score"] >= other["score"] for other in selectable)]
    checks["recommendation"] = proposal["assessment"]["recommended_option"] == (
        highest[0]["option_id"] if len(highest) == 1 else "keep_original"
    )
    return {"checks": checks, "passed": all(checks.values())}


def decision_error(row, action, acknowledged=False):
    if action != "accept" or row["option_id"] == "keep_original":
        return None
    if row["hard_conflicts"]:
        return "Resolve the listed conflicts or revise your adjustable limits first."
    if row["score"] < LOW_SCORE and not acknowledged:
        return "Confirm that the benefit is worth the time and money before accepting."
    return None
