"""Transport choices, route geometry, and Google Maps directions links."""

import json
from threading import Lock
from time import monotonic
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
_next_request = 0.0


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
    global _next_request
    profile = ROAD_PROFILES[mode]
    road_points = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    request = Request(
        f"https://routing.openstreetmap.de/routed-{profile[0]}/route/v1/{profile[1]}/{road_points}"
        "?geometries=polyline6&overview=full&generate_hints=false",
        headers={"User-Agent": "WorthTheDetour-CoursePrototype/1.0"},
    )
    with _request_lock:
        now = monotonic()
        if now < _next_request:
            raise ValueError("Please wait a moment before requesting another route.")
        _next_request = now + 1.0
    with urlopen(request, timeout=6) as response:
        payload = json.loads(response.read(1_000_000))
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise ValueError("No route was found.")
    route = payload["routes"][0]
    return {
        "path": decode_polyline6(route["geometry"]),
        "distance_m": route["distance"],
    }


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
