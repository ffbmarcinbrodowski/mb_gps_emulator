"""
Data access for the three tables: servers, devices, routes.

Plain SQL + small functions that turn rows into JSON-friendly dicts.
"""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config


def local_time(utc_text):
    """
    '2026-10-22 21:15:00' (UTC, as stored) -> '23:15:00' in the app time zone,
    with the date in front when it is not today: '21.10. 07:31:12'.
    """
    if not utc_text:
        return None
    tz = ZoneInfo(config.TIMEZONE)
    moment = datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(tz)
    if moment.date() == datetime.now(tz).date():
        return f"{moment:%H:%M:%S}"
    return f"{moment:%d.%m. %H:%M:%S}"


# --- Servers ----------------------------------------------------------------

def server_to_dict(row):
    return {
        "id": row["id"], "name": row["name"], "host": row["host"], "port": row["port"],
        # Traccar REST API access (optional); the password itself never leaves the core.
        "api_url": row["api_url"] or "",
        "api_user": row["api_user"] or "",
        "api_password_set": bool(row["api_password"]),
        "has_api": bool(row["api_url"]),
    }


def server_api_access(db, server_id):
    """(url, user, password) of the server's Traccar API, or None if not configured."""
    row = db.execute("SELECT api_url, api_user, api_password FROM servers WHERE id = ?", (server_id,)).fetchone()
    return (row["api_url"], row["api_user"], row["api_password"]) if row and row["api_url"] else None


def list_servers(db):
    return [server_to_dict(r) for r in db.execute("SELECT * FROM servers ORDER BY name")]


def get_server(db, server_id):
    row = db.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
    return server_to_dict(row) if row else None


def create_server(db, data):
    cur = db.execute(
        "INSERT INTO servers (name, host, port, api_url, api_user, api_password) VALUES (?, ?, ?, ?, ?, ?)",
        (data["name"], data["host"], data["port"], data.get("api_url"), data.get("api_user"), data.get("api_password")),
    )
    db.commit()
    return cur.lastrowid


def update_server(db, server_id, data):
    db.execute(
        "UPDATE servers SET name = ?, host = ?, port = ?, api_url = ?, api_user = ?, api_password = ? WHERE id = ?",
        (data["name"], data["host"], data["port"], data.get("api_url"), data.get("api_user"), data.get("api_password"),
         server_id),
    )
    db.commit()


def delete_server(db, server_id):
    db.execute("DELETE FROM servers WHERE id = ?", (server_id,))
    db.commit()


def server_in_use(db, server_id):
    return db.execute("SELECT 1 FROM devices WHERE server_id = ?", (server_id,)).fetchone() is not None


# --- Routes -----------------------------------------------------------------

def route_to_dict(row, include_points=False):
    result = {
        "id": row["id"],
        "start": [row["start_lat"], row["start_lon"]],
        "end": [row["end_lat"], row["end_lon"]],
        "distance_m": row["distance_m"],
        "duration_s": row["duration_s"],
        "status": row["status"],
        "has_profile": row["profile"] is not None,   # road speed profile (realistic driving)
        "has_return": row["return_points"] is not None,   # return route B -> A (scenarios)
        "return_distance_m": row["return_distance_m"],
        # Stops between start and end: [{"point": [lat, lon], "dwell_min": 5}, ...]
        "stops": [{"point": [s["lat"], s["lon"]], "dwell_min": s["dwell_min"]} for s in _stops(row)],
    }
    if include_points:
        result["points"] = json.loads(row["points"])
        result["return_points"] = json.loads(row["return_points"]) if row["return_points"] else None
    return result


def get_route(db, route_id, include_points=True):
    row = db.execute("SELECT * FROM routes WHERE id = ?", (route_id,)).fetchone()
    return route_to_dict(row, include_points) if row else None


def _stops(route_row):
    return json.loads(route_row["stops"]) if route_row["stops"] else []


def _stops_json(stops, indices):
    """Stops [{"point", "dwell_min"}] + their route point indices -> JSON for routes.stops."""
    if not stops:
        return None
    return json.dumps([{"lat": s["point"][0], "lon": s["point"][1], "dwell_min": s["dwell_min"], "index": i}
                       for s, i in zip(stops, indices)])


def save_route(db, device_id, start, end, route, return_route=None, stops=()):
    """
    Insert or replace the route of a device (and its return route). Resets the device's progress.
    stops: [{"point": [lat, lon], "dwell_min": n}] in driving order; route["stop_indices"] says
    where on the route each of them lies.
    """
    db.execute("DELETE FROM routes WHERE device_id = ?", (device_id,))
    profile = route.get("profile")
    db.execute(
        "INSERT INTO routes (device_id, start_lat, start_lon, end_lat, end_lon, points, distance_m, duration_s,"
        " profile, stops) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (device_id, start[0], start[1], end[0], end[1],
         json.dumps(route["points"]), route["distance_m"], route["duration_s"],
         json.dumps(profile) if profile else None, _stops_json(stops, route.get("stop_indices") or [])),
    )
    if return_route:
        set_return_route(db, device_id, return_route)
    first = route["points"][0]
    db.execute(
        "UPDATE devices SET progress_m = 0, status = 'stopped', direction = 'forward', current_lat = ?,"
        " current_lon = ?, current_speed = 0, current_bearing = 0 WHERE id = ?",
        (first[0], first[1], device_id),
    )


