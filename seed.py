"""
Seed the database with example servers and devices (around Saarbrücken).

    python seed.py          # only if the database is empty
    python seed.py --reset  # delete all devices/servers first

Routes are generated with GraphHopper; if that fails a straight line is used.
"""
import random
import sys

from core import database, graphhopper, models, obd

SERVERS = [
    {"name": "Local Traccar", "host": "127.0.0.1", "port": 5027},
    {"name": "Fake server (tools/fake_server.py)", "host": "127.0.0.1", "port": 5055},
    {"name": "Traccar demo", "host": "demo.traccar.org", "port": 5027},
]

DEVICES = [
    {"name": "Teltonika001", "imei": "356307042441001", "start": [49.2043, 6.9472], "end": [49.2425, 7.0445],
     "interval_s": 5, "speed_kmh": 130},
    {"name": "Teltonika002", "imei": "356307042441002", "start": [49.2766, 7.0365], "end": [49.2152, 7.0390],
     "interval_s": 5, "speed_kmh": 110},
    {"name": "Teltonika003", "imei": "356307042441003", "start": [49.2790, 7.1150], "end": [49.2160, 7.1080],
     "interval_s": 10, "speed_kmh": 90},   # e.g. a truck
]

# OBD-II values (Teltonika FMB003, Codec 8 Extended): one car nearly due for service,
# one with a stored fault code, one high-mileage vehicle with a big tank.
OBD = [
    {"odometer_km": 48_210, "service_km": 12_400, "fuel_pct": 64, "tank_l": 50, "dtcs": []},
    {"odometer_km": 131_870, "service_km": 350, "fuel_pct": 38, "tank_l": 55, "dtcs": ["P0420"]},
    {"odometer_km": 243_500, "service_km": 8_900, "fuel_pct": 81, "tank_l": 90, "dtcs": []},
]


def main():
    database.run_migrations()
    db = database.connect()

    if "--reset" in sys.argv:
        db.execute("DELETE FROM devices")
        db.execute("DELETE FROM servers")
        db.commit()
    elif db.execute("SELECT COUNT(*) FROM servers").fetchone()[0] > 0:
        print("Database already has data - use --reset to re-seed.")
        return

    server_ids = [models.create_server(db, s) for s in SERVERS]
    # Devices point to the fake server by default, so they work out of the box.
    default_server = server_ids[1]

    rng = random.Random(42)      # the same VINs on every seed
    for d, o in zip(DEVICES, OBD):
        try:
            route = graphhopper.generate_route(d["start"], d["end"])
            source = "GraphHopper"
        except graphhopper.RoutingError as exc:
            route = graphhopper.straight_line_route(d["start"], d["end"])
            source = f"straight line ({exc})"
        settings = {**o, "enabled": True, "codec": "8E", "vin": obd.generate_vin(rng), "weak_battery": False}
        device_id = models.create_device(db, {**d, "server_id": default_server, "obd": settings})
        models.save_route(db, device_id, d["start"], d["end"], route)
        db.commit()
        print(f"  {d['name']}: {route['distance_m'] / 1000:.1f} km, {len(route['points'])} points via {source},"
              f" VIN {settings['vin']}")

    print(f"Seeded {len(SERVERS)} servers and {len(DEVICES)} devices.")
    db.close()


if __name__ == "__main__":
    main()
