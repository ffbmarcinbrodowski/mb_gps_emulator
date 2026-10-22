"""
Teltonika GPS emulator - web app (REST API + single-page UI).

All logic lives in core/ (shared with the desktop app); this module only maps
HTTP requests to core.services and core.models.

Run:  python -m web
"""
from flask import Flask, g, jsonify, render_template, request

from core import config, database, events, models, services
from core.engine import Emulator, reset_stale_statuses

app = Flask(__name__)
database.run_migrations()
reset_stale_statuses()
emulator = Emulator()

# Scenario devices that were armed before a restart continue on their own.
_db = database.connect()
emulator.start_all(services.armed_device_ids(_db))
_db.close()


def get_db():
    """Database connection bound to the current request."""
    if "db" not in g:
        g.db = database.connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.errorhandler(services.ServiceError)
def handle_service_error(err):
    return jsonify({"error": err.message}), err.status


@app.route("/")
def index():
    return render_template("index.html")


# --- Servers ----------------------------------------------------------------

@app.get("/api/servers")
def servers_list():
    return jsonify(models.list_servers(get_db()))


@app.post("/api/servers")
def servers_create():
    return jsonify(services.create_server(get_db(), request.get_json(force=True))), 201


@app.get("/api/servers/<int:server_id>")
def servers_get(server_id):
    server = models.get_server(get_db(), server_id)
    if server is None:
        raise services.ServiceError("Server not found", 404)
    return jsonify(server)


@app.put("/api/servers/<int:server_id>")
def servers_update(server_id):
    return jsonify(services.update_server(get_db(), server_id, request.get_json(force=True)))


@app.delete("/api/servers/<int:server_id>")
def servers_delete(server_id):
    services.delete_server(get_db(), server_id)
    return "", 204


# --- Devices ----------------------------------------------------------------

@app.get("/api/devices")
def devices_list():
    # Without route points - this endpoint is polled by the UI every few seconds.
    return jsonify(models.list_devices(get_db()))


@app.get("/api/devices/new-imei")
def devices_new_imei():
    """A valid, unused test IMEI for the "Generate" button."""
    return jsonify({"imei": services.new_imei(get_db())})


@app.get("/api/devices/<int:device_id>")
def devices_get(device_id):
    device = models.get_device(get_db(), device_id)
    if device is None:
        raise services.ServiceError("Device not found", 404)
    return jsonify(device)


@app.post("/api/devices")
def devices_create():
    return jsonify(services.create_device(get_db(), request.get_json(force=True))), 201


@app.put("/api/devices/<int:device_id>")
def devices_update(device_id):
    return jsonify(services.update_device(get_db(), emulator, device_id, request.get_json(force=True)))


@app.delete("/api/devices/<int:device_id>")
def devices_delete(device_id):
    services.delete_device(get_db(), emulator, device_id)
    return "", 204


# --- Routes -----------------------------------------------------------------

@app.post("/api/routes")
def routes_generate():
    """Generate (preview) a route between two points - not saved yet."""
    data = request.get_json(force=True)
    return jsonify(services.generate_route(data.get("start"), data.get("end")))


@app.get("/api/routes/<int:route_id>")
def routes_get(route_id):
    route = models.get_route(get_db(), route_id)
    if route is None:
        raise services.ServiceError("Route not found", 404)
    return jsonify(route)


# --- Scenarios / settings --------------------------------------------------

@app.post("/api/scenarios/preview")
def scenarios_preview():
    """Body: {"scenario": {...}, "duration_s": 1290} -> upcoming departures + warnings."""
    return jsonify(services.scenario_preview(request.get_json(force=True)))


@app.get("/api/settings")
def settings_get():
    return jsonify({"timezone": config.TIMEZONE})


# --- Emulator ---------------------------------------------------------------
# Body: {"device_id": 1}  -> single device
#       {"all": true}     -> all devices

@app.post("/api/emulator/start")
def emulator_start():
    db = get_db()
    data = request.get_json(silent=True) or {}
    if data.get("all"):
        started = emulator.start_all(services.startable_device_ids(db))
    else:
        device_id = services.parse_id(data, "device_id")
        if models.get_device(db, device_id, include_points=False) is None:
            raise services.ServiceError("Device not found", 404)
        started = [device_id] if emulator.start(device_id) else []
    return jsonify({"started": started})


@app.post("/api/emulator/stop")
def emulator_stop():
    data = request.get_json(silent=True) or {}
    if data.get("all"):
        emulator.stop_all()
    else:
        emulator.stop(services.parse_id(data, "device_id"))
    return jsonify({"ok": True})


@app.get("/api/emulator/events")
def emulator_events():
    """Events that can be triggered (for the "Event" menu in the UI)."""
    return jsonify(events.catalog())


@app.post("/api/emulator/event")
def emulator_event():
    """Body: {"device_id": 1, "type": "crash"} - send an event record right away."""
    data = request.get_json(force=True)
    device_id = services.parse_id(data, "device_id")
    event_type = data.get("type")
    if event_type not in events.EVENTS:
        raise services.ServiceError(f"Unknown event type '{event_type}'")
    if not emulator.trigger(device_id, event_type):
        raise services.ServiceError("Device is not running - start it first", 409)
    return jsonify({"ok": True})


@app.get("/api/emulator/status")
def emulator_status():
    return jsonify({"running": [d for d in emulator.workers if emulator.is_running(d)]})


def main():
    # threaded=True: API calls are not blocked while emulator threads run.
    # use_reloader=False: the reloader would start a second process with its own emulator.
    print(f"Emulator UI: http://localhost:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, threaded=True, debug=False, use_reloader=False)
