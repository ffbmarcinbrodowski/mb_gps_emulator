"""
Seed the database with example servers and devices (around Saarbrücken).

    python seed.py          # only if the database is empty
    python seed.py --reset  # delete all devices/servers first

Routes are generated with GraphHopper; if that fails a straight line is used.
"""
import sys

import database
import models
from emulator import graphhopper

SERVERS = [
    {"name": "Local Traccar", "host": "127.0.0.1", "port": 5027},
    {"name": "Fake server (tools/fake_server.py)", "host": "127.0.0.1", "port": 5055},
    {"name": "Traccar demo", "host": "demo.traccar.org", "port": 5027},
]

DEVICES = [
    {"name": "Teltonika001", "imei": "356307042441001", "start": [49.2043, 6.9472], "end": [49.2425, 7.0445],
     "interval_s": 5, "speed_kmh": 50},
    {"name": "Teltonika002", "imei": "356307042441002", "start": [49.2766, 7.0365], "end": [49.2152, 7.0390],
     "interval_s": 5, "speed_kmh": 40},
    {"name": "Teltonika003", "imei": "356307042441003", "start": [49.2790, 7.1150], "end": [49.2160, 7.1080],
     "interval_s": 10, "speed_kmh": 70},
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

    for d in DEVICES:
        try:
            route = graphhopper.generate_route(d["start"], d["end"])
            source = "GraphHopper"
        except graphhopper.RoutingError as exc:
            route = graphhopper.straight_line_route(d["start"], d["end"])
            source = f"straight line ({exc})"
        device_id = models.create_device(db, {**d, "server_id": default_server})
        models.save_route(db, device_id, d["start"], d["end"], route)
        db.commit()
        print(f"  {d['name']}: {route['distance_m'] / 1000:.1f} km, {len(route['points'])} points via {source}")

    print(f"Seeded {len(SERVERS)} servers and {len(DEVICES)} devices.")
    db.close()


if __name__ == "__main__":
    main()
