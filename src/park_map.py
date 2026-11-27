"""Build map pins and walking legs for the two Disneyland demo itineraries."""


BLOCKED_STOP = "🔒 Blocked option"


def _activity_name(text):
    return text.removeprefix("Flexible · ").split(" · ", 1)[0]


def park_walk_map(day_title, timeline, fixtures):
    park_name = day_title.rsplit(" — ", 1)[-1]
    park = fixtures.get(park_name)
    if not park:
        return None

    stops = []
    for item in timeline:
        name = _activity_name(item["Activity"])
        stop = park["stops"].get(name)
        stops.append(stop)
    if not stops or not stops[0]:
        return None

    points, names, details, legs = [], [], [], []
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
                key = f'{stop["id"]}|{next_stop["id"]}'
                leg = park["legs"].get(key)
                if leg is not None:
                    detail["meters"] = round(leg["distance_m"])
                    legs.append({"path": leg["path"], "blocked": detail["nextBlocked"]})
        points.append(stop["point"])
        names.append(stop["label"])
        details.append(detail)

    return {"points": points, "names": names, "stopDetails": details, "legs": legs,
            "follow": True, "maxFitZoom": 18}
