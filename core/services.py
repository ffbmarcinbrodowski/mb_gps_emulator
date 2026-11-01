"""
Use cases shared by the web app and the desktop app: validation + create/update/delete
of servers and devices. Both UIs call these functions, so the rules are the same everywhere.

Errors are raised as ServiceError(message, status) - the web app turns them into
HTTP responses, the desktop app shows them in a message box.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config, events, graphhopper, history, models, obd, scenarios, traccar_api
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


def parse_server(data, stored_password=None):
    """
    A server; api_* = optional access to its Traccar REST API. An empty password keeps
    the stored one (the UI never gets the password back).
    """
    server = {
        "name": _require(data, "name"),
        "host": _require(data, "host"),
        "port": _number(data, "port", 1, 65535, int),
    }
    server.update(parse_api_access(data, stored_password))
    return server


def parse_api_access(data, stored_password=None):
    url = str(data.get("api_url") or "").strip().rstrip("/")
    if not url:
        return {"api_url": None, "api_user": None, "api_password": None}
    if not re.match(r"https?://[^\s/]+", url):
        raise ServiceError("Traccar API address must start with http:// or https://, e.g. http://10.40.1.114:8082")
    user = str(data.get("api_user") or "").strip()
    if not user:
        raise ServiceError("Enter the Traccar login (e-mail) for the API access")
    password = data.get("api_password") or stored_password
    if not password:
        raise ServiceError("Enter the Traccar password for the API access")
    return {"api_url": url, "api_user": user, "api_password": password}


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
        # Only present when the OBD settings were edited (see models.update_device).
        "obd": parse_obd(data.get("obd")),
        "start": _point(data, "start"),
        "end": _point(data, "end"),
        "stops": parse_stops(data.get("stops")),
    }


MAX_STOPS = 50       # a sanity limit; the public GraphHopper API gets them in parts


def parse_stops(value):
    """
    Stops between start and end, in driving order: [{"point": [lat, lon], "dwell_min": 5}, ...].
    dwell_min = how long the vehicle waits there (engine running); 0 = it only drives through.
    """
    if not value:
        return []
    if not isinstance(value, list):
        raise ServiceError("Invalid stops")
    if len(value) > MAX_STOPS:
        raise ServiceError(f"At most {MAX_STOPS} stops")
    stops = []
    for n, stop in enumerate(value, 1):
        if not isinstance(stop, dict):
            raise ServiceError("Invalid stops")
        point = _point({f"stop {n}": stop.get("point")}, f"stop {n}")
        dwell = stop.get("dwell_min") if stop.get("dwell_min") not in (None, "") else 0
        stops.append({"point": point, "dwell_min": _number({f"stop {n} waiting time": dwell},
                                                           f"stop {n} waiting time", 0, 720, int)})
    return stops


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


def parse_obd(value):
    """Validate codec + OBD settings. None = not edited."""
    if not value:
        return None
    if not isinstance(value, dict):
        raise ServiceError("Invalid OBD settings")
    enabled = bool(value.get("enabled"))
    codec = value.get("codec") or "8"
    if codec not in ("8", "8E"):
        raise ServiceError("Codec must be 8 or 8E")
    if enabled:
        codec = "8E"     # OBD ids above 255, VIN and fault codes need Codec 8 Extended

    vin = str(value.get("vin") or "").strip().upper()
    if enabled and not obd.VIN_PATTERN.match(vin):
        raise ServiceError("VIN must have 17 characters (letters and digits, no I, O or Q)")

    dtcs = value.get("dtcs") or []
    if isinstance(dtcs, str):
        dtcs = [c for c in re.split(r"[,;\s]+", dtcs) if c]
    dtcs = [str(c).strip().upper() for c in dtcs]
    bad = [c for c in dtcs if not obd.DTC_PATTERN.match(c)]
    if bad:
        raise ServiceError(f"Invalid fault code {bad[0]} (format like P0420)")
    if len(dtcs) > 10:
        raise ServiceError("At most 10 fault codes")

    return {
        "enabled": enabled,
        "codec": codec,
        "vin": vin,
        "odometer_km": _number(value, "odometer_km", 0, 2_000_000),
        "service_km": _number(value, "service_km", 0, 100_000),
        "fuel_pct": _number(value, "fuel_pct", 0, 100),
        "tank_l": _number(value, "tank_l", 20, 200) if value.get("tank_l") is not None else 55,
        "dtcs": list(dict.fromkeys(dtcs)),
        "weak_battery": bool(value.get("weak_battery")),
    }


def check_event(db, device_id, event_type):
    """Can this event be sent from this device? (OBD events need OBD enabled.)"""
    if event_type not in events.EVENTS:
        raise ServiceError(f"Unknown event type '{event_type}'")
    if events.EVENTS[event_type].get("needs_obd"):
        device = models.get_device(db, device_id, include_points=False)
        if device is None or not device["obd"]["enabled"]:
            raise ServiceError("Enable OBD for this device first", 409)


def new_vin():
    """A realistic random VIN for the "Generate" button."""
    return obd.generate_vin()


def scenario_preview(data):
    """Upcoming departures of a scenario for the UI (data: {scenario, duration_s, stops_s})."""
    cfg = parse_scenario(data.get("scenario"))
    if cfg is None:
        return {"timezone": config.TIMEZONE, "scenario": None, "trips": [], "warnings": []}
    now = datetime.now(ZoneInfo(config.TIMEZONE))
    # stops_s: waiting time at the stops of the route, part of every A -> B drive
    leg_s = scenarios.leg_seconds(float(data.get("duration_s") or 0), float(data.get("stops_s") or 0))
    trips, warnings = scenarios.preview(cfg, now, leg_s, seed=data.get("device_id") or 0)
    return {
        "timezone": config.TIMEZONE,
        "scenario": cfg,   # validated and normalized (e.g. times as a sorted list)
        "trips": [{"at": f"{scenarios.DAY_NAMES[t.weekday()]} {t:%d.%m. %H:%M}", "legs": legs} for t, legs in trips],
        "warnings": warnings,
    }


# --- Routes -----------------------------------------------------------------

def generate_route(start, end, via=()):
    """Route preview between two [lat, lon] points, through the `via` points in between (GraphHopper)."""
    start = _point({"start": start}, "start")
    end = _point({"end": end}, "end")
    via = [_point({"stop": p}, "stop") for p in via or []]
    if len(via) > MAX_STOPS:
        raise ServiceError(f"At most {MAX_STOPS} stops")
    try:
        return graphhopper.generate_route(start, end, via=via)
    except graphhopper.RoutingError as exc:
        raise ServiceError(str(exc), 502)


def _route_for(data, start, end, stops):
    """Use the route already generated in the UI (preview) or ask GraphHopper now."""
    route = data.get("route")
    if route and isinstance(route.get("points"), list) and len(route["points"]) >= 2:
        indices = route.get("stop_indices") or []
        # A preview made for other stops (or by an older UI) can't say where the stops are.
        if (len(indices) == len(stops)
                and all(isinstance(i, int) and 0 <= i < len(route["points"]) for i in indices)):
            return {
                "points": route["points"],
                "distance_m": float(route.get("distance_m") or 0),
                "duration_s": float(route.get("duration_s") or 0),
                "profile": route.get("profile") if isinstance(route.get("profile"), dict) else None,
                "stop_indices": indices,
            }
    return generate_route(start, end, [s["point"] for s in stops])


# --- Servers ----------------------------------------------------------------

def create_server(db, data):
    server_id = models.create_server(db, parse_server(data))
    return models.get_server(db, server_id)


def update_server(db, server_id, data):
    if models.get_server(db, server_id) is None:
        raise ServiceError("Server not found", 404)
    models.update_server(db, server_id, parse_server(data, _stored_password(db, server_id)))
    return models.get_server(db, server_id)


def _stored_password(db, server_id):
    access = models.server_api_access(db, server_id) if server_id else None
    return access[2] if access else None


def test_traccar_api(db, data):
    """
    Check the Traccar API access entered in the servers dialog (before or after saving).
    data: {api_url, api_user, api_password, server_id} - an empty password = the stored one.
    """
    server_id = data.get("server_id")
    access = parse_api_access(data, _stored_password(db, int(server_id)) if server_id else None)
    if not access["api_url"]:
        raise ServiceError("Enter the Traccar API address, e.g. http://10.40.1.114:8082")
    try:
        version = traccar_api.TraccarApi(access["api_url"], access["api_user"], access["api_password"]).version()
    except traccar_api.TraccarError as exc:
        raise ServiceError(str(exc), 502)
    return {"version": version}


def add_to_traccar(db, device_id):
    """
    Create the device in Traccar (name, IMEI, model) through the server's API - or link the
    device that already exists there with this IMEI. Nothing in Traccar is changed or deleted.
    Returns {"result": "created" / "exists", "traccar_id", "model", "message"}.
    """
    device = models.get_device(db, device_id, include_points=False)
    if device is None:
        raise ServiceError("Device not found", 404)
    access = models.server_api_access(db, device["server_id"]) if device["server_id"] else None
    if access is None:
        raise ServiceError("Set up the Traccar API access of the server first (Servers)", 409)
    model = traccar_api.device_model(device["obd"]["enabled"])
    try:
        found, created = traccar_api.TraccarApi(*access).ensure_device(device["name"], device["imei"], model)
    except traccar_api.TraccarError as exc:
        raise ServiceError(str(exc), 502)
    models.set_traccar_id(db, device_id, found["id"])
    if created:
        message = f'Created in Traccar as "{found["name"]}" (model {model})'
    else:
        message = f'Already in Traccar as "{found["name"]}" (model {found.get("model") or "not set"}) - linked, not changed'
    return {"result": "created" if created else "exists", "traccar_id": found["id"],
            "model": found.get("model"), "message": message}


def delete_server(db, server_id):
    if models.server_in_use(db, server_id):
        raise ServiceError("Server is used by at least one device", 409)
    models.delete_server(db, server_id)


# --- Devices ----------------------------------------------------------------

def create_device(db, data):
    device = parse_device(db, data)
    if models.imei_taken(db, device["imei"]):
        raise ServiceError("A device with this IMEI already exists", 409)

    route = _route_for(data, device["start"], device["end"], device["stops"])
    # Scenarios drive back and forth: B -> A gets its own route (one-way streets differ).
    # It goes straight back, without the stops.
    return_route = generate_route(device["end"], device["start"]) if device["scenario"] else None
    device_id = models.create_device(db, device)
    models.save_route(db, device_id, device["start"], device["end"], route, return_route, device["stops"])
    db.commit()

    # "Create in Traccar": the device is saved anyway - a Traccar error is only reported.
    traccar = None
    if data.get("traccar"):
        try:
            traccar = add_to_traccar(db, device_id)
        except ServiceError as exc:
            traccar = {"result": "error", "message": exc.message}
    result = models.get_device(db, device_id)
    if traccar:
        result["traccar_result"] = traccar
    return result


def update_device(db, emulator, device_id, data):
    existing = models.get_device(db, device_id, include_points=False)
    if existing is None:
        raise ServiceError("Device not found", 404)
    device = parse_device(db, data)
    if models.imei_taken(db, device["imei"], exclude_id=device_id):
        raise ServiceError("A device with this IMEI already exists", 409)

    old = existing["route"]
    stop_points = [s["point"] for s in device["stops"]]
    route_changed = (old is None or old["start"] != device["start"] or old["end"] != device["end"]
                     or [s["point"] for s in old["stops"]] != stop_points)
    # Routes created before realistic driving have no road speed profile -> fetch it once.
    needs_profile = device["realistic"] and old is not None and not old["has_profile"]
    # Generate a new route before touching anything, so a routing error changes nothing.
    new_route = None
    if route_changed or needs_profile or data.get("route"):
        new_route = _route_for(data, device["start"], device["end"], device["stops"])
    needs_return = device["scenario"] and (new_route is not None or not old["has_return"])
    return_route = generate_route(device["end"], device["start"]) if needs_return else None

    # A running worker uses the old settings -> stop it, save, restart it.
    was_running = emulator.is_running(device_id)
    if was_running:
        emulator.stop(device_id)

    models.update_device(db, device_id, device)
    if new_route:
        models.save_route(db, device_id, device["start"], device["end"], new_route, return_route,
                          device["stops"])
    else:
        # Same stops, maybe other waiting times - the route itself stays.
        models.set_stop_dwell(db, device_id, device["stops"])
        if return_route:
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
