"""
History generator: past data for a scenario device, e.g. the last 30 days.

The scenario is simulated in virtual time with exactly the same models as live
emulation (core/motion.py, core/recorder.py): the same trips, traffic stops,
idling, parking with heartbeat records, GPS noise and IO elements. Every record
gets the timestamp of the moment it "happened" in the past.

The records are then sent like a tracker that was offline and empties its
buffer: in batches, oldest first. Nothing is faster than reality except the
upload itself - the data in Traccar looks as if the vehicle really drove.

Records are generated and sent as a stream, so even months of data need
little memory.
"""
import json
import random
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, database, models, scenarios
from .motion import GpsReceiver, Parked, Trip
from .obd import ObdUnit
from .recorder import RecordBuilder, RecordPolicy
from .tcp_client import TeltonikaClient
from .teltonika import CODEC_8, CODEC_8_EXT, IO_IGNITION

MAX_DAYS = 90
SEND_BATCH = 100       # records per packet (Codec 8 allows up to 255)
SEND_RETRIES = 3


class HistorySimulator:
    """Plays a scenario in virtual time and yields the AVL records in time order."""

    def __init__(self, device, route, cfg, seed, odometer_m=0.0):
        self.cfg = cfg
        self.seed = seed
        self.realistic = bool(device["realistic"])
        self.max_speed = device["speed_kmh"]
        self.route_duration = route["duration_s"]
        self.stops_s = models.stops_seconds(route)
        self.rng = random.Random(f"history|{seed}")    # same input -> same history
        self.legs = {"forward": models.route_leg(route, "forward"), "return": models.route_leg(route, "return")}
        self.gps = GpsReceiver(self.realistic, self.rng)
        self.policy = RecordPolicy(max(1, device["interval_s"]), self.realistic)
        self.builder = RecordBuilder(self.realistic, self.rng)
        self.odometer = odometer_m
        # OBD values follow the simulated drive exactly like in live emulation (core/obd.py).
        self.obd = ObdUnit(device, self.rng) if device["obd_enabled"] else None
        self.obd_t = 0.0          # virtual time the OBD state was last advanced to
        self.t = 0.0              # virtual time (Unix seconds)
        self.fix = None
        self.start_ts = self.end_ts = 0.0

    @property
    def progress(self):
        """0.0 - 1.0 of the simulated period."""
        span = self.end_ts - self.start_ts
        return min(1.0, max(0.0, (self.t - self.start_ts) / span)) if span else 1.0

    def records(self, start, end, cancelled=lambda: False):
        """Generator of AvlRecords between the local datetimes `start` and `end`."""
        tz = start.tzinfo
        self.start_ts, self.end_ts = start.timestamp(), end.timestamp()
        self.t = self.obd_t = self.start_ts
        other = {"A": "B", "B": "A"}
        leg_s = scenarios.leg_seconds(self.route_duration, self.stops_s)
        location, drive_now = scenarios.initial_position(self.cfg, start, leg_s, self.seed)

        while self.t < self.end_ts and not cancelled():
            if not drive_now:
                departure = scenarios.next_departure(self.cfg, datetime.fromtimestamp(self.t, tz), self.seed)
                until = self.end_ts if departure is None else min(departure.timestamp(), self.end_ts)
                yield from self._park(location, until)
                if until >= self.end_ts:
                    break
            drive_now = False

            yield from self._drive("forward" if location == "A" else "return")
            location = other[location]

            dwell = self.cfg.get("dwell_min", 0) * 60
            if self.cfg["type"] == "interval" and self.cfg.get("round_trip") and location == "B":
                yield from self._park(location, min(self.t + dwell, self.end_ts))
                yield from self._drive("return")
                location = "A"
            elif self.cfg["type"] == "loop":
                yield from self._park(location, min(self.t + dwell, self.end_ts))

    def _drive(self, direction):
        points, profile, stops = self.legs[direction]
        trip = Trip(points, profile, self.max_speed, self.realistic, rng=self.rng, stops=stops)
        trip_start = self.odometer
        while not trip.finished and self.t < self.end_ts:
            self.t += 1.0
            before = trip.s
            trip.step(1.0)
            self.odometer += trip.s - before
            vehicle = trip.state()
            self._advance_obd(trip.s - before, vehicle)
            self.fix = self.gps.fix(vehicle)
            trigger = self.policy.check(self.fix, self.t)
            if trigger is not None:
                yield self._record(trigger, trip_start)

    def _park(self, location, until):
        """Parked with the ignition off: jump from heartbeat to heartbeat instead of second by second."""
        points, _profile, _stops = self.legs["forward"]
        heading = self.fix["heading"] if self.fix else 0.0
        parked = Parked(points[-1] if location == "B" else points[0], heading, realistic=self.realistic)
        if self.obd:
            self.obd.park()
        while self.t < until:
            self._advance_obd(0.0, parked.state())     # one long step: the engine cools down
            self.fix = self.gps.fix(parked.state())
            trigger = self.policy.check(self.fix, self.t)
            if trigger is not None:
                yield self._record(trigger, self.odometer)
            next_record = self.policy.last[0] + self.policy.parked_period
            self.t = min(until, max(self.t + 1.0, next_record))

    def _advance_obd(self, distance_m, vehicle):
        if self.obd and self.t > self.obd_t:
            self.obd.step(self.t - self.obd_t, distance_m, vehicle)
            self.obd_t = self.t

    def _record(self, trigger, trip_start):
        obd_io = None
        if self.obd:
            obd_io = self.obd.record_io(ignition_switched_on=trigger == IO_IGNITION and self.fix["ignition"])
        record = self.builder.build(self.fix, int(self.t * 1000), self.odometer, self.odometer - trip_start,
                                    event_io_id=trigger, extra_io=obd_io,
                                    weak_battery=bool(self.obd and self.obd.state.weak_battery))
        self.policy.saved(self.fix, self.t)
        return record