def set_stop_dwell(db, device_id, stops):
    """New waiting times for the same stops (the route itself stays as it is)."""
    row = db.execute("SELECT stops FROM routes WHERE device_id = ?", (device_id,)).fetchone()
    indices = [s["index"] for s in _stops(row)]
    db.execute("UPDATE routes SET stops = ? WHERE device_id = ?", (_stops_json(stops, indices), device_id))


def route_leg(route_row, direction):
    """
    What the vehicle drives on A -> B ("forward") or B -> A ("return"):
    (points, road profile, stops [(route point index, seconds waiting)]).
    The return route goes straight back without stops.
    """
    if direction == "return":
        if route_row["return_points"]:
            profile = json.loads(route_row["return_profile"]) if route_row["return_profile"] else None
            return json.loads(route_row["return_points"]), profile, []
        return list(reversed(json.loads(route_row["points"]))), None, []   # old data without a return route
    profile = json.loads(route_row["profile"]) if route_row["profile"] else None
    stops = [(s["index"], s["dwell_min"] * 60) for s in _stops(route_row) if s["dwell_min"] > 0]
    return json.loads(route_row["points"]), profile, stops


def stops_seconds(route_row):
    """Total waiting time at the stops of A -> B (for the expected trip duration of scenarios)."""
    return sum(s["dwell_min"] * 60 for s in _stops(route_row))


def set_return_route(db, device_id, route):
    """Store the B -> A route next to the A -> B route."""
    profile = route.get("profile")
    db.execute(
        "UPDATE routes SET return_points = ?, return_distance_m = ?, return_duration_s = ?, return_profile = ?"
        " WHERE device_id = ?",
        (json.dumps(route["points"]), route["distance_m"], route["duration_s"],
         json.dumps(profile) if profile else None, device_id),
    )


# --- Devices ----------------------------------------------------------------

DEVICE_QUERY = """
    SELECT d.*, s.name AS server_name, s.host AS server_host, s.port AS server_port,
           s.api_url AS server_api_url
    FROM devices d
    LEFT JOIN servers s ON s.id = d.server_id
"""


def display_status(device_status, route_status, activity=None):
    """
    One status for the UI, combined from device status + route status:
        stopped + planned -> ready    (not started yet)
        running           -> running
        running + parked  -> parked   (scenario: waiting for the next departure)
        stopped + running -> paused   (stopped halfway, Start resumes)
        finished          -> finished (Start begins the route again)
    """
    if device_status == "running" and activity == "parked":
        return "parked"
    if device_status in ("running", "finished"):
        return device_status
    return "paused" if route_status == "running" else "ready"


def device_to_dict(db, row, include_points=False):
    route_row = db.execute("SELECT * FROM routes WHERE device_id = ?", (row["id"],)).fetchone()
    route = route_to_dict(route_row, include_points) if route_row else None
    # Progress of the current leg: A -> B uses the route, B -> A the return route.
    leg_m = route and (route["return_distance_m"] if row["direction"] == "return" else route["distance_m"])
    return {
        "id": row["id"],
        "name": row["name"],
        "imei": row["imei"],
        "server_id": row["server_id"],
        "server": {"id": row["server_id"], "name": row["server_name"],
                   "host": row["server_host"], "port": row["server_port"],
                   "has_api": bool(row["server_api_url"])} if row["server_id"] else None,
        "traccar_id": row["traccar_id"],      # device id in Traccar, once created there by the emulator
        "interval_s": row["interval_s"],
        "speed_kmh": row["speed_kmh"],
        "realistic": bool(row["realistic"]),
        "odometer_m": row["odometer_m"],
        "status": row["status"],
        "display_status": display_status(row["status"], route["status"] if route else None, row["activity"]),
        "scenario": json.loads(row["scenario"]) if row["scenario"] else None,
        "scenario_enabled": bool(row["scenario_enabled"]),
        "direction": row["direction"],
        "activity": row["activity"],
        "next_trip_at": row["next_trip_at"],
        "progress_m": row["progress_m"],
        "progress_pct": 100.0 if row["status"] == "finished" else
        min(100.0, round(100 * row["progress_m"] / leg_m, 1)) if leg_m else 0,
        "current": {
            "lat": row["current_lat"], "lon": row["current_lon"],
            "speed": row["current_speed"], "bearing": row["current_bearing"],
        } if row["current_lat"] is not None else None,
        "last_sent_at": row["last_sent_at"],              # UTC, as stored
        "last_sent_local": local_time(row["last_sent_at"]),  # for display (app time zone)
        "last_error": row["last_error"],
        "last_event": row["last_event"],
        "last_event_at": row["last_event_at"],
        "last_event_local": local_time(row["last_event_at"]),
        "codec": row["codec"],
        "obd": {
            "enabled": bool(row["obd_enabled"]),
            "vin": row["vin"] or "",
            "odometer_km": row["obd_odometer_km"],
            "service_km": row["obd_service_km"],       # below 0 = overdue (the device sends 0)
            "fuel_pct": row["obd_fuel_pct"],
            "tank_l": row["obd_tank_l"],
            "dtcs": [c for c in row["obd_dtcs"].split(",") if c],
            "mil_km": row["obd_mil_km"],
            "coolant_c": row["obd_coolant_c"],
            "weak_battery": bool(row["obd_weak_battery"]),
        },
        "route": route,
    }


