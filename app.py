"""Streamlit workbench for one travel decision at a time."""

from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

from src.agents import PlannerAgent, ReviewerAgent
from src.database import get_latest_evaluation, save_human_decision
from src.decision import DECISION_SCHEMA, DEFAULT_WEIGHTS, DIMENSIONS, LOW_SCORE, decision_error, validate_decision
from src.itinerary import change_ideas, export_trip, find_flexible_stop, format_hours, parse_itinerary, requested_day
from src.park_map import BLOCKED_STOP, park_walk_map
from src.routing import DRIVE_MODE, decode_polyline6, directions_links, fetch_road_route, route_key
from src.trial import DEFAULT_DAY, DEFAULT_VISIT, free_minutes, trial_slot
from src.workflow import run_refresh


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "worth_the_detour.db"
HOSTED_REVIEW = ROOT / "data" / "reviewed_demo.json"
EXAMPLE = ROOT / "data" / "transfer_itinerary_example.md"
DISNEYLAND_DEMO = ROOT / "data" / "disneyland_two_day_demo.md"
ITINERARY_TEMPLATE = ROOT / "data" / "itinerary_template.md"
EXAMPLE_CASE = ROOT / "data" / "raw" / "05_transfer_day_modes.json"
EXAMPLE_ROUTES = ROOT / "data" / "example_road_routes.json"
PARK_ROUTES = ROOT / "data" / "disneyland_walk_routes.json"
ROUTE_FIT = {1: "1 · Significant backtracking", 2: "2 · Out of the way",
             3: "3 · Some detour", 4: "4 · Nearly on the way", 5: "5 · On the way"}
UI = ROOT / "ui"
ROUTE_MAP = st.components.v2.component(
    "detour_route_map",
    html=(UI / "route-map.html").read_text(encoding="utf-8"),
    css=(UI / "route-map.css").read_text(encoding="utf-8"),
    js=(UI / "route-map.js").read_text(encoding="utf-8"),
)


