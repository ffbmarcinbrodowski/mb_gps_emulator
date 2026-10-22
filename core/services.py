"""
Use cases shared by the web app and the desktop app: validation + create/update/delete
of servers and devices. Both UIs call these functions, so the rules are the same everywhere.

Errors are raised as ServiceError(message, status) - the web app turns them into
HTTP responses, the desktop app shows them in a message box.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config, graphhopper, history, models, scenarios
from .imei import generate_imei


class ServiceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# --- Validation helpers -----------------------------------------------------

def _require(data, key):
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ServiceError(f"Field '{key}' is required")
    return value.strip() if isinstance(value, str) else value


def _number(data, key, minimum, maximum, cast=float):
    try:
        value = cast(_require(data, key))
    except (TypeError, ValueError):
        raise ServiceError(f"Field '{key}' must be a number")
    if not minimum <= value <= maximum:
        raise ServiceError(f"Field '{key}' must be between {minimum} and {maximum}")
    return value


def _point(data, key):
    """A [lat, lon] pair."""
    value = _require(data, key)
    try:
        lat, lon = float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError):
        raise ServiceError(f"Field '{key}' must be [lat, lon]")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ServiceError(f"Field '{key}' is out of range")
    return [lat, lon]


def parse_id(data, key):
    return _number(data, key, 1, 10**9, int)


def parse_server(data):
    return {
        "name": _require(data, "name"),
        "host": _require(data, "host"),
        "port": _number(data, "port", 1, 65535, int),
    }


def parse_device(db, data):
    imei = str(_require(data, "imei"))
    if not re.fullmatch(r"[0-9A-Za-z]{1,20}", imei):
        raise ServiceError("IMEI / ID must be 1-20 letters or digits (usually 15 digits)")
    server_id = _number(data, "server_id", 1, 10**9, int)
    if models.get_server(db, server_id) is None:
        raise ServiceError("Selected server does not exist")
    return {
        "name": _require(data, "name"),
        "imei": imei,
        "server_id": server_id,
        "interval_s": _number(data, "interval_s", 1, 3600, int),
        "speed_kmh": _number(data, "speed_kmh", 1, 300),
        # Realistic driving (road speeds, acceleration, stops, GPS noise) unless switched off.
        "realistic": bool(data.get("realistic", True)),
        "scenario": parse_scenario(data.get("scenario")),
        "start": _point(data, "start"),
        "end": _point(data, "end"),
    }


def parse_scenario(value):
    """Validate a scenario (see core/scenarios.py). None/empty = manual (drive once)."""
    if not value:
        return None
    if not isinstance(value, dict) or value.get("type") not in scenarios.TYPES:
        raise ServiceError("Unknown scenario type")
    kind = value["type"]

    days = value.get("days", scenarios.ALL_DAYS)
    if not isinstance(days, list) or not days or any(d not in range(7) for d in days):
        raise ServiceError("Select at least one day")
    cfg = {"type": kind, "days": sorted(set(days))}

    def hhmm(text, allow_24=False):
        """'7:30' -> '07:30'"""
        text = str(text).strip()
        try:
            h, m = scenarios.parse_hhmm(text, allow_24)
        except (ValueError, TypeError):
            raise ServiceError(f"'{text}' is not a valid time (HH:MM)")
        return f"{h:02d}:{m:02d}"

    if kind == "times":
        times = value.get("times") or []
        if isinstance(times, str):
            times = [t for t in re.split(r"[,;\s]+", times) if t]
        if not times:
            raise ServiceError("Enter at least one departure time, e.g. 07:30, 16:30")
        cfg["times"] = sorted({hhmm(t) for t in times})
    else:
        cfg["from"] = hhmm(value.get("from") or "00:00")
        cfg["to"] = hhmm(value.get("to") or "24:00", allow_24=True)
        if cfg["from"] >= cfg["to"]:
            raise ServiceError("'From' must be earlier than 'to'")
        cfg["dwell_min"] = _number(value, "dwell_min", 0, 720, int) if value.get("dwell_min") is not None else 5
    if kind == "interval":
        cfg["every_min"] = _number(value, "every_min", 5, 1440, int)
        cfg["round_trip"] = bool(value.get("round_trip"))
    if kind in ("times", "interval"):
        cfg["jitter_min"] = _number(value, "jitter_min", 0, 60, int) if value.get("jitter_min") is not None else 0
    return cfg


def scenario_preview(data):
    """Upcoming departures of a scenario for the UI (data: {scenario, duration_s})."""
    cfg = parse_scenario(data.get("scenario"))
    if cfg is None:
        return {"timezone": config.TIMEZONE, "scenario": None, "trips": [], "warnings": []}
    now = datetime.now(ZoneInfo(config.TIMEZONE))
    leg_s = scenarios.leg_seconds(float(data.get("duration_s") or 0))
    trips, warnings = scenarios.preview(cfg, now, leg_s, seed=data.get("device_id") or 0)
    return {
        "timezone": config.TIMEZONE,
        "scenario": cfg,   # validated and normalized (e.g. times as a sorted list)
        "trips": [{"at": f"{scenarios.DAY_NAMES[t.weekday()]} {t:%d.%m. %H:%M}", "legs": legs} for t, legs in trips],
        "warnings": warnings,
    }


# --- Routes -----------------------------------------------------------------

def generate_route(start, end):
    """Route preview between two [lat, lon] points (GraphHopper)."""
    start = _point({"start": start}, "start")
    end = _point({"end": end}, "end")
    try:
        return graphhopper.generate_route(start, end)
    except graphhopper.RoutingError as exc:
        raise ServiceError(str(exc), 502)


def _route_for(data, start, end):
    """Use the route already generated in the UI (preview) or ask GraphHopper now."""
    route = data.get("route")
    if route and isinstance(route.get("points"), list) and len(route["points"]) >= 2:
        return {
            "points": route["points"],
            "distance_m": float(route.get("distance_m") or 0),
            "duration_s": float(route.get("duration_s") or 0),
            "profile": route.get("profile") if isinstance(route.get("profile"), dict) else None,
        }
    return generate_route(start, end)


# --- Servers ----------------------------------------------------------------

def create_server(db, data):
    server_id = models.create_server(db, parse_server(data))
    return models.get_server(db, server_id)


def update_server(db, server_id, data):
    if models.get_server(db, server_id) is None:
        raise ServiceError("Server not found", 404)
    models.update_server(db, server_id, parse_server(data))
    return models.get_server(db, server_id)


def delete_server(db, server_id):
    if models.server_in_use(db, server_id):
        raise ServiceError("Server is used by at least one device", 409)
    models.delete_server(db, server_id)


# --- Devices ----------------------------------------------------------------

def create_device(db, data):
    device = parse_device(db, data)
    if models.imei_taken(db, device["imei"]):
        raise ServiceError("A device with this IMEI already exists", 409)

    route = _route_for(data, device["start"], device["end"])
    # Scenarios drive back and forth: B -> A gets its own route (one-way streets differ).
    return_route = generate_route(device["end"], device["start"]) if device["scenario"] else None
    device_id = models.create_device(db, device)
    models.save_route(db, device_id, device["start"], device["end"], route, return_route)
    db.commit()
    return models.get_device(db, device_id)


def update_device(db, emulator, device_id, data):
    existing = models.get_device(db, device_id, include_points=False)
    if existing is None:
        raise ServiceError("Device not found", 404)
    device = parse_device(db, data)
    if models.imei_taken(db, device["imei"], exclude_id=device_id):
        raise ServiceError("A device with this IMEI already exists", 409)

    old = existing["route"]
    route_changed = old is None or old["start"] != device["start"] or old["end"] != device["end"]
    # Routes created before realistic driving have no road speed profile -> fetch it once.
    needs_profile = device["realistic"] and old is not None and not old["has_profile"]
    # Generate a new route before touching anything, so a routing error changes nothing.
    new_route = None
    if route_changed or needs_profile or data.get("route"):
        new_route = _route_for(data, device["start"], device["end"])
    needs_return = device["scenario"] and (new_route is not None or not old["has_return"])
    return_route = generate_route(device["end"], device["start"]) if needs_return else None

    # A running worker uses the old settings -> stop it, save, restart it.
    was_running = emulator.is_running(device_id)
    if was_running:
        emulator.stop(device_id)

    models.update_device(db, device_id, device)
    if new_route:
        models.save_route(db, device_id, device["start"], device["end"], new_route, return_route)
    elif return_route:
        models.set_return_route(db, device_id, return_route)
    if not device["scenario"]:
        models.set_armed(db, device_id, False)
    db.commit()

    if was_running and not route_changed:
        emulator.start(device_id)
    return models.get_device(db, device_id)


def delete_device(db, emulator, device_id):
    emulator.stop(device_id)
    models.delete_device(db, device_id)


def new_imei(db):
    """A valid, unused test IMEI for a new device (see core/imei.py)."""
    taken = {row["imei"] for row in db.execute("SELECT imei FROM devices")}
    return generate_imei(taken)


def start_history(db, emulator, device_id, days):
    """Generate `days` of past data for a scenario device (see core/history.py)."""
    device = models.get_device(db, device_id, include_points=False)
    if device is None:
        raise ServiceError("Device not found", 404)
    if not device["scenario"]:
        raise ServiceError("History follows the device's scenario - choose a scenario first "
                           "(e.g. Fixed times 07:30, 16:30).")
    if not device["route"] or not device["server"]:
        raise ServiceError("The device needs a route and a server")
    if emulator.is_running(device_id):
        raise ServiceError("Stop the device first - live data and history must not mix.", 409)
    if emulator.history_running(device_id):
        raise ServiceError("History is already being generated for this device", 409)
    days = _number({"days": days}, "days", 1, history.MAX_DAYS, int)
    emulator.start_history(device_id, days)
    return emulator.history_status(device_id)


def armed_device_ids(db):
    """Scenario devices that were running before the app stopped - started again on startup."""
    return [r["id"] for r in db.execute(
        "SELECT id FROM devices WHERE scenario_enabled = 1 AND scenario IS NOT NULL")]


def startable_device_ids(db):
    """Devices that have a route and a server (used by "Start all")."""
    return [r["id"] for r in db.execute(
        "SELECT d.id FROM devices d JOIN routes r ON r.device_id = d.id WHERE d.server_id IS NOT NULL")]
