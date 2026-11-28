"""Transport choices, route geometry, and Google Maps directions links."""

import json
from math import ceil, isfinite
import re
from threading import Lock
from time import monotonic, sleep
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DRIVE_MODE = "driving"
TRANSPORT = {
    None: ("Undecided", None),
    "driving": ("Drive", "driving"),
    "rideshare": ("Taxi / rideshare", "driving"),
    "transit": ("Public transit", "transit"),
    "walking": ("Walk", "walking"),
    "bicycling": ("Bike", "bicycling"),
}
ROAD_PROFILES = {
    "driving": ("car", "driving"),
    "rideshare": ("car", "driving"),
    "walking": ("foot", "walking"),
    "bicycling": ("bike", "cycling"),
}
_request_lock = Lock()
_next_request = {}


def _request_json(provider, url):
    request = Request(url, headers={"User-Agent": "WorthTheDetour-CoursePrototype/1.0"})
    with _request_lock:
        delay = _next_request.get(provider, 0) - monotonic()
        if delay > 0:
            sleep(delay)
        _next_request[provider] = monotonic() + 1
    with urlopen(request, timeout=6) as response:
        return json.loads(response.read(1_000_000))


def transport_label(mode):
    return TRANSPORT[mode][0]


def route_key(points, mode):
    """Share cached road geometry between driving and rideshare."""
    if mode not in ROAD_PROFILES or len(points) < 2 or not all(points):
        return None
    return ROAD_PROFILES[mode][0], tuple((round(lat, 6), round(lon, 6)) for lat, lon in points)


def decode_polyline6(encoded):
    """Decode OSRM's full-resolution route as Leaflet latitude/longitude pairs."""
    coordinates = []
    values = [0, 0]
    result = shift = index = 0
    for char in encoded:
        chunk = ord(char) - 63
        if not 0 <= chunk < 64 or shift > 30:
            raise ValueError("Invalid route geometry.")
        result |= (chunk & 31) << shift
        if chunk & 32:
            shift += 5
            continue
        values[index % 2] += ~(result >> 1) if result & 1 else result >> 1
        if index % 2:
            lat, lon = values
            if abs(lat) > 90_000_000 or abs(lon) > 180_000_000:
                raise ValueError("Invalid route coordinate.")
            coordinates.append([lat / 1_000_000, lon / 1_000_000])
        index += 1
        result = shift = 0
    if shift or index % 2 or len(coordinates) < 2:
        raise ValueError("Incomplete route geometry.")
    return coordinates


def fetch_road_route(points, mode):
    """Fetch a route on demand from the FOSSGIS demo router."""
    profile = ROAD_PROFILES[mode]
    road_points = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    payload = _request_json(
        "routing",
        f"https://routing.openstreetmap.de/routed-{profile[0]}/route/v1/{profile[1]}/{road_points}"
        "?geometries=polyline6&overview=full&generate_hints=false",
    )
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise ValueError("No route was found.")
    route = payload["routes"][0]
    if any(not isfinite(route[key]) or route[key] < 0 for key in ("distance", "duration")):
        raise ValueError("Invalid route estimate.")
    return {
        "path": decode_polyline6(route["geometry"]),
        "distance_m": route["distance"],
        "duration_s": route["duration"],
    }


def _search_places(query):
    url = "https://photon.komoot.io/api/?" + urlencode({"q": query, "limit": 5})
    places = []
    for result in _request_json("geocoding", url)["features"]:
        fields = result["properties"]
        if result["geometry"]["type"] != "Point":
            continue
        lon, lat = result["geometry"]["coordinates"]
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        kind = fields.get("osm_value")
        street = fields.get("street") or ""
        address = " ".join(fields[key] for key in ("housenumber", "street") if fields.get(key))
        name = fields.get("name") or address
        if not name:
            continue
        access = fields.get("extra") or {}
        if (kind in {"footway", "pedestrian", "steps", "path", "platform", "subway_entrance",
                     "bus_stop", "tram_stop", "swimming_pool"}
                or re.search(r"\b(?:walkway|footbridge|footpath|pedestrian (?:bridge|entrance))\b",
                             f"{name} {street}", re.I)
                or any(access.get(tag) in {"no", "private"} for tag in ("access", "vehicle", "motor_vehicle", "motorcar"))):
            continue
        parking = kind in {"parking", "parking_entrance"}
        street_address = bool(street and fields.get("housenumber"))
        if not (parking or street_address) and fields.get("osm_key") in {"highway", "boundary", "place"}:
            continue
        access_hint = "Parking" if parking else "Street address" if street_address else None
        parts = dict.fromkeys(part for part in (name, address, fields.get("city"), fields.get("state")) if part)
        places.append({"name": name, "label": ", ".join(parts), "point": [lat, lon], "access": access_hint})
    return places


def find_route_places(query):
    """Prioritize driving locations and look for a named car park when useful."""
    places = _search_places(query)
    name, separator, context = query.partition(",")
    name_words = set(re.findall(r"[^\W_]+", name.casefold()))
    if name_words and "parking" not in name_words and not any(place["access"] for place in places):
        try:
            parking = _search_places(f"{name} parking{separator}{context}")
        except OSError:
            parking = []
        places.extend(place for place in parking if place["access"] == "Parking"
                      and name_words <= set(re.findall(r"[^\W_]+", place["name"].casefold())))
    places.sort(key=lambda place: place["access"] is None)
    return places


def compare_driving_routes(points, cache):
    """Compare the same two anchors with and without the selected stop."""
    start, stop, end = points
    estimates = []
    for route in ([start, end], [start, stop, end]):
        key = route_key(route, DRIVE_MODE)
        if key not in cache:
            cache[key] = fetch_road_route(route, DRIVE_MODE)
        estimates.append({**cache[key], "minutes": ceil(cache[key]["duration_s"] / 60)})
    return estimates


def directions_links(addresses, mode):
    if len(addresses) < 2 or not all(addresses):
        return []

    def build(stops):
        params = {"api": 1, "origin": stops[0], "destination": stops[-1]}
        maps_mode = TRANSPORT[mode][1]
        if maps_mode:
            params["travelmode"] = maps_mode
        if len(stops) > 2:
            params["waypoints"] = "|".join(stops[1:-1])
        return "https://www.google.com/maps/dir/?" + urlencode(params)

    # Split transit into legs for Maps clients that ignore waypoints.
    if mode == "transit" and len(addresses) > 2:
        return [(f"Open transit leg {i} in Google Maps", build(leg))
                for i, leg in enumerate(zip(addresses, addresses[1:]), start=1)]
    return [("Open in Google Maps", build(addresses))]