def clock(minutes):
    minutes = int(minutes)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def as_time(minutes):
    return None if minutes is None else time(minutes // 60, minutes % 60)


def as_minutes(value):
    return value.hour * 60 + value.minute


def day_settings(day):
    return {**day.get("settings", {}), **day.get("detour", {}).get("settings", {})}


def trip_setting(days, name):
    values = {settings[name] for day in days if name in (settings := day_settings(day))}
    return values.pop() if len(values) == 1 else None


def show_upload_instructions():
    template = ITINERARY_TEMPLATE.read_text(encoding="utf-8")
    st.caption("Paste your plan and the stops you want to try into the prompt. Save the reply as itinerary.md.")
    prompt = f"""Convert my original itinerary and proposed changes below into one Markdown file for Worth the Detour.
Use the exact English headings and field labels shown here. Keep the source language for places and notes.

1. Start each day with ## Day N — YYYY-MM-DD — City. Omit the date or city if unknown.
   Under it, keep the ORIGINAL plan as one bullet per activity. Start confirmed activities with local
   HH:MM or HH:MM–HH:MM. Start untimed or tentative activities with "- Flexible ·".
2. For any day with changes I want to compare, add ### Detour options AFTER the original plan.
   List one to three alternatives using #### Option 1 — Place name, #### Option 2 — Place name, and so on.
   Keep each proposed stop in this section so the app treats it as a choice.
3. Under each option, copy the information I provide into these fields, one bullet per field:
   Start (clock time including driving), Visit minutes, Extra drive minutes, Extra transport USD,
   Other cost USD, Interest, and Route fit. Use 1–5 for both ratings. Interest 5 means I strongly
   want to go, and Route fit 5 means the stop is on the way. Use the field names exactly as written.
   Omit Start when I want Worth the Detour to find a slot. Keep any stated time range and duration
   for an explicitly flexible original stop on that activity's line.
   Extra drive minutes and transport cost are differences from the original driving plan.
   For a replacement, also include Replace (exact name of an original flexible stop), Released minutes,
   and Recoverable original cost USD. Released minutes is the duration of the original stop.
   Include 0 for a known zero, such as a nonrefundable cost.
4. Under ### Detour options before the first option, put Free minutes (time still free after the
   original plan). If provided, also copy Question, Minimum rest minutes, Trip budget USD,
   Original trip estimate USD, Extra transport limit USD, Start location, End location,
   Start coordinates, and End coordinates. Under an option, optional fields are Notes, Coordinates,
   Map name, Public rating, Rating source, and Luggage (yes or no). Use latitude, longitude for coordinates.
   For an itinerary entirely inside one theme park, add Setting: park and include walking and queue time
   in Visit minutes. Set Extra drive minutes to 0.
5. Write numeric fields as bare numbers. Convert given hours to minutes. Fill only facts or estimates
   I supply. Omit unknown fields, keep my stated ratings, and use USD fields only for known USD amounts.
   Leave out traveler names, booking codes, and payment details. Replace the sample content and return
   only the Markdown, with no surrounding code fence.

FORMAT EXAMPLE
{template.rstrip()}

SOURCE ITINERARY
[Paste your original itinerary here]

CHANGES I WANT TO TRY
[For each proposed stop, give the day, whether it adds or replaces a stop, and any known time, cost,
interest, and route-fit inputs. Include how much free time you have that day.]"""
    st.code(prompt, language="text", wrap_lines=True)
    st.download_button("Download Markdown template", template, "itinerary-template.md", "text/markdown",
                       on_click="ignore")


def point_from_text(value):
    try:
        lat, lon = [float(part.strip()) for part in value.split(",")]
        if isfinite(lat) and isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180:
            return [lat, lon]
    except ValueError:
        pass
    return None


@st.cache_data
def example_route_data():
    sample = json.loads(EXAMPLE_ROUTES.read_text(encoding="utf-8"))
    routes = {
        route_key(route["points"], DRIVE_MODE): {
            "path": decode_polyline6(route["geometry"]), "distance_m": route["distance_m"],
        }
        for route in sample["routes"]
    }
    return sample["example_points"], routes


def display_route(candidate, sample, token, settings_data):
    heading, settings = st.columns([3, 2], vertical_alignment="center")
    heading.subheader(":material/map: Route", anchor=False)
    with settings.popover("Map settings", icon=":material/settings:"):
        follow = st.checkbox("Keep center on the selected route", value=True, key=f"{token}-follow")
        left, right = st.columns(2)
        origin = left.text_input("Start location", settings_data.get("start_location", "Anaheim Resort" if sample else ""), key=f"{token}-origin")
        destination = right.text_input("End location", settings_data.get("end_location", "Universal City, CA" if sample else ""), key=f"{token}-destination")
        coordinates = left.text_input("Start latitude, longitude", settings_data.get("start_coordinates", "33.802, -117.919" if sample else ""), key=f"{token}-xy-start")
        end_coordinates = right.text_input("End latitude, longitude", settings_data.get("end_coordinates", "34.139, -118.354" if sample else ""), key=f"{token}-xy-end")
    start, end = point_from_text(coordinates), point_from_text(end_coordinates)
    stop = candidate.get("point") if candidate else None
    stop_name = (candidate.get("map_name") or candidate["name"]) if candidate else None
    names = [origin, stop_name, destination] if candidate else [origin, destination]
    points = [start, stop, end] if candidate else [start, end]
    stops = " → ".join(str(name or "Location needed") for name in names)
    st.caption(f"{stops} · Drive")
    if all(points):
        viewport = st.container()
        key = route_key(points, DRIVE_MODE)
        route = example_route_data()[1].get(key) if sample else None
        if key and route is None:
            routes = st.session_state.setdefault("road-route-cache", {})
            route = routes.get(key)
            if route is None:
                if st.button("Draw route", key=f"{token}-draw",
                             help="Sends the stop coordinates to the FOSSGIS routing service."):
                    try:
                        route = fetch_road_route(points, DRIVE_MODE)
                        routes[key] = route
                    except (OSError, ValueError, KeyError, TypeError):
                        st.caption("The route service did not return a route. You can still open directions in Google Maps.")
        with viewport:
            ROUTE_MAP(data={"names": names, "points": points, "path": route["path"] if route else [],
                            "follow": follow}, key=f"{token}-map")
        if route:
            miles = route["distance_m"] / 1609.344
            st.caption(f"{miles:.1f} mi for this route · FOSSGIS / OpenStreetMap · "
                       "[Report a map issue](https://www.openstreetmap.org/fixthemap)")
    else:
        st.info("Add stop coordinates in Map settings and the option details to show the map.")
    addresses = [f"{point[0]},{point[1]}" if point else name for name, point in zip(names, points)]
    for label, url in directions_links(addresses, DRIVE_MODE):
        st.link_button(label, url, icon=":material/open_in_new:")


def show_total_score(score):
    if score is None:
        st.caption("Original plan · comparison baseline")
        return
    st.html(
        '<div class="detour-score"><span class="detour-score-label">Detour score</span>'
        f'<strong class="detour-score-number">{score:g}<small>/100</small></strong>'
        '<span class="detour-stars" aria-hidden="true">★★★★★'
        f'<span style="width:{score:.1f}%">★★★★★</span></span></div>'
    )


@st.cache_data
def park_route_data():
    return json.loads(PARK_ROUTES.read_text(encoding="utf-8"))


def show_park_order(day, token, row=None):
    schedule = [
        (item["start"], item["end"], item["text"], "Original plan")
        for item in day["items"] if not row or item["text"] != row.get("replaces")
    ]
    if row and row["option_id"] != "keep_original":
        if row["hard_conflicts"]:
            status = BLOCKED_STOP
        elif row.get("replaces"):
            status = "↻ Proposed swap"
        else:
            status = "✦ Proposed stop"
        insert_at = next((i for i, (start, _, _, _) in enumerate(schedule)
                          if start is not None and start > row["start"]), len(schedule))
        schedule.insert(insert_at, (row["start"], row["end"], row["label"], status))
    timeline = []
    for start, end, text, status in schedule:
        slot = "Flexible" if start is None else clock(start)
        if end is not None:
            slot += "–" + clock(end)
        if start is not None and text.startswith(clock(start)):
            text = text.split(" ", 1)[-1]
        timeline.append({"Stop": len(timeline) + 1, "Time": slot, "Activity": text, "Status": status})

    map_data = park_walk_map(day["title"], timeline, park_route_data())
    if map_data:
        st.subheader(":material/map: Park walkthrough", anchor=False)
        st.caption(f'Start at **1 · {map_data["names"][0]}**. Tap a pin for the next stop.')
        ROUTE_MAP(data=map_data, key=f"{token}-park-map")
        if any(leg["blocked"] for leg in map_data["legs"]):
            st.caption("Dashed route · Blocked option preview. See the conflict in the option card.")
        if len(map_data["legs"]) != len(timeline) - 1:
            st.caption("Some stops or walking segments have no mapped path.")
        st.caption("Demo walking paths and sample lunch locations · "
                   "[FOSSGIS / OpenStreetMap](https://routing.openstreetmap.de/about.html) · "
                   "[Report a map issue](https://www.openstreetmap.org/fixthemap)")

    st.subheader("Park order", anchor=False)
    st.caption("Example times include queueing and walking. Check the Disneyland app during your visit.")
    st.dataframe(pd.DataFrame(timeline), hide_index=True, width="stretch")


def candidate_fields(index, defaults, token, day_items, imported=False, park=False):
    with st.container():
        key = f"{token}-option-{index}"
        name = st.text_input("Place or idea", defaults.get("name", ""), key=f"{key}-name")
        cols = st.columns(2 if park else 3)
        start = cols[0].time_input("Start of park block" if park else "Start (including extra travel)",
                                   as_time(defaults.get("start")), step=300, key=f"{key}-start")
        visit = cols[1].number_input("Ride, queue and walk (min)" if park else "Visit time (min)", min_value=0,
                                     value=defaults.get("visit_minutes"), key=f"{key}-visit")
        travel = 0 if park else cols[2].number_input("Extra driving time (min)", min_value=0,
                                                     value=defaults.get("travel_minutes"), key=f"{key}-drive-time")
        left, right = st.columns(2)
        transport = left.number_input("Extra transport (USD)", min_value=0.0,
                                      value=float(defaults["transport_cost"]) if "transport_cost" in defaults else None,
                                      key=f"{key}-drive-cost", help="Enter the additional costs compared with your original drive, such as parking, tolls or the difference between two rideshare quotes.")
        activity_default = defaults.get("activity_cost", None if imported else 0.0)
        activity = right.number_input("Tickets and other costs (USD)", min_value=0.0,
                                      value=None if activity_default is None else float(activity_default), key=f"{key}-tickets")
        left, right = st.columns(2)
        interest = left.number_input("How much do you want to go? (1–5)", min_value=1, max_value=5,
                                     value=defaults.get("interest"), key=f"{key}-interest")
        fit = right.selectbox("How well does it fit the route?", list(ROUTE_FIT),
                              index=defaults["route_fit"] - 1 if "route_fit" in defaults else None,
                              format_func=ROUTE_FIT.__getitem__, key=f"{key}-drive-fit",
                              help="Judge the order of stops and backtracking. Enter the extra minutes separately.")
        with st.popover("More option details"):
            replacement = None
            released = recoverable = 0
            if defaults.get("replaces") or any(item["start"] is None for item in day_items):
                replacement = st.text_input("Name of flexible stop to replace (optional)",
                                            defaults.get("replaces", ""), key=f"{key}-replace")
                if replacement.strip():
                    match = find_flexible_stop(day_items, replacement)
                    replacement = match["text"] if match else replacement.strip()
                    released = st.number_input("Minutes freed by removing the original stop", min_value=0,
                                               value=defaults.get("replaced_minutes"), key=f"{key}-released")
                    recoverable = st.number_input("Recoverable original cost (USD)", min_value=0.0,
                                                  value=defaults.get("replaced_activity_cost"), key=f"{key}-recovered")
                else:
                    replacement = None
            reputation = st.number_input("Public rating (1–5, optional)", min_value=1.0, max_value=5.0,
                                         value=defaults.get("reputation"), step=0.1, key=f"{key}-rating")
            reputation_source = st.text_input("Rating source", defaults.get("reputation_source", ""),
                                              placeholder="e.g. Google Maps", key=f"{key}-rating-source")
            notes = st.text_area("Things to check", defaults.get("notes", ""), key=f"{key}-notes")
            luggage = st.checkbox("Need somewhere to store luggage", defaults.get("luggage", False), key=f"{key}-luggage")
            default_point = defaults.get("coordinates", ", ".join(map(str, defaults.get("point", []))))
            map_name = st.text_input("Google Maps place or address (optional)", defaults.get("map_name", ""), key=f"{key}-map-name")
            coordinates = st.text_input("Stop latitude, longitude (optional)", default_point, key=f"{key}-point")
            if coordinates.strip() and point_from_text(coordinates) is None:
                st.caption("Use a valid latitude and longitude separated by a comma.")
        return {"name": name, "start": None if start is None else start.hour * 60 + start.minute,
                "visit_minutes": visit, "travel_minutes": travel, "transport_cost": transport,
                "activity_cost": activity, "notes": notes, "luggage": luggage, "map_name": map_name,
                "point": point_from_text(coordinates), "interest": interest,
                "route_fit": fit, "reputation": reputation, "reputation_source": reputation_source,
                "replaces": replacement, "replaced_minutes": released, "replaced_activity_cost": recoverable}


def show_itinerary_day(day):
    if not day["items"]:
        st.caption("No original activities were recognized for this day.")
        return
    rows = []
    for item in day["items"]:
        start, end = item["start"], item["end"]
        if start is not None:
            timing = f"Locked · {clock(start)}"
            timing += "–" + clock(end) if end is not None else " · end unknown"
        elif item.get("flexible"):
            timing = "Flexible"
        else:
            timing = "Untimed"
        rows.append({"Activity": item["text"], "Schedule": timing})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def trial_fields(idea, saved, key, park, has_flexible, extra_cap):
    defaults = {**idea, **saved}
    values = dict(idea)
    visit, drive, price = st.columns(3)
    values["visit_minutes"] = visit.number_input(
        "Ride, queue and walk (min)" if park else "Visit time (min)", min_value=1, max_value=1440,
        value=defaults.get("visit_minutes") or DEFAULT_VISIT, key=f"{key}-visit",
    )
    if "visit_minutes" not in idea:
        visit.caption(f"Editable estimate · {DEFAULT_VISIT} min")
    values["travel_minutes"] = 0 if park else drive.number_input(
        "Extra driving time (min)", min_value=0, max_value=1440, value=defaults.get("travel_minutes"),
        key=f"{key}-drive", help="Extra driving time for the full visit block, including getting back to your route.",
    )
    if extra_cap is not None or "transport_cost" in idea or "activity_cost" in idea:
        values["transport_cost"] = price.number_input("Extra transport (USD)", min_value=0.0,
                                                     value=defaults.get("transport_cost"), key=f"{key}-transport")
        values["activity_cost"] = price.number_input("Tickets and other costs (USD)", min_value=0.0,
                                                    value=defaults.get("activity_cost"), key=f"{key}-tickets")
    else:
        values["transport_cost"] = 0.0
        values["activity_cost"] = price.number_input("Total extra cost (USD)", min_value=0.0,
                                                    value=defaults.get("activity_cost"), key=f"{key}-cost",
                                                    help="Include extra driving costs, admission, and other costs. Enter zero for a known zero.")
    left, right = st.columns(2)
    values["interest"] = left.slider("Worth visiting for you", 1, 5, defaults.get("interest", 3),
                                     key=f"{key}-interest")
    values["route_fit"] = right.slider("How well it fits your route", 1, 5, defaults.get("route_fit", 3),
                                      key=f"{key}-fit")
    if has_flexible:
        with st.expander("If this replaces a flexible stop"):
            values["replaced_activity_cost"] = st.number_input(
                "Recoverable cost of the original stop (USD)", min_value=0.0,
                value=defaults.get("replaced_activity_cost"), key=f"{key}-recovery",
                help="Enter zero if cancelling the original stop saves nothing.",
            )
    values["point"] = point_from_text(idea.get("coordinates", ""))
    return values


def guided_trial(day, day_index, trip, token, settings, park):
    entry = trip["entries"].setdefault(day_index, {})
    question = st.text_input("What would you like to change?", value=entry.get("question", ""),
                             placeholder="Add the art museum this afternoon", key=f"{token}-question")
    entry["question"] = question
    target = requested_day(question)
    if target is not None and target != day_index + 1:
        st.info(f"This request names Day {target}. Select that day before trying the change.")
        return None
    imported = day.get("detour", {}).get("candidates", []) or day.get("ideas", [])
    ideas = change_ideas(question, allow_names=True) if question.strip() else imported
    if not ideas:
        if question.strip():
            st.info("Enter the name of a stop, or join up to three alternatives with ‘or’.")
        return None
    if len(ideas) > 3:
        st.caption("Comparing the first three ideas.")
    ideas = ideas[:3]
    form_key = sha256(json.dumps(ideas, sort_keys=True).encode()).hexdigest()[:12]
    saved = entry.get("trial", {})
    matched = saved.get("key") == form_key
    saved = saved if matched else {}
    items = day["items"]
    has_flexible = any(item.get("flexible") for item in items)
    unknown_ends = [item for item in items if item["start"] is not None and item["end"] is None]
    if unknown_ends:
        st.caption("A locked event with an unknown end reserves the rest of the planning window. "
                   "Add an end estimate under Planning hours and preferences if you need to schedule after it.")

    with st.form(f"{token}-{form_key}", border=True):
        values = []
        for index, idea in enumerate(ideas):
            st.text(idea["name"])
            stored = saved.get("options", [{}] * len(ideas))[index]
            values.append(trial_fields(idea, stored, f"{token}-{form_key}-{index}", park,
                                       has_flexible, settings.get("extra_transport_cap")))
        remaining = None
        if free_minutes(items, DEFAULT_DAY) is None or "free_minutes" in settings:
            remaining = st.number_input("Free time after the original plan (min)", min_value=0, max_value=1440,
                                        value=saved.get("free_override", settings.get("free_minutes")),
                                        key=f"{token}-{form_key}-free")
        bounds = saved.get("bounds", DEFAULT_DAY)
        st.caption(f"Planning hours · {clock(bounds[0])}–{clock(bounds[1])} local time")
        end_times = {}
        with st.expander("Planning hours and preferences"):
            columns = st.columns(3)
            first = columns[0].time_input("Earliest start", as_time(bounds[0]), key=f"{token}-{form_key}-earliest")
            last = columns[1].time_input("Latest finish", as_time(bounds[1]), key=f"{token}-{form_key}-latest")
            rest = columns[2].number_input("Minimum rest to keep (min)", min_value=0, max_value=1440,
                                           value=saved.get("rest", settings.get("minimum_rest", 0)),
                                           key=f"{token}-{form_key}-rest")
            weights = [col.slider(label, 0, 100, weight, key=f"{token}-{form_key}-weight-{index}")
                       for index, (col, label, weight) in enumerate(zip(
                           st.columns(3), DIMENSIONS, saved.get("weights", DEFAULT_WEIGHTS)))]
            for item in unknown_ends:
                line = item["line"]
                estimated = st.time_input(f'End estimate · {item["text"]}',
                                          as_time(saved.get("end_times", {}).get(line)),
                                          key=f"{token}-{form_key}-end-{line}")
                if estimated is not None:
                    end_times[line] = as_minutes(estimated)
        if st.form_submit_button("Try schedule", type="primary", icon=":material/event_available:"):
            saved = {"key": form_key, "options": values, "bounds": (as_minutes(first), as_minutes(last)),
                     "free_override": remaining, "rest": rest, "weights": weights, "end_times": end_times}
            entry["trial"] = saved

    if not saved:
        return None
    bounds = saved["bounds"]
    if bounds[0] >= bounds[1]:
        st.warning("Choose a latest finish after the earliest start.")
        return None
    end_times = saved.get("end_times", {})
    if any(end_times[item["line"]] <= item["start"] for item in unknown_ends if item["line"] in end_times):
        st.warning("An estimated end must be after the locked start time.")
        return None
    items = [{**item, "end": end_times.get(item["line"], item["end"])} for item in items]
    free = saved["free_override"]
    calculated = free_minutes(items, bounds)
    if calculated is not None:
        free = calculated if free is None else min(free, calculated)
    if free is None:
        st.warning("Estimate the free time left after the original untimed activities.")
        return None
    result = []
    for option in saved["options"]:
        fields = {"travel_minutes": "extra driving time", "transport_cost": "transport cost",
                  "activity_cost": "visit or total extra cost"}
        missing = [label for key, label in fields.items() if option[key] is None]
        if missing:
            st.warning(f'{option["name"]} · Add {", ".join(missing)} to try this option.')
            continue
        slot, issue = trial_slot(items, bounds, option, free, saved["rest"])
        if issue:
            st.warning(f'{option["name"]} · {issue}')
            continue
        recoverable = option.get("replaced_activity_cost")
        if slot["replaces"] and recoverable is None:
            st.info(f'{option["name"]} fits if it replaces {slot["replaces"]}. Enter its recoverable cost to compare.')
            continue
        notes = option.get("notes", "")
        if any(item["line"] in end_times and item["end"] <= slot["start"] for item in items):
            notes = "\n".join(filter(None, [notes, "Verify estimated end times before leaving for this stop."]))
        result.append({**option, **slot, "replaced_activity_cost": recoverable if slot["replaces"] else 0,
                       "notes": notes})
    return {"candidates": result, "free": free, "rest": saved["rest"], "weights": saved["weights"],
            "question": question or settings.get("question", ""),
            "locks": [item for item in items if item["start"] is not None]}


def show_comparison(rows):
    table = [
        {"Option": row["label"], "Transport": row.get("transport", "—"),
         "Total score / 100": row["score"], "Extra time (hours)": round(row["extra_minutes"] / 60, 2),
         "Extra cost (USD)": row["extra_cost"], "Rest left (min)": row["rest_minutes"],
         "Trip total (USD)": row["trip_spend"], "Budget left (USD)": row["budget_left"],
         "Status": "Conflict" if row["hard_conflicts"] else "Available"}
        for row in rows
    ]
    comparison = pd.DataFrame(table)
    if comparison["Transport"].nunique() <= 1:
        comparison = comparison.drop(columns="Transport")
    st.dataframe(comparison, width="stretch", hide_index=True)
    chart_measure = "Extra cost (USD)"
    if not comparison[chart_measure].any():
        chart_measure = "Extra time (hours)"
    if comparison[chart_measure].any():
        chart = comparison[["Option", chart_measure]].copy()
        chart["Option"] = [f"{index}. {name}" for index, name in enumerate(chart["Option"])]
        st.bar_chart(chart, x="Option", y=chart_measure, horizontal=True, sort=False,
                     height=max(160, len(chart) * 35 + 64), alt=f"{chart_measure} by option")


def show_decision(row, case, review, trip, day_index):
    signature = sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()[:16]
    selected = row["option_id"]
    original = selected == "keep_original"
    acknowledged = False
    if not original and row["score"] < LOW_SCORE and not row["hard_conflicts"]:
        acknowledged = st.checkbox(
            ("I’m okay with the time and cost shown." if row.get("replaces")
             else f'I’m okay with an extra {format_hours(row["extra_minutes"])} and ${row["extra_cost"]:,.2f}.'),
            key=f"{signature}-{selected}-ack",
        )
    if not review["passed"]:
        st.error("Review found an inconsistency. Check your inputs before adding a stop.")
    has_estimate = trip["baseline_spend"] is not None
    if not original and not has_estimate:
        st.caption("Enter the original trip estimate under Trip costs to confirm this change.")

    accept = False
    if original:
        reject = st.button("Keep original", type="primary", width="stretch", key="keep-baseline",
                           icon=":material/bookmark:")
    else:
        new_choice, baseline = st.columns(2)
        action_label = "Use this replacement" if row.get("replaces") else "Add this stop"
        action_icon = ":material/swap_horiz:" if row.get("replaces") else ":material/add_circle:"
        accept = new_choice.button(action_label, type="primary", width="stretch", icon=action_icon,
                                   disabled=not has_estimate or not review["passed"]
                                   or bool(decision_error(row, "accept", acknowledged)))
        reject = baseline.button("Keep original", width="stretch", key="reject-option")
    if accept or reject:
        trip["decisions"][day_index] = {
            "option_id": selected if accept else "keep_original",
            "action": "accept" if accept else "reject",
            "label": row["label"] if accept else "Original plan",
            "row": row if accept else None, "case": case,
        }
        st.rerun()


def show_saved_decision(trip, day_index, day_count, picker_key, sample):
    outcome = trip["decisions"].get(day_index)
    if not outcome:
        return
    st.success(f'{outcome["label"]} confirmed.')
    if outcome["action"] == "accept":
        if st.button("Restore this day's original plan", icon=":material/undo:"):
            del trip["decisions"][day_index]
            st.rerun()
    if day_index + 1 < day_count:
        st.button(f"Continue to Day {day_index + 2}", icon=":material/arrow_forward:",
                  on_click=st.session_state.__setitem__, args=(picker_key, day_index + 1))
    if sample and not HOSTED_REVIEW.is_file():
        with st.expander("Course evidence"):
            if st.button("Save anonymized decision to the local workflow log"):
                saved = {**outcome["case"], "case_id": f"interactive-{uuid4().hex}"}
                raw_path = ROOT / "data" / "raw" / f'{saved["case_id"]}.json'
                with raw_path.open("x", encoding="utf-8") as output:
                    json.dump(saved, output, ensure_ascii=False, indent=2)
                result = run_refresh(raw_path, ROOT)
                if result["status"] == "reviewed":
                    save_human_decision(DB, result["run_id"], outcome["option_id"], outcome["action"],
                                        datetime.now(timezone.utc).isoformat())
                    st.success(f'Decision saved with run {result["run_id"]}.')
                else:
                    st.error("The workflow stopped. See the run log.")


def show_open_itinerary(message):
    with st.container(key="itinerary-welcome"):
        st.subheader("Open your itinerary", anchor=False)
        st.write(message)
        st.button("Try the Disneyland demo", icon=":material/map:", on_click=st.session_state.update,
                  args=({"itinerary-source": "Try a demo", "demo-itinerary": "Disneyland two-day demo"},))


st.set_page_config(page_title="Worth the Detour", page_icon="🧭", layout="wide", initial_sidebar_state="auto")
st.html(UI / "dashboard.css")
st.title(":material/explore: Worth the Detour", anchor=False)

workbench, history = st.tabs(["Plan a change", "Latest reviewed workflow"])
with history:
    hosted = HOSTED_REVIEW.is_file()
    recent = json.loads(HOSTED_REVIEW.read_text(encoding="utf-8")) if hosted else get_latest_evaluation(DB)
    if recent is None:
        st.info("Run the refresh workflow to see its reviewed result here.")
        st.code("python scripts/refresh_dashboard.py data/raw/05_transfer_day_modes.json")
    else:
        if "trip_budget" in recent["metrics"]:
            st.subheader(recent["candidate_name"])
        st.write(recent["recommendation"])
        prefix = "Demo snapshot · " if hosted else ""
        st.caption(f'{prefix}Input date {recent["received_date"]} · Review completed {recent["processed_at"]} · {recent["raw_filename"]}')
        if "trip_budget" in recent["metrics"]:
            for option in recent["options"]:
                for conflict in option["hard_conflicts"]:
                    st.warning(f'{option["label"]} · {conflict}')
            show_comparison(recent["options"])
            st.caption("Times and costs come from the input estimates.")
        else:
            st.dataframe(pd.DataFrame(recent["options"]), width="stretch", hide_index=True)
        with st.expander("Reviewer checks"):
            st.json(recent["reviewer_checks"], expanded=False)

with workbench:
    source_panel = st.sidebar
    source_panel.header("Your itinerary", anchor=False)
    source = source_panel.selectbox("Itinerary source", ["Upload Markdown", "Paste Markdown", "Try a demo"],
                                    key="itinerary-source")
    if source == "Try a demo":
        source = source_panel.radio(
            "Demo itinerary", ["Disneyland two-day demo", "Anonymized example"],
            format_func=lambda label: "Los Angeles trip demo" if label == "Anonymized example" else label,
            key="demo-itinerary",
        )
    if source == "Upload Markdown":
        upload = source_panel.file_uploader("Markdown file", type=["md", "markdown"])
        with source_panel.popover("Markdown template & AI prompt", icon=":material/help_outline:"):
            show_upload_instructions()
        if upload is None:
            show_open_itinerary("Start with a Markdown itinerary in the sidebar. Compare a change and take the updated plan with you.")
            st.stop()
        try:
            markdown = upload.getvalue().decode("utf-8-sig")
        except UnicodeDecodeError:
            source_panel.error("Upload a UTF-8 Markdown file.")
            st.stop()
    elif source == "Anonymized example":
        markdown = EXAMPLE.read_text(encoding="utf-8")
    elif source == "Disneyland two-day demo":
        markdown = DISNEYLAND_DEMO.read_text(encoding="utf-8")
    else:
        markdown = source_panel.text_area("Paste your itinerary", height=170, placeholder="## Day 1 — City")
        if not markdown.strip():
            show_open_itinerary("Paste a Markdown itinerary in the sidebar to review a day and try a change.")
            st.stop()
    days = parse_itinerary(markdown)
    if not days:
        st.warning("No day headings found. Try '## Day 1 — City', '## 12/26 · City', or '## 第1天'.")
        st.stop()
    trip_key = "trip-" + sha256((source + "\0" + markdown).encode()).hexdigest()[:16]
    example = source == "Anonymized example"
    trip = st.session_state.setdefault(trip_key, {
        "decisions": {}, "entries": {}, "baseline_spend": 2200.0 if example else trip_setting(days, "baseline_spend"),
        "budget": 3000.0 if example else trip_setting(days, "trip_budget"),
    })
    picker_key = f"{trip_key}-day"
    accepted_days = {index for index, outcome in trip["decisions"].items() if outcome["action"] == "accept"}
    day_index = source_panel.selectbox("Which day are you deciding about?", range(len(days)),
                                      index=1 if example and len(days) > 1 else 0,
                                      format_func=lambda index: ("✓ " if index in accepted_days else "") + days[index]["title"],
                                      key=picker_key)
    day = days[day_index]
    current_markdown = export_trip(markdown, trip["decisions"])
    if accepted_days:
        source_panel.caption(f"{len(accepted_days)} of {len(days)} days updated")
    source_panel.download_button("Download itinerary", current_markdown, "updated-itinerary.md", "text/markdown",
                                 icon=":material/download:", on_click="ignore", width="stretch")
    sample = example and day_index == 1
    detour = day.get("detour", {})
    imported_settings = day_settings(day)
    park = (imported_settings.get("setting", "").casefold() == "park"
            or source == "Disneyland two-day demo")
    imported_options = detour.get("candidates", [])
    guided = not sample and (not imported_options or any(
        "start" not in option or "visit_minutes" not in option for option in imported_options))
    token = f"{trip_key}-{day_index}"
    decided = day_index in accepted_days
    summary = st.container()
    workspace = st.container()
    with summary:
        st.subheader(day["title"])
        if decided:
            show_saved_decision(trip, day_index, len(days), picker_key, sample)
            confirmed = parse_itinerary(current_markdown)[day_index]
            if park:
                show_park_order(confirmed, token)
            else:
                show_itinerary_day(confirmed)
        elif guided or source in ("Upload Markdown", "Paste Markdown"):
            if park:
                with st.expander("Original itinerary", expanded=True):
                    show_itinerary_day(day)
            else:
                show_itinerary_day(day)
    with st.expander("Trip costs"):
        left, right = st.columns(2)
        spending = left.number_input("Original trip estimate (USD)", min_value=0.0,
                                     value=trip["baseline_spend"], key=f"{trip_key}-spend",
                                     help="Total for the whole trip before any changes.")
        budget = right.number_input("Trip budget (USD)", min_value=0.0,
                                    value=trip["budget"], key=f"{trip_key}-budget", placeholder="Optional")
        trip["baseline_spend"], trip["budget"] = spending, budget
        changes = sum(outcome["row"]["extra_cost"] for outcome in trip["decisions"].values() if outcome["row"])
        if spending is not None:
            current_total = round(spending + changes, 2)
            st.metric("Current trip total", f"${current_total:,.2f}", f"${changes:,.2f} from confirmed changes")
            if budget is not None and current_total > budget:
                st.warning(f"The current itinerary is ${current_total - budget:,.2f} over your budget.")
    if decided:
        st.stop()

    locks = [item for item in day["items"] if item["start"] is not None]
    if guided:
        with workspace:
            trial = guided_trial(day, day_index, trip, token, imported_settings, park)
        if not trial or not trial["candidates"]:
            if park:
                show_park_order(day, token)
            st.stop()
        candidates, free_time, minimum_rest = trial["candidates"], trial["free"], trial["rest"]
        ideas, weights, locks = trial["question"], trial["weights"], trial["locks"]
        planned, extra_cap = 0, imported_settings.get("extra_transport_cap")
        workspace = st.container()
    else:
        defaults = json.loads(EXAMPLE_CASE.read_text(encoding="utf-8")) if sample else {
            "limits": imported_settings, "candidates": imported_options[:3],
        }
        if sample:
            example_points = example_route_data()[0]
            for option in defaults["candidates"]:
                if option["name"] in example_points:
                    option.setdefault("point", example_points[option["name"]])
        with st.expander("Edit options and limits"):
            options_editor, preferences_editor = st.tabs(["Options", "Day preferences"])
            default_limits = defaults["limits"]
            planned = default_limits.get("baseline_minutes", 0)
            free_default = default_limits["available_minutes"] - planned if sample else imported_settings.get("free_minutes")
            with options_editor:
                time_col, count_col = st.columns([2, 1])
                free_time = time_col.number_input("Free time today (min)", min_value=0, max_value=1440 - planned,
                                                  value=free_default, key=f"{token}-free-time",
                                                  help="Time left after your existing plans, including time you want to keep for rest.")
                presets = defaults["candidates"]
                count = count_col.selectbox("Options", [1, 2, 3], index=max(0, len(presets) - 1), key=f"{token}-count")
                candidates = []
                for i, tab in enumerate(st.tabs([f"Option {i + 1}" for i in range(count)])):
                    with tab:
                        candidate_defaults = presets[i] if i < len(presets) else {}
                        candidates.append(candidate_fields(i, candidate_defaults, token, day["items"],
                                                           imported=i < len(imported_options) and not sample, park=park))
            with preferences_editor:
                ideas = st.text_input("What are you deciding? (optional)",
                                      "Should we add a stop between hotels?" if sample else imported_settings.get("question", ""),
                                      placeholder="e.g. Is the waterfront worth the detour?", key=f"{token}-idea")
                first, second = st.columns(2)
                extra_cap = first.number_input("Extra transport limit (USD)", min_value=0.0,
                                               value=100.0 if sample else imported_settings.get("extra_transport_cap"), key=f"{token}-extra")
                minimum_rest = second.number_input("Minimum rest to keep (min)", min_value=0,
                                                   value=default_limits.get("minimum_rest", 0), key=f"{token}-rest")
                st.caption("Adjust how much each dimension matters to you.")
                weights = [col.slider(label, 0, 100, default, key=f"{token}-weight-{index}")
                           for index, (col, label, default) in enumerate(zip(st.columns(3), DIMENSIONS, DEFAULT_WEIGHTS))]
    limits = {
        "trip_budget": budget if spending is not None else None,
        "baseline_spend": None if spending is None else round(spending + changes, 2),
        "extra_transport_cap": extra_cap,
        "available_minutes": None if free_time is None else free_time + planned,
        "baseline_minutes": planned, "minimum_rest": minimum_rest,
    }
    case = {
        "schema": DECISION_SCHEMA, "case_id": "preview", "received_date": date.today().isoformat(),
        "day_title": day["title"], "question": ideas, "limits": limits, "weights": weights,
        "transport_mode": None if park else DRIVE_MODE,
        "locks": locks, "candidates": candidates,
        "flexible_stops": [item["text"] for item in day["items"] if item["start"] is None],
    }
    if len(imported_options) > 3:
        summary.warning("Imported the first three options. Compare additional stops in a separate decision.")
    errors = validate_decision(case)
    if errors:
        with summary:
            st.info("Score pending. Review the missing or invalid inputs below.")
            with st.expander("Missing or invalid information"):
                for error in errors:
                    st.write(error)
        if park or sample or any(option["name"] for option in candidates):
            with workspace:
                if park:
                    show_park_order(day, token)
                else:
                    st.caption("Check directions while you fill in estimates.")
                    index = 0
                    if len(candidates) > 1:
                        index = st.selectbox("Route to check", range(len(candidates)),
                                             format_func=lambda i: candidates[i]["name"] or f"Option {i + 1}",
                                             key=f"{token}-unscored-route")
                    display_route(candidates[index], sample, token, imported_settings)
        st.stop()

    proposal = PlannerAgent().propose(case)
    review = ReviewerAgent().review(case, proposal)
    rows = proposal["options"]
    by_id = {row["option_id"]: row for row in rows}
    recommended = proposal["assessment"]["recommended_option"]
    first_inspection = rows[1]["option_id"] if guided and len(candidates) == 1 else recommended
    with summary:
        if imported_options:
            count = min(len(imported_options), 3)
            st.caption(f"{count} {'option' if count == 1 else 'options'} loaded from your Markdown.")
        if recommended == "keep_original":
            st.write(proposal["assessment"]["recommendation"])
        else:
            st.write(f'Suggested stop · **{by_id[recommended]["label"]}**')

    with workspace:
        choice_column, map_column = st.columns([2, 3], gap="large")
        with choice_column:
            with st.container(border=True):
                st.subheader("Choose an option")
                selected = st.selectbox("Option to inspect", list(by_id),
                                        index=list(by_id).index(first_inspection),
                                        format_func=lambda key: by_id[key]["label"],
                                        label_visibility="collapsed", key=f"{token}-selected")
                row = by_id[selected]
                if row.get("replaces"):
                    st.caption("Replaces " + row["replaces"].removeprefix("Flexible · ").split(" · ", 1)[0])
                if not park and row["option_id"] != "keep_original":
                    st.caption(f'Proposed block · {clock(row["start"])}–{clock(row["end"])} including extra driving')
                show_total_score(row["score"])
                time_col, cost_col = st.columns(2)
                time_col.metric("Time saved" if row["extra_minutes"] < 0 else "Extra time",
                                format_hours(abs(row["extra_minutes"])))
                cost_col.metric("Cost saved" if row["extra_cost"] < 0 else "Extra cost",
                                f'${abs(row["extra_cost"]):,.2f}')
                for issue in row["hard_conflicts"]:
                    st.error(issue)
                low_score = row["score"] is not None and row["score"] < LOW_SCORE
                if low_score:
                    for note in row["notes"]:
                        st.caption(note)
                show_decision(row, case, review, trip, day_index)
                show_saved_decision(trip, day_index, len(days), picker_key, sample)
                facts = [f'{row["rest_minutes"]:g} min rest left']
                if row["trip_spend"] is not None:
                    facts.append(f'${row["trip_spend"]:,.2f} projected total')
                if row["budget_left"] is not None:
                    amount = row["budget_left"]
                    facts.append(f'${abs(amount):,.2f} {"budget left" if amount >= 0 else "over budget"}')
                st.caption(" · ".join(facts))
                if not low_score:
                    for note in row["notes"]:
                        st.caption(note)
        option = candidates[int(selected.split("_")[1]) - 1] if selected != "keep_original" else None
        with map_column:
            if park:
                show_park_order(day, token, row)
            else:
                display_route(option, sample, token, imported_settings)

    with st.expander("Compare all options"):
        st.caption(f'Data date {case["received_date"]} · Times and costs are estimates.')
        show_comparison(rows)
        if limits["trip_budget"] is not None:
            st.caption(f'Trip budget ${limits["trip_budget"]:,.2f}')
        st.write("Worth visiting starts with your interest. Route fit is how naturally the stop fits your day's route. Time cost measures how much of your flexible time the change uses.")
        if proposal["assessment"]["metrics"]["reputation_in_score"]:
            st.caption("Worth visiting uses 70% personal interest and 30% public rating. Every alternative has a sourced rating.")
            st.text("Rating sources: " + " · ".join(f'{option["name"]} ({option["reputation_source"]})' for option in candidates))
        else:
            st.caption("Worth visiting uses your interest alone. Add a sourced public rating for every alternative to include reputation.")
        st.caption(f"Suggestion threshold: {LOW_SCORE}. Transport spending is checked against your budget and transport limit.")
        st.dataframe(pd.DataFrame([{"Option": row["label"], **row["components"]} for row in rows[1:]]),
                     width="stretch", hide_index=True)
