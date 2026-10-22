"""
Geo helpers: distance, bearing, moving along a route, speed and timestamps.

A route is a list of points [lat, lon] or [lat, lon, elevation].
"""
import math
import random
import time

EARTH_RADIUS_M = 6_371_000


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial compass bearing from point 1 to point 2 (0 = north, 90 = east)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def cumulative_distances(points):
    """[0, d(p0,p1), d(p0,p2), ...] - distance from the start to each point."""
    result = [0.0]
    for a, b in zip(points, points[1:]):
        result.append(result[-1] + haversine_m(a[0], a[1], b[0], b[1]))
    return result


def position_at(points, cum_dist, distance_m):
    """
    Interpolate the position `distance_m` meters along the route.

    Returns a dict with lat, lon, altitude (m) and bearing (deg).
    """
    if len(points) == 1:
        p = points[0]
        return {"lat": p[0], "lon": p[1], "altitude": _ele(p), "bearing": 0.0}

    distance_m = max(0.0, min(distance_m, cum_dist[-1]))

    # Find the segment [i, i+1] that contains distance_m (linear scan = simple + fast enough).
    i = 0
    while i < len(points) - 2 and cum_dist[i + 1] < distance_m:
        i += 1

    a, b = points[i], points[i + 1]
    seg_len = cum_dist[i + 1] - cum_dist[i]
    t = 0.0 if seg_len == 0 else (distance_m - cum_dist[i]) / seg_len

    return {
        "lat": a[0] + (b[0] - a[0]) * t,
        "lon": a[1] + (b[1] - a[1]) * t,
        "altitude": _ele(a) + (_ele(b) - _ele(a)) * t,
        "bearing": bearing_deg(a[0], a[1], b[0], b[1]),
    }


def vary_speed(avg_kmh, variation):
    """Random speed around the average, so the track looks more realistic."""
    return max(1.0, avg_kmh * random.uniform(1 - variation, 1 + variation))


def distance_for(speed_kmh, seconds):
    """Meters travelled at `speed_kmh` during `seconds`."""
    return speed_kmh / 3.6 * seconds


def now_ms():
    """Current UTC time as Unix epoch milliseconds (Teltonika timestamp format)."""
    return int(time.time() * 1000)


def _ele(point):
    return point[2] if len(point) > 2 and point[2] is not None else 0.0
