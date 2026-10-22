"""
Data access for the three tables: servers, devices, routes.

Plain SQL + small functions that turn rows into JSON-friendly dicts.
"""
import json


# --- Servers ----------------------------------------------------------------

def server_to_dict(row):
    return {"id": row["id"], "name": row["name"], "host": row["host"], "port": row["port"]}


def list_servers(db):
    return [server_to_dict(r) for r in db.execute("SELECT * FROM servers ORDER BY name")]


def get_server(db, server_id):
    row = db.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
    return server_to_dict(row) if row else None


def create_server(db, data):
    cur = db.execute(
        "INSERT INTO servers (name, host, port) VALUES (?, ?, ?)",
        (data["name"], data["host"], data["port"]),
    )
    db.commit()
    return cur.lastrowid


def update_server(db, server_id, data):
    db.execute(
        "UPDATE servers SET name = ?, host = ?, port = ? WHERE id = ?",
        (data["name"], data["host"], data["port"], server_id),
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
    }
    if include_points:
        result["points"] = json.loads(row["points"])
    return result


def get_route(db, route_id, include_points=True):
    row = db.execute("SELECT * FROM routes WHERE id = ?", (route_id,)).fetchone()
    return route_to_dict(row, include_points) if row else None


def save_route(db, device_id, start, end, route):
    """Insert or replace the route of a device. Resets the device's progress."""
    db.execute("DELETE FROM routes WHERE device_id = ?", (device_id,))
    db.execute(
        "INSERT INTO routes (device_id, start_lat, start_lon, end_lat, end_lon, points, distance_m, duration_s)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (device_id, start[0], start[1], end[0], end[1],
         json.dumps(route["points"]), route["distance_m"], route["duration_s"]),
    )
    first = route["points"][0]
    db.execute(
        "UPDATE devices SET progress_m = 0, status = 'stopped', current_lat = ?, current_lon = ?,"
        " current_speed = 0, current_bearing = 0 WHERE id = ?",
        (first[0], first[1], device_id),
    )


# --- Devices ----------------------------------------------------------------

DEVICE_QUERY = """
    SELECT d.*, s.name AS server_name, s.host AS server_host, s.port AS server_port
    FROM devices d
    LEFT JOIN servers s ON s.id = d.server_id
"""


def device_to_dict(db, row, include_points=False):
    route_row = db.execute("SELECT * FROM routes WHERE device_id = ?", (row["id"],)).fetchone()
    route = route_to_dict(route_row, include_points) if route_row else None
    return {
        "id": row["id"],
        "name": row["name"],
        "imei": row["imei"],
        "server_id": row["server_id"],
        "server": {"id": row["server_id"], "name": row["server_name"],
                   "host": row["server_host"], "port": row["server_port"]} if row["server_id"] else None,
        "interval_s": row["interval_s"],
        "speed_kmh": row["speed_kmh"],
        "status": row["status"],
        "progress_m": row["progress_m"],
        "progress_pct": 100.0 if row["status"] == "finished" else
        min(100.0, round(100 * row["progress_m"] / route["distance_m"], 1))
        if route and route["distance_m"] else 0,
        "current": {
            "lat": row["current_lat"], "lon": row["current_lon"],
            "speed": row["current_speed"], "bearing": row["current_bearing"],
        } if row["current_lat"] is not None else None,
        "last_sent_at": row["last_sent_at"],
        "last_error": row["last_error"],
        "last_event": row["last_event"],
        "last_event_at": row["last_event_at"],
        "route": route,
    }


def list_devices(db):
    rows = db.execute(DEVICE_QUERY + " ORDER BY d.name").fetchall()
    return [device_to_dict(db, r) for r in rows]


def get_device(db, device_id, include_points=True):
    row = db.execute(DEVICE_QUERY + " WHERE d.id = ?", (device_id,)).fetchone()
    return device_to_dict(db, row, include_points) if row else None


def imei_taken(db, imei, exclude_id=None):
    row = db.execute("SELECT id FROM devices WHERE imei = ?", (imei,)).fetchone()
    return row is not None and row["id"] != exclude_id


def create_device(db, data):
    cur = db.execute(
        "INSERT INTO devices (name, imei, server_id, interval_s, speed_kmh) VALUES (?, ?, ?, ?, ?)",
        (data["name"], data["imei"], data["server_id"], data["interval_s"], data["speed_kmh"]),
    )
    return cur.lastrowid


def update_device(db, device_id, data):
    db.execute(
        "UPDATE devices SET name = ?, imei = ?, server_id = ?, interval_s = ?, speed_kmh = ?,"
        " updated_at = datetime('now') WHERE id = ?",
        (data["name"], data["imei"], data["server_id"], data["interval_s"], data["speed_kmh"], device_id),
    )


def delete_device(db, device_id):
    db.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    db.commit()