class HistoryJob(threading.Thread):
    """Generates and sends the history of one device in the background."""

    def __init__(self, device_id, days):
        super().__init__(daemon=True, name=f"history-{device_id}")
        self.device_id = device_id
        self.days = days
        self.cancel_event = threading.Event()
        self.state = "running"        # running / done / cancelled / failed
        self.progress = 0.0
        self.sent = 0
        self.period = ""
        self.error = None

    def cancel(self):
        self.cancel_event.set()

    def status(self):
        return {"state": self.state, "progress": round(100 * self.progress, 1), "sent": self.sent,
                "days": self.days, "period": self.period, "error": self.error}

    def run(self):
        db = database.connect()
        client = None
        try:
            device = db.execute("SELECT * FROM devices WHERE id = ?", (self.device_id,)).fetchone()
            route = db.execute("SELECT * FROM routes WHERE device_id = ?", (self.device_id,)).fetchone()
            server = db.execute("SELECT * FROM servers WHERE id = ?", (device["server_id"],)).fetchone()
            cfg = json.loads(device["scenario"])

            tz = ZoneInfo(config.TIMEZONE)
            end = datetime.now(tz).replace(microsecond=0)
            start = end - timedelta(days=self.days)
            self.period = f"{start:%d.%m.%Y %H:%M} – {end:%d.%m.%Y %H:%M} ({config.TIMEZONE})"
            print(f"[history] {device['name']}: {self.period}")

            sim = HistorySimulator(device, route, cfg, seed=self.device_id, odometer_m=device["odometer_m"])
            codec = CODEC_8_EXT if device["codec"] == "8E" else CODEC_8
            client = TeltonikaClient(server["host"], server["port"], device["imei"], config.TCP_TIMEOUT_S, codec)
            batch = []
            for record in sim.records(start, end, cancelled=self.cancel_event.is_set):
                batch.append(record)
                if len(batch) >= SEND_BATCH:
                    self._send(client, batch)
                    batch = []
                    self.progress = sim.progress
            if batch and not self.cancel_event.is_set():
                self._send(client, batch)

            # The live odometer continues from where the history ended; the device stands
            # where the history left it.
            if sim.fix:
                db.execute(
                    "UPDATE devices SET odometer_m = ?, current_lat = ?, current_lon = ?, current_speed = 0,"
                    " current_bearing = ?, last_sent_at = ?, updated_at = ? WHERE id = ?",
                    (sim.odometer, sim.fix["lat"], sim.fix["lon"], round(sim.fix["heading"], 1),
                     _utc_now(), _utc_now(), self.device_id))
                if sim.obd:
                    # OBD mileage, fuel, ... continue from the end of the history too.
                    fields = sim.obd.state.stored_fields()
                    db.execute(f"UPDATE devices SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                               (*fields.values(), self.device_id))
                db.commit()
            self.progress = sim.progress if self.cancel_event.is_set() else 1.0
            self.state = "cancelled" if self.cancel_event.is_set() else "done"
            print(f"[history] {device['name']}: {self.state}, {self.sent} records sent")
        except Exception as exc:
            self.state = "cancelled" if self.cancel_event.is_set() else "failed"
            self.error = f"{type(exc).__name__}: {exc}"
            print(f"[history] device {self.device_id} failed: {self.error}")
        finally:
            if client:
                client.close()
            db.close()

    def _send(self, client, batch):
        """Send one packet; reconnect and retry a few times before giving up."""
        for attempt in range(SEND_RETRIES):
            try:
                client.send(batch)
                self.sent += len(batch)
                return
            except Exception:
                client.close()
                if attempt == SEND_RETRIES - 1 or self.cancel_event.wait(2):
                    raise


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
