"""
Route generation with the GraphHopper Routing API.

Docs: https://docs.graphhopper.com/#tag/Routing-API
"""
import requests

from . import config, geo


# Per-segment road data used by core/motion.py for a realistic speed profile.
# GraphHopper returns each as intervals: [[from_point, to_point, value], ...]
PROFILE_DETAILS = ("average_speed", "max_speed", "road_class")


class RoutingError(Exception):
    pass


def generate_route(start, end, profile=None):
    """
    Ask GraphHopper for a route between two points.

    start, end: (lat, lon)
    Returns {"points": [[lat, lon, ele], ...], "distance_m": float, "duration_s": float,
             "profile": {"average_speed": [...], "max_speed": [...], "road_class": [...]}}
    """
    if not config.GRAPHHOPPER_API_KEY:
        raise RoutingError("GraphHopper API key missing (see graphhopper_api.txt)")

    params = [
        ("point", f"{start[0]},{start[1]}"),
        ("point", f"{end[0]},{end[1]}"),
        ("profile", profile or config.GRAPHHOPPER_PROFILE),
        ("points_encoded", "false"),   # plain GeoJSON coordinates
        ("elevation", "true"),
        ("instructions", "false"),
        ("locale", "en"),
        ("key", config.GRAPHHOPPER_API_KEY),
        *[("details", name) for name in PROFILE_DETAILS],
    ]
    try:
        resp = requests.get(config.GRAPHHOPPER_ROUTE_URL, params=params, timeout=20)
    except requests.RequestException as exc:
        raise RoutingError(f"GraphHopper request failed: {exc}") from exc

    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code != 200:
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
    }


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
    return {"points": points, "distance_m": total, "duration_s": total / (50 / 3.6), "profile": None}