def list_devices(db, search=""):
    """All devices by name; `search` keeps only those that match (see device_matches)."""
    rows = db.execute(DEVICE_QUERY + " ORDER BY d.name").fetchall()
    devices = [device_to_dict(db, r) for r in rows]
    return [d for d in devices if device_matches(d, search)] if search.strip() else devices


def device_matches(device, search):
    """
    Device search (same in both UIs): every word of `search` must appear, ignoring case, in
    the name, IMEI, VIN or server (name, host) of the device. "mb 78" finds "SB MB 7825".
    Empty text matches every device.
    """
    server = device["server"] or {}
    text = " ".join([device["name"], device["imei"], device["obd"]["vin"],
                     server.get("name") or "", server.get("host") or ""]).lower()
    return all(word in text for word in search.lower().split())


def get_device(db, device_id, include_points=True):
    row = db.execute(DEVICE_QUERY + " WHERE d.id = ?", (device_id,)).fetchone()
    return device_to_dict(db, row, include_points) if row else None


def imei_taken(db, imei, exclude_id=None):
    row = db.execute("SELECT id FROM devices WHERE imei = ?", (imei,)).fetchone()
    return row is not None and row["id"] != exclude_id


def create_device(db, data):
    cur = db.execute(
        "INSERT INTO devices (name, imei, server_id, interval_s, speed_kmh, realistic, scenario)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (data["name"], data["imei"], data["server_id"], data["interval_s"], data["speed_kmh"],
         int(data.get("realistic", True)), _scenario_json(data)),
    )
    if data.get("obd"):
        save_obd(db, cur.lastrowid, data["obd"])
    return cur.lastrowid


def update_device(db, device_id, data):
    db.execute(
        "UPDATE devices SET name = ?, imei = ?, server_id = ?, interval_s = ?, speed_kmh = ?, realistic = ?,"
        " scenario = ?, updated_at = datetime('now'),"
        # The link to the Traccar device only holds for the same server and IMEI.
        " traccar_id = CASE WHEN server_id = ? AND imei = ? THEN traccar_id END WHERE id = ?",
        (data["name"], data["imei"], data["server_id"], data["interval_s"], data["speed_kmh"],
         int(data["realistic"]), _scenario_json(data), data["server_id"], data["imei"], device_id),
    )
    # OBD values are only written when they were edited: a running device keeps changing them,
    # and an old copy from the dialog must not turn the mileage back.
    if data.get("obd"):
        save_obd(db, device_id, data["obd"])


def save_obd(db, device_id, obd):
    """Codec + OBD settings of a device (validated by services.parse_obd)."""
    db.execute(
        "UPDATE devices SET codec = ?, obd_enabled = ?, vin = ?, obd_odometer_km = ?, obd_service_km = ?,"
        " obd_fuel_pct = ?, obd_tank_l = ?, obd_dtcs = ?, obd_weak_battery = ?,"
        " obd_mil_km = CASE WHEN ? = '' THEN 0 ELSE obd_mil_km END WHERE id = ?",
        (obd["codec"], int(obd["enabled"]), obd["vin"] or None, obd["odometer_km"], obd["service_km"],
         obd["fuel_pct"], obd["tank_l"], ",".join(obd["dtcs"]), int(obd["weak_battery"]),
         ",".join(obd["dtcs"]), device_id),
    )


def _scenario_json(data):
    return json.dumps(data["scenario"]) if data.get("scenario") else None


def set_traccar_id(db, device_id, traccar_id):
    db.execute("UPDATE devices SET traccar_id = ? WHERE id = ?", (traccar_id, device_id))
    db.commit()


def set_armed(db, device_id, armed):
    """Armed scenario devices are started again automatically after a restart."""
    db.execute("UPDATE devices SET scenario_enabled = ? WHERE id = ?", (int(armed), device_id))
    db.commit()


def delete_device(db, device_id):
    db.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    db.commit()
