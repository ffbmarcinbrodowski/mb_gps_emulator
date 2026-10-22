"""
Emulation engine.

Every running device gets its own thread (DeviceWorker). Once per second it:
    1. advances the vehicle along the route (core/motion.py: realistic speed,
       acceleration, curves, traffic stops, idling, GPS noise),
    2. asks the record policy (core/recorder.py) whether the tracker would save
       a record now (period / heading change / speed change / ignition change),
    3. if so, builds a Teltonika AVL record, puts it into the buffer and sends
       the buffer to the server - records are kept while the server is
       unreachable and sent later, like a real tracker does,
    4. stores the live state in the database (so the UI can show it).

Between seconds the worker can be woken up to send an event record right
away (crash, towing, ... - see events.py).

At the end of the route the vehicle idles, turns the ignition off (event
record) and the device becomes "finished" / the route "completed".

With a scenario (core/scenarios.py) the device is "armed" instead: it parks at
one end of the route (ignition off, a record every few minutes), drives at the
planned times, parks at the other end, and so on until it is stopped.
"""
import json
import queue
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, database, geo, scenarios
from .events import EVENTS
from .motion import GpsReceiver, Parked, Trip
from .recorder import RecordPolicy
from .tcp_client import TeltonikaClient
from .teltonika import (
    AvlRecord, IO_EXTERNAL_VOLTAGE, IO_GNSS_STATUS, IO_GSM_SIGNAL, IO_HDOP, IO_IGNITION, IO_MOVEMENT,
    IO_PDOP, IO_TOTAL_ODOMETER, IO_TRIP_ODOMETER,
)

TICK_S = 1.0              # simulation step
SEND_BATCH = 50           # records per packet when sending the buffer
MAX_BUFFER = 20_000       # oldest records are dropped beyond this (a real FMB stores ~100k)


