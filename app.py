"""
Teltonika GPS emulator - Flask application (REST API + single-page UI).

Run:  python app.py
"""
import re

from flask import Flask, jsonify, render_template, request

import config
import database
import models
from emulator import events, graphhopper
from emulator.engine import Emulator, reset_stale_statuses

app = Flask(__name__)
database.init_app(app)
reset_stale_statuses()
emulator = Emulator()


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


@app.errorhandler(ApiError)
def handle_api_error(err):
    return jsonify({"error": err.message}), err.status


@app.route("/")
def index():
    return render_template("index.html")


# --- Validation helpers -----------------------------------------------------

def _require(data, key):
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ApiError(f"Field '{key}' is required")
    return value.strip() if isinstance(value, str) else value


def _number(data, key, minimum, maximum, cast=float):
    try:
        value = cast(_require(data, key))
    except (TypeError, ValueError):
        raise ApiError(f"Field '{key}' must be a number")
    if not minimum <= value <= maximum:
        raise ApiError(f"Field '{key}' must be between {minimum} and {maximum}")
    return value


def _point(data, key):
    """A [lat, lon] pair."""
    value = _require(data, key)
    try:
        lat, lon = float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError):
        raise ApiError(f"Field '{key}' must be [lat, lon]")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ApiError(f"Field '{key}' is out of range")
    return [lat, lon]


def _parse_server(data):
    return {
        "name": _require(data, "name"),
        "host": _require(data, "host"),
        "port": _number(data, "port", 1, 65535, int),
    }


def _parse_device(db, data):
    imei = str(_require(data, "imei"))
    if not re.fullmatch(r"[0-9A-Za-z]{1,20}", imei):
        raise ApiError("IMEI / ID must be 1-20 letters or digits (usually 15 digits)")
    server_id = _number(data, "server_id", 1, 10**9, int)
    if models.get_server(db, server_id) is None:
        raise ApiError("Selected server does not exist")
    return {
        "name": _require(data, "name"),
        "imei": imei,
        "server_id": server_id,
        "interval_s": _number(data, "interval_s", 1, 3600, int),
        "speed_kmh": _number(data, "speed_kmh", 1, 300),
        "start": _point(data, "start"),
        "end": _point(data, "end"),
    }


def _route_for(data, start, end):
    """Use the route already generated in the UI (preview) or ask GraphHopper now."""
    route = data.get("route")
    if route and isinstance(route.get("points"), list) and len(route["points"]) >= 2:
        return {
            "points": route["points"],
            "distance_m": float(route.get("distance_m") or 0),
            "duration_s": float(route.get("duration_s") or 0),
        }
    try:
        return graphhopper.generate_route(start, end)
    except graphhopper.RoutingError as exc:
        raise ApiError(str(exc), 502)


# --- Servers ----------------------------------------------------------------

@app.get("/api/servers")
def servers_list():
    return jsonify(models.list_servers(database.get_db()))


@app.post("/api/servers")
def servers_create():
    db = database.get_db()
    server_id = models.create_server(db, _parse_server(request.get_json(force=True)))
    return jsonify(models.get_server(db, server_id)), 201


@app.get("/api/servers/<int:server_id>")
def servers_get(server_id):
    server = models.get_server(database.get_db(), server_id)
    if server is None:
        raise ApiError("Server not found", 404)
    return jsonify(server)


@app.put("/api/servers/<int:server_id>")
def servers_update(server_id):
    db = database.get_db()
    if models.get_server(db, server_id) is None:
        raise ApiError("Server not found", 404)
    models.update_server(db, server_id, _parse_server(request.get_json(force=True)))
    return jsonify(models.get_server(db, server_id))


@app.delete("/api/servers/<int:server_id>")
def servers_delete(server_id):
    db = database.get_db()
    if models.server_in_use(db, server_id):
        raise ApiError("Server is used by at least one device", 409)
    models.delete_server(db, server_id)
    return "", 204


# --- Devices ----------------------------------------------------------------

@app.get("/api/devices")
def devices_list():
    # Without route points - this endpoint is polled by the UI every few seconds.
    return jsonify(models.list_devices(database.get_db()))


@app.get("/api/devices/<int:device_id>")
def devices_get(device_id):
    device = models.get_device(database.get_db(), device_id)
    if device is None:
        raise ApiError("Device not found", 404)
    return jsonify(device)


