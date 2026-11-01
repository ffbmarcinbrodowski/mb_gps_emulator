"""
Route generation with the GraphHopper Routing API - our own server by default
(config.GRAPHHOPPER_ROUTE_URL), or the public API on graphhopper.com.

Docs: https://docs.graphhopper.com/#tag/Routing-API
"""
import requests

from . import config, geo


# Per-segment road data used by core/motion.py for a realistic speed profile.
# GraphHopper returns each as intervals: [[from_point, to_point, value], ...]
PROFILE_DETAILS = ("average_speed", "max_speed", "road_class")
PUBLIC_MAX_POINTS = 5           # points per request on graphhopper.com (free plan)

# Servers without elevation data (answered "Elevation not supported!") - asked without it.
_no_elevation = set()


class RoutingError(Exception):
    pass


def generate_route(start, end, profile=None, via=()):
    """
    Ask GraphHopper for a route between two points, optionally through stops in between.

    start, end: (lat, lon); via: [(lat, lon), ...] visited in this order
    Returns {"points": [[lat, lon, ele], ...], "distance_m": float, "duration_s": float,
             "profile": {"average_speed": [...], "max_speed": [...], "road_class": [...]},
             "stop_indices": [index into points of every via point]}
    """
    if is_public() and not config.GRAPHHOPPER_API_KEY:
        raise RoutingError("GraphHopper API key missing (see graphhopper_api.txt)")

    # A limit on points per request (public API): ask for overlapping parts (the last point
    # of one part is the first of the next) and join them.
    waypoints = [start, *via, end]
    step = (max_points() or len(waypoints)) - 1
    parts = [_request(waypoints[i:i + step + 1], profile) for i in range(0, len(waypoints) - 1, step)]
    return _join(parts)


def is_public():
    """Is the route URL the public GraphHopper API (key required, few points per request)?"""
    return "graphhopper.com" in config.GRAPHHOPPER_ROUTE_URL


def max_points():
    """Points per request: GRAPHHOPPER_MAX_POINTS, else 5 on graphhopper.com and no limit (None) elsewhere."""
    return config.GRAPHHOPPER_MAX_POINTS or (PUBLIC_MAX_POINTS if is_public() else None)


def _request(waypoints, profile):
    """One GraphHopper request through all `waypoints` (first = start, last = end)."""
    url = config.GRAPHHOPPER_ROUTE_URL
    elevation = url not in _no_elevation
    params = [
        *[("point", f"{lat},{lon}") for lat, lon in waypoints],
        ("profile", profile or config.GRAPHHOPPER_PROFILE),
        ("points_encoded", "false"),   # plain GeoJSON coordinates
        ("elevation", "true" if elevation else "false"),
        ("instructions", "false"),
        ("locale", "en"),
        *[("details", name) for name in PROFILE_DETAILS],
    ]
    if is_public():
        params.append(("key", config.GRAPHHOPPER_API_KEY))   # our own server needs no key
    try:
        resp = requests.get(url, params=params, timeout=20)
    except requests.RequestException as exc:
        raise RoutingError(f"GraphHopper request failed ({url}): {exc}") from exc

    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code != 200:
        if elevation and "elevation not supported" in str(body.get("message", "")).lower():
            # The server has no elevation data: route without it (altitude 0 in the records).
            _no_elevation.add(url)
            return _request(waypoints, profile)
        raise RoutingError(body.get("message") or f"GraphHopper HTTP {resp.status_code}")
    if not body.get("paths"):
        raise RoutingError("GraphHopper returned no route")

    path = body["paths"][0]
    # GeoJSON order is [lon, lat, ele] -> convert to [lat, lon, ele]
    points = [[c[1], c[0], round(c[2], 1) if len(c) > 2 else 0] for c in path["points"]["coordinates"]]

    return {
        "points": points,
        "distance_m": path["distance"],
        "duration_s": path["time"] / 1000,
        "profile": {name: path.get("details", {}).get(name) for name in PROFILE_DETAILS},
        "stop_indices": _stop_indices(points, path.get("snapped_waypoints", {}).get("coordinates", [])[1:-1]),
    }


def _join(parts):
    """
    One route from consecutive parts. Part k starts where part k-1 ends, so its first point
    is dropped and its point indices (details, stops) move by the length of the route so far.
    The end of every part but the last is a stop too.
    """
    route = dict(parts[0])
    route["profile"] = {name: list(route["profile"][name] or []) for name in PROFILE_DETAILS}
    route["stop_indices"] = list(route["stop_indices"])
    for part in parts[1:]:
        offset = len(route["points"]) - 1
        route["stop_indices"].append(offset)
        route["stop_indices"] += [i + offset for i in part["stop_indices"]]
        route["points"] = route["points"] + part["points"][1:]
        route["distance_m"] += part["distance_m"]
        route["duration_s"] += part["duration_s"]
        for name in PROFILE_DETAILS:
            route["profile"][name] += [[a + offset, b + offset, v] for a, b, v in part["profile"][name] or []]
    if len(parts) == 1:
        route["profile"] = parts[0]["profile"]
    return route


def _stop_indices(points, snapped):
    """
    Route point index of every via point. GraphHopper's snapped waypoints are vertices
    of the path, so look for the same coordinates (in order); the nearest point is a fallback.
    """
    indices, start = [], 0
    for lon, lat, *_ in snapped:
        candidates = range(start, len(points))
        index = next((i for i in candidates if points[i][0] == lat and points[i][1] == lon), None)
        if index is None:
            index = min(candidates, key=lambda i: geo.haversine_m(lat, lon, points[i][0], points[i][1]))
        indices.append(index)
        start = index
    return indices


def straight_line_route(start, end, step_m=100):
    """
    Fallback route (straight line split every `step_m` meters).
    Used by seed.py when GraphHopper is not reachable.
    """
    total = geo.haversine_m(start[0], start[1], end[0], end[1])
    steps = max(1, int(total // step_m))
    points = [
        [start[0] + (end[0] - start[0]) * i / steps, start[1] + (end[1] - start[1]) * i / steps, 0]
        for i in range(steps + 1)
    ]
    return {"points": points, "distance_m": total, "duration_s": total / (50 / 3.6), "profile": None,
            "stop_indices": []}