class DeviceWorker(threading.Thread):
    def __init__(self, device_id, on_change=None):
        super().__init__(daemon=True, name=f"device-{device_id}")
        self.device_id = device_id
        self.on_change = on_change               # called with device_id after every state change
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()      # interrupts the sleep between steps
        self.pending_events = queue.Queue()      # event types waiting to be sent
        self.overspeed_event = None              # sent once the vehicle is really that fast
        self.buffer = []                         # records not yet accepted by the server
        self.rng = random.Random()
        self.fix = None                          # last GPS fix (what the tracker reports)

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()

    def trigger(self, event_type):
        """Called from the web/GUI thread: send this event as soon as possible."""
        self.pending_events.put(event_type)
        self.wake_event.set()

    def run(self):
        self.db = database.connect()
        try:
            self._run()
        except Exception as exc:  # never let a worker die silently
            print(f"[emulator] device {self.device_id} crashed: {exc}")
            self._update(status="stopped", last_error=str(exc))
        finally:
            self.db.close()

    def _run(self):
        db = self.db
        device = db.execute("SELECT * FROM devices WHERE id = ?", (self.device_id,)).fetchone()
        route = db.execute("SELECT * FROM routes WHERE device_id = ?", (self.device_id,)).fetchone()
        server = db.execute("SELECT * FROM servers WHERE id = ?", (device["server_id"],)).fetchone()
        if route is None or server is None:
            self._update(status="stopped", last_error="Device has no route or server")
            return

        self.device = device
        self.route = route
        self.realistic = bool(device["realistic"])
        self.gps = GpsReceiver(self.realistic, self.rng)
        self.policy = RecordPolicy(max(1, device["interval_s"]), self.realistic)
        self.odometer = device["odometer_m"]
        self.trip_start_odometer = self.odometer
        self.gsm = 5

        self.client = TeltonikaClient(server["host"], server["port"], device["imei"], config.TCP_TIMEOUT_S)
        self._update(status="running", last_error=None)
        self._set_route("running")
        print(f"[emulator] {device['name']} started -> {server['host']}:{server['port']}")

        scenario = json.loads(device["scenario"]) if device["scenario"] else None
        if scenario:
            self._run_scenario(scenario)
        else:
            self._run_once()
        self.client.close()

    def _run_once(self):
        """Manual mode: drive A -> B once (resuming where it was stopped)."""
        points, _profile = self._leg("forward")
        total = geo.cumulative_distances(points)[-1]
        progress = self.device["progress_m"] if self.device["direction"] == "forward" else 0.0
        if self._drive("forward", progress if progress < total else 0.0):
            self._update(status="finished", activity=None)
            self._set_route("completed")
            print(f"[emulator] {self.device['name']} finished")
        else:
            self._update(status="stopped", activity=None)
            print(f"[emulator] {self.device['name']} stopped")

    def _run_scenario(self, cfg):
        """
        Scenario mode (core/scenarios.py): park at one end, drive at the planned
        times, park at the other end, ... until the device is stopped.
        """
        tz = ZoneInfo(config.TIMEZONE)
        seed = self.device_id
        other = {"A": "B", "B": "A"}
        self._update(scenario_enabled=1)

        # Armed now: stand where the plan says the vehicle should be (or leave at once).
        leg_s = scenarios.leg_seconds(self.route["duration_s"])
        location, drive_now = scenarios.initial_position(cfg, datetime.now(tz), leg_s, seed)
        if cfg["type"] == "loop":
            # A loop has no fixed plan - continue from the end of the route the vehicle is at.
            location = self._nearest_end()

        while True:
            if not drive_now:
                departure = scenarios.next_departure(cfg, datetime.now(tz), seed)
                if not self._park(location, departure, tz):
                    break
            drive_now = False

            if not self._drive("forward" if location == "A" else "return"):
                break
            location = other[location]

            dwell = timedelta(minutes=cfg.get("dwell_min", 0))
            if cfg["type"] == "interval" and cfg.get("round_trip") and location == "B":
                # Round trip: wait at B, then drive back home.
                if not self._park(location, datetime.now(tz) + dwell, tz) or not self._drive("return"):
                    break
                location = "A"
            elif cfg["type"] == "loop":
                if not self._park(location, datetime.now(tz) + dwell, tz):
                    break

        self._update(status="stopped", activity=None, next_trip_at=None)
        print(f"[emulator] {self.device['name']} stopped")

    def _nearest_end(self):
        """'A' or 'B', whichever end of the route is closer to the last known position."""
        points, _profile = self._leg("forward")
        lat, lon = self.device["current_lat"], self.device["current_lon"]
        if lat is None:
            return "A"
        to_a = geo.haversine_m(lat, lon, points[0][0], points[0][1])
        to_b = geo.haversine_m(lat, lon, points[-1][0], points[-1][1])
        return "B" if to_b < to_a else "A"

    def _leg(self, direction):
        """Points and road profile of A -> B ("forward") or B -> A ("return")."""
        r = self.route
        if direction == "return":
            if r["return_points"]:
                profile = json.loads(r["return_profile"]) if r["return_profile"] else None
                return json.loads(r["return_points"]), profile
            return list(reversed(json.loads(r["points"]))), None   # old data without a return route
        return json.loads(r["points"]), json.loads(r["profile"]) if r["profile"] else None

    def _drive(self, direction, start_m=0.0):
        """Drive one leg. Returns True at the destination, False if the device was stopped."""
        points, profile = self._leg(direction)
        self.trip = Trip(points, profile, self.device["speed_kmh"], self.realistic, start_m, self.rng)
        self.trip_start_odometer = self.odometer
        self.fix = self.gps.fix(self.trip.state())
        self._update(direction=direction, activity="driving", next_trip_at=None, progress_m=start_m)
        return self._simulate(lambda: self.trip.finished)

    def _park(self, location, until, tz):
        """Stand at "A" or "B" with the ignition off until `until` (None = until stopped)."""
        points, _profile = self._leg("forward")
        at_b = location == "B"
        heading = self.fix["heading"] if self.fix else 0.0
        distance = geo.cumulative_distances(points)[-1] if at_b else 0.0
        self.trip = Parked(points[-1] if at_b else points[0], heading, distance, self.realistic)
        self.fix = self.gps.fix(self.trip.state())
        next_trip = f"{scenarios.DAY_NAMES[until.weekday()]} {until:%d.%m. %H:%M}" if until else None
        self._update(direction="forward", activity="parked", next_trip_at=next_trip, progress_m=distance)
        return self._simulate(lambda: until is not None and datetime.now(tz) >= until)

    def _simulate(self, done):
        """
        Step the current Trip/Parked state once per second until done() is true.
        Returns False if the device was stopped meanwhile.
        """
        last = time.monotonic()
        while True:
            now = time.monotonic()
            dt, last = now - last, now
            before = self.trip.s
            self.trip.step(dt)
            self.odometer += max(0.0, self.trip.s - before)
            self.fix = self.gps.fix(self.trip.state())
            self._maybe_record(now)
            if done():
                return True
            self._sleep(TICK_S - (time.monotonic() - now))
            if self.stop_event.is_set():
                return False

    # --- Records -------------------------------------------------------------

    def _maybe_record(self, now):
        # Overspeed: send the event when the vehicle is really that fast - or when the
        # speeding is over without reaching it (curves, traffic), at the speed reached.
        if self.overspeed_event and (self.fix["speed_kmh"] >= self.overspeed_event["overspeed_kmh"] - 5
                                     or self.trip.override is None):
            self._record(event=self.overspeed_event)
            self.overspeed_event = None
            return
        trigger = self.policy.check(self.fix, now)
        if trigger is not None:
            self._record(event_io_id=trigger)

    def _record(self, event=None, event_io_id=0):
        """Save a record (like the tracker does), send the buffer, store the live state."""
        self.buffer.append(self._make_record(event, event_io_id))
        del self.buffer[:-MAX_BUFFER]
        self.policy.saved(self.fix, time.monotonic())
        error = self._flush()

        fields = dict(
            current_lat=self.fix["lat"], current_lon=self.fix["lon"],
            current_speed=round(self.fix["speed_kmh"], 1), current_bearing=round(self.fix["heading"], 1),
            progress_m=self.trip.s, odometer_m=self.odometer, last_error=error,
        )
        if error is None:
            fields["last_sent_at"] = _utc_now()
        if event:
            fields.update(last_event=event["label"], last_event_at=_utc_now())
            print(f"[emulator] {self.device['name']} event: {event['label']}")
        self._update(**fields)

    def _flush(self):
        """Send buffered records. Returns an error text, or None when everything was sent."""
        while self.buffer:
            batch = self.buffer[:SEND_BATCH]
            try:
                self.client.send(batch)
            except Exception as exc:
                # Keep the records - they are sent when the server is reachable again.
                return f"{type(exc).__name__}: {exc} ({len(self.buffer)} records buffered)"
            del self.buffer[:len(batch)]
        return None

    def _make_record(self, event=None, event_io_id=0):
        fix, e = self.fix, event or {}
        ignition = e.get("ignition", int(fix["ignition"]))
        speed = e.get("speed", fix["speed_kmh"])
        if self.trip.realistic and self.rng.random() < 0.05:
            self.gsm = min(5, max(2, self.gsm + self.rng.choice((-1, 1))))

        record = AvlRecord(
            timestamp_ms=geo.now_ms(),
            lat=fix["lat"], lon=fix["lon"], altitude=fix["altitude"],
            angle=fix["heading"], speed_kmh=speed, satellites=fix["satellites"],
            priority=e.get("priority", 1 if event_io_id else 0),
            event_io_id=e.get("io_id", event_io_id),
            io_1b={
                IO_IGNITION: ignition,
                IO_MOVEMENT: e.get("movement", int(fix["moving"])),
                IO_GNSS_STATUS: 1,
                IO_GSM_SIGNAL: self.gsm,
            },
            io_2b={
                IO_EXTERNAL_VOLTAGE: e.get("external_voltage", self._voltage_mv(ignition)),
                IO_PDOP: round(fix["pdop"] * 10),
                IO_HDOP: round(fix["hdop"] * 10),
            },
            io_4b={
                IO_TOTAL_ODOMETER: int(self.odometer),
                IO_TRIP_ODOMETER: int(self.odometer - self.trip_start_odometer),
            },
        )
        if event:
            # Overspeed (IO 255) carries the speed itself, the others their fixed value.
            record.io_1b[event["io_id"]] = round(speed) if event["value"] is None else event["value"]
        return record

    def _voltage_mv(self, ignition):
        """Car battery: ~14 V while the engine runs (alternator), ~12.5 V when off."""
        if not self.trip.realistic:
            return 12_600
        base = 14_050 if ignition else 12_500
        return int(base + self.rng.gauss(0, 50))

    # --- Events --------------------------------------------------------------

    def _sleep(self, seconds):
        """Wait until the next step, sending events that arrive in the meantime."""
        deadline = time.monotonic() + seconds
        while not self.stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if self.wake_event.wait(remaining):
                self.wake_event.clear()
                self._send_pending_events()

    def _send_pending_events(self):
        while not self.stop_event.is_set():
            try:
                event_type = self.pending_events.get_nowait()
            except queue.Empty:
                return
            event = EVENTS[event_type]

            if event_type == "overspeed":
                # Speed up first; the record follows when the vehicle is really that fast.
                self.trip.overspeed(event["overspeed_kmh"], event["overspeed_s"])
                self.overspeed_event = event
                continue
            if event_type == "harsh_braking":
                self.trip.brake_hard()
            elif event_type == "harsh_acceleration":
                self.trip.accelerate_hard()
            elif event_type == "crash":
                self.trip.crash()
                self._update(scenario_enabled=0)   # a crashed vehicle doesn't follow its plan

            self.fix = self.gps.fix(self.trip.state())
            self._record(event=event)
            if event.get("stop_after"):
                self.stop_event.set()

    # --- Database --------------------------------------------------------------

    def _update(self, **fields):
        _update_device(self.db, self.device_id, **fields)
        if self.on_change:
            self.on_change(self.device_id)

    def _set_route(self, status):
        _update_route(self.db, self.device_id, status)
        if self.on_change:
            self.on_change(self.device_id)


