"""Find a tentative slot while preserving the original day's known commitments."""

from .itinerary import find_flexible_stop


DEFAULT_DAY = (9 * 60, 21 * 60)
DEFAULT_VISIT = 90


def _open_windows(items, bounds, removed=None):
    first, last = bounds
    occupied = []
    for item in items:
        if item is removed:
            continue
        start = item["start"] if item["start"] is not None else item.get("flexible_start")
        if start is None or start >= last:
            continue
        end = item["end"] or item.get("flexible_end") or last
        if end > first:
            occupied.append((max(start, first), min(end, last)))
    open_windows = []
    cursor = first
    for start, end in sorted(occupied):
        if cursor < start:
            open_windows.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < last:
        open_windows.append((cursor, last))
    return open_windows


def _untimed(items, removed=None):
    return [item for item in items if item is not removed and item["start"] is None
            and item.get("flexible_start") is None]


def free_minutes(items, bounds):
    untimed = _untimed(items)
    if any(item.get("duration") is None for item in untimed):
        return None
    return max(0, sum(end - start for start, end in _open_windows(items, bounds))
               - sum(item["duration"] for item in untimed))


def _fits_untimed(windows, start, end, items):
    gaps = []
    for lower, upper in windows:
        if lower < start:
            gaps.append(min(start, upper) - lower)
        if end < upper:
            gaps.append(upper - max(end, lower))
    for duration in sorted((item["duration"] for item in items if item.get("duration")), reverse=True):
        eligible = [i for i, gap in enumerate(gaps) if gap >= duration]
        if not eligible:
            return False
        gaps[min(eligible, key=gaps.__getitem__)] -= duration
    return True


def trial_slot(items, bounds, option, free, rest=0):
    """Return a slot and optional flexible replacement, or a reason why none fits."""
    duration = option["visit_minutes"] + option["travel_minutes"]
    desired = option.get("window", bounds)
    explicit = option.get("replaces")
    if explicit:
        chosen = find_flexible_stop(items, explicit)
        if chosen is None or not chosen.get("flexible"):
            return None, "Choose a stop explicitly marked Flexible for the replacement."
        replacements = [chosen]
    else:
        replacements = [None, *(item for item in items if item.get("flexible") and item.get("duration"))]

    for removed in replacements:
        released = (option.get("replaced_minutes") or removed.get("duration")) if removed else 0
        if released is None or duration > free + released - rest:
            continue
        windows = _open_windows(items, bounds, removed)
        untimed = _untimed(items, removed)
        for start, end in windows:
            lower, upper = max(start, desired[0]), min(end, desired[1])
            fixed_start = option.get("start")
            possible = (fixed_start,) if fixed_start is not None else range(lower, upper - duration + 1)
            for candidate in possible:
                finish = candidate + duration
                if lower <= candidate and finish <= upper and _fits_untimed(windows, candidate, finish, untimed):
                    return {"start": candidate, "replaces": removed["text"] if removed else None,
                            "replaced_minutes": released}, None
    return None, "No slot fits this visit and drive while preserving fixed times and the rest you chose."