@app.post("/api/devices")
def devices_create():
    db = database.get_db()
    data = request.get_json(force=True)
    device = _parse_device(db, data)
    if models.imei_taken(db, device["imei"]):
        raise ApiError("A device with this IMEI already exists", 409)

    route = _route_for(data, device["start"], device["end"])
    device_id = models.create_device(db, device)
    models.save_route(db, device_id, device["start"], device["end"], route)
    db.commit()
    return jsonify(models.get_device(db, device_id)), 201


@app.put("/api/devices/<int:device_id>")
def devices_update(device_id):
    db = database.get_db()
    existing = models.get_device(db, device_id, include_points=False)
    if existing is None:
        raise ApiError("Device not found", 404)
    data = request.get_json(force=True)
    device = _parse_device(db, data)
    if models.imei_taken(db, device["imei"], exclude_id=device_id):
        raise ApiError("A device with this IMEI already exists", 409)

    # A running worker uses the old settings -> stop it, save, restart it.
    was_running = emulator.is_running(device_id)
    if was_running:
        emulator.stop(device_id)

    models.update_device(db, device_id, device)
    old = existing["route"]
    route_changed = old is None or old["start"] != device["start"] or old["end"] != device["end"]
    if route_changed or data.get("route"):
        models.save_route(db, device_id, device["start"], device["end"],
                          _route_for(data, device["start"], device["end"]))
    db.commit()

    if was_running and not route_changed:
        emulator.start(device_id)
    return jsonify(models.get_device(db, device_id))


@app.delete("/api/devices/<int:device_id>")
def devices_delete(device_id):
    emulator.stop(device_id)
    models.delete_device(database.get_db(), device_id)
    return "", 204


# --- Routes -----------------------------------------------------------------

@app.post("/api/routes")
def routes_generate():
    """Generate (preview) a route between two points - not saved yet."""
    data = request.get_json(force=True)
    start, end = _point(data, "start"), _point(data, "end")
    try:
        return jsonify(graphhopper.generate_route(start, end))
    except graphhopper.RoutingError as exc:
        raise ApiError(str(exc), 502)


@app.get("/api/routes/<int:route_id>")
def routes_get(route_id):
    route = models.get_route(database.get_db(), route_id)
    if route is None:
        raise ApiError("Route not found", 404)
    return jsonify(route)


# --- Emulator ---------------------------------------------------------------
# Body: {"device_id": 1}  -> single device
#       {"all": true}     -> all devices

@app.post("/api/emulator/start")
def emulator_start():
    db = database.get_db()
    data = request.get_json(silent=True) or {}
    if data.get("all"):
        ids = [r["id"] for r in db.execute(
            "SELECT d.id FROM devices d JOIN routes r ON r.device_id = d.id WHERE d.server_id IS NOT NULL")]
        started = emulator.start_all(ids)
    else:
        device_id = _number(data, "device_id", 1, 10**9, int)
        if models.get_device(db, device_id, include_points=False) is None:
            raise ApiError("Device not found", 404)
        started = [device_id] if emulator.start(device_id) else []
    return jsonify({"started": started})


@app.post("/api/emulator/stop")
def emulator_stop():
    data = request.get_json(silent=True) or {}
    if data.get("all"):
        emulator.stop_all()
    else:
        emulator.stop(_number(data, "device_id", 1, 10**9, int))
    return jsonify({"ok": True})


@app.get("/api/emulator/events")
def emulator_events():
    """Events that can be triggered (for the "Event" menu in the UI)."""
    return jsonify(events.catalog())


@app.post("/api/emulator/event")
def emulator_event():
    """Body: {"device_id": 1, "type": "crash"} - send an event record right away."""
    data = request.get_json(force=True)
    device_id = _number(data, "device_id", 1, 10**9, int)
    event_type = data.get("type")
    if event_type not in events.EVENTS:
        raise ApiError(f"Unknown event type '{event_type}'")
    if not emulator.trigger(device_id, event_type):
        raise ApiError("Device is not running - start it first", 409)
    return jsonify({"ok": True})


@app.get("/api/emulator/status")
def emulator_status():
    return jsonify({"running": [d for d in emulator.workers if emulator.is_running(d)]})


if __name__ == "__main__":
    # threaded=True: API calls are not blocked while emulator threads run.
    # use_reloader=False: the reloader would start a second process with its own emulator.
    print(f"Emulator UI: http://localhost:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, threaded=True, debug=False, use_reloader=False)