class Emulator:
    """
    Keeps track of running DeviceWorkers. One instance per app (web or desktop).

    on_change(device_id) is called from the worker threads after every state change
    (position, status, event, error). The desktop app turns it into a Qt signal.
    """

    def __init__(self, on_change=None):
        self.on_change = on_change
        self.workers = {}
        self.lock = threading.Lock()

    def is_running(self, device_id):
        worker = self.workers.get(device_id)
        return worker is not None and worker.is_alive() and not worker.stop_event.is_set()

    def start(self, device_id):
        with self.lock:
            old = self.workers.get(device_id)
            if old is not None and old.is_alive():
                if not old.stop_event.is_set():
                    return False  # already running
                old.join(timeout=config.TCP_TIMEOUT_S + 1)  # wait for a stopping worker
            worker = DeviceWorker(device_id, self.on_change)
            self.workers[device_id] = worker
            worker.start()
            return True

    def stop(self, device_id, disarm=True):
        """Stop a device. disarm=False keeps its scenario armed (app shutdown -> resumed on next start)."""
        self.stop_many([device_id], disarm)

    def start_all(self, device_ids):
        return [d for d in device_ids if self.start(d)]

    def stop_all(self, disarm=True):
        self.stop_many(list(self.workers), disarm)

    def stop_many(self, device_ids, disarm=True):
        with self.lock:
            workers = [self.workers.pop(d) for d in device_ids if d in self.workers]
        for w in workers:   # signal all first ...
            w.stop()
        for w in workers:   # ... then wait, so stopping many devices is fast
            w.join(timeout=config.TCP_TIMEOUT_S + 1)
        if disarm and device_ids:
            db = database.connect()
            db.executemany("UPDATE devices SET scenario_enabled = 0 WHERE id = ?", [(d,) for d in device_ids])
            db.commit()
            db.close()

    def trigger(self, device_id, event_type):
        """Queue an event for a running device. Returns False if it is not running."""
        if not self.is_running(device_id):
            return False
        self.workers[device_id].trigger(event_type)
        return True


def reset_stale_statuses():
    """After a restart no thread is running, so 'running' devices are really stopped."""
    db = database.connect()
    db.execute("UPDATE devices SET status = 'stopped', activity = NULL WHERE status = 'running'")
    db.commit()
    db.close()


def _update_device(db, device_id, **fields):
    fields["updated_at"] = _utc_now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE devices SET {cols} WHERE id = ?", (*fields.values(), device_id))
    db.commit()


def _update_route(db, device_id, status):
    db.execute("UPDATE routes SET status = ? WHERE device_id = ?", (status, device_id))
    db.commit()


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
