"""Build map pins and walking legs for the two Disneyland demo itineraries."""

from itertools import combinations, permutations
from math import ceil

from .routing import decode_polyline6


BLOCKED_STOP = "🔒 Blocked option"


def _activity_name(text):
    return text.removeprefix("Flexible · ").split(" · ", 1)[0]


def _park_route(day_title, timeline, fixtures):
    park = fixtures.get(day_title.rsplit(" — ", 1)[-1])
    if park is None:
        return [], {}
    return [park["stops"].get(_activity_name(item["Activity"])) for item in timeline], park["legs"]


def _leg(legs, origin, destination):
    return legs.get(f'{origin["id"]}|{destination["id"]}')


def _walk_minutes(leg):
    return ceil(leg.get("duration_s", leg["distance_m"] / 1.25) / 60)


def park_walk_map(day_title, timeline, fixtures):
    stops, known_legs = _park_route(day_title, timeline, fixtures)
    if not stops or not stops[0]:
        return None

    points, names, details, legs = [], [], [], []
    distance_m = 0.0
    for index, (item, stop) in enumerate(zip(timeline, stops)):
        if stop is None:
            continue
        blocked = item["Status"] == BLOCKED_STOP
        detail = {"order": index + 1, "slot": item["Time"], "status": item["Status"], "blocked": blocked}
        if index + 1 < len(stops):
            next_item, next_stop = timeline[index + 1], stops[index + 1]
            detail["next"] = f'{index + 2} · {next_stop["label"] if next_stop else _activity_name(next_item["Activity"])}'
            next_blocked = next_item["Status"] == BLOCKED_STOP
            detail["nextBlocked"] = blocked or next_blocked
            if next_stop is not None:
                leg = _leg(known_legs, stop, next_stop)
                if leg is not None:
                    detail["meters"] = round(leg["distance_m"])
                    detail["walkMinutes"] = _walk_minutes(leg)
                    path = leg.get("path") or decode_polyline6(leg["geometry"])
                    legs.append({"path": path, "blocked": detail["nextBlocked"]})
                    distance_m += leg["distance_m"]
        points.append(stop["point"])
        names.append(stop["label"])
        details.append(detail)

    return {"points": points, "names": names, "stopDetails": details, "legs": legs,
            "distance_m": distance_m if len(legs) == len(timeline) - 1 else None,
            "follow": True, "maxFitZoom": 18}


def _schedule_order(order, timeline, stops, legs, movable, deadline):
    """Fit each moved block as early as possible with walking to and from it."""
    first = order[0]
    finish = timeline[first]["end"]
    schedule = [(first, timeline[first]["start"], finish)]
    distance = 0.0
    for previous, index in zip(order, order[1:]):
        route = _leg(legs, stops[previous], stops[index])
        if route is None:
            return None
        distance += route["distance_m"]
        # Unchanged adjacent fixed blocks already include their original walking allowance.
        if previous not in movable and index not in movable and index == previous + 1:
            travel_minutes = 0
        else:
            travel_minutes = _walk_minutes(route)
        arrival = finish + travel_minutes
        item = timeline[index]
        if index in movable:
            start = arrival
            finish = start + item["duration"]
        else:
            start, finish = item["start"], item["end"]
            if arrival > start:
                return None
        if finish > deadline:
            return None
        schedule.append((index, start, finish))
    return round(distance, 1), finish, schedule


def shortest_park_order(day_title, timeline, fixtures, movable):
    """Compare all known stop orders with the opening stop and fixed times held in place."""
    stops, legs = _park_route(day_title, timeline, fixtures)
    movable = set(movable) - {0}
    if (not movable or not stops or not all(stops)
            or any(item["Status"] == BLOCKED_STOP for item in timeline)
            or len({stop["id"] for stop in stops}) != len(stops)):
        return None
    fixed = [i for i in range(len(stops)) if i not in movable]
    if (any(timeline[i]["start"] is None or timeline[i]["end"] is None for i in fixed)
            or any(not timeline[i]["duration"] for i in movable)):
        return None
    deadline = max(item["end"] for item in timeline if item["end"] is not None)
    best = None
    for positions in combinations(range(1, len(stops)), len(movable)):
        moving_positions = set(positions)
        for ordering in permutations(sorted(movable)):
            locks, flexible = iter(fixed), iter(ordering)
            order = [next(flexible) if i in moving_positions else next(locks) for i in range(len(stops))]
            candidate = _schedule_order(order, timeline, stops, legs, movable, deadline)
            if candidate is not None and (best is None or candidate[:2] < best[:2]):
                best = candidate
    return (best[0], best[2]) if best is not None else None
