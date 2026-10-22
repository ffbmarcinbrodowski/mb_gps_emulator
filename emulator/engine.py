"""
Emulation engine.

Every running device gets its own thread (DeviceWorker). On every tick it:
    1. computes the current position on the route (progress in meters),
    2. sends it to the server as a Teltonika AVL record,
    3. stores the live state in the database (so the UI can show it),
    4. advances `progress` by speed * interval and sleeps `interval` seconds.

While sleeping, the worker can be woken up to send an event record right
away (crash, towing, ... - see events.py).

When the end of the route is reached, one last record with speed 0 is sent
and the device becomes "finished" / the route "completed".
"""
import json
import queue
import threading
import time
from datetime import datetime, timezone

import config
import database

from . import geo
from .events import EVENTS
from .tcp_client import TeltonikaClient
from .teltonika import (
    AvlRecord, IO_EXTERNAL_VOLTAGE, IO_GNSS_STATUS, IO_IGNITION, IO_MOVEMENT, IO_TOTAL_ODOMETER,
)

NORMAL_VOLTAGE_MV = 12_600


class DeviceWorker(threading.Thread):
    def __init__(self, device_id):
        super().__init__(daemon=True, name=f"device-{device_id}")
        self.device_id = device_id
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()      # interrupts the sleep between ticks
        self.pending_events = queue.Queue()      # event types waiting to be sent
        self.overspeed_ticks = 0                 # > 0 while an "overspeed" event is active
        self.last = None                         # (position, speed, progress) of the last record

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()

    def trigger(self, event_type):
        """Called from the web thread: send this event as soon as possible."""
        self.pending_events.put(event_type)
        self.wake_event.set()

    def run(self):
        self.db = database.connect()
        try:
            self._run()
        except Exception as exc:  # never let a worker die silently
            print(f"[emulator] device {self.device_id} crashed: {exc}")
            _update_device(self.db, self.device_id, status="stopped", last_error=str(exc))
        finally:
            self.db.close()

    def _run(self):
        db = self.db
        device = db.execute("SELECT * FROM devices WHERE id = ?", (self.device_id,)).fetchone()
        route = db.execute("SELECT * FROM routes WHERE device_id = ?", (self.device_id,)).fetchone()
        server = db.execute("SELECT * FROM servers WHERE id = ?", (device["server_id"],)).fetchone()
        if route is None or server is None:
            _update_device(db, self.device_id, status="stopped", last_error="Device has no route or server")
            return

        self.device = device
        points = json.loads(route["points"])
        cum = geo.cumulative_distances(points)
        total = cum[-1]
        interval = max(1, device["interval_s"])

        # Resume where we stopped; restart from the beginning if the route was completed.
        progress = device["progress_m"] if device["progress_m"] < total else 0.0

        self.client = TeltonikaClient(server["host"], server["port"], device["imei"], config.TCP_TIMEOUT_S)
        _update_device(db, self.device_id, status="running", progress_m=progress, last_error=None)
        _update_route(db, self.device_id, "running")
        print(f"[emulator] {device['name']} started -> {server['host']}:{server['port']}")

        while True:
            finished = progress >= total
            pos = geo.position_at(points, cum, progress)
            self._send(pos, self._tick_speed(finished), progress, ignition=not finished)

            if finished:
                _update_device(db, self.device_id, status="finished")
                _update_route(db, self.device_id, "completed")
                print(f"[emulator] {device['name']} finished")
                break

            progress = min(total, progress + geo.distance_for(self.last[1], interval))
            self._sleep(interval)
            if self.stop_event.is_set():
                _update_device(db, self.device_id, status="stopped")
                print(f"[emulator] {device['name']} stopped")
                break

        self.client.close()

    def _tick_speed(self, finished):
        if finished:
            return 0.0
        if self.overspeed_ticks > 0:
            self.overspeed_ticks -= 1
            return EVENTS["overspeed"]["overspeed_kmh"]
        return geo.vary_speed(self.device["speed_kmh"], config.SPEED_VARIATION)

    def _sleep(self, seconds):
        """Wait until the next tick, sending events that arrive in the meantime."""
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
            pos, speed, progress = self.last   # event happens at the last known position

            if event_type == "overspeed":
                speed = event["overspeed_kmh"]
                self.overspeed_ticks = event["overspeed_ticks"]
            speed = event.get("speed", speed)

            self._send(pos, speed, progress, event=event)
            _update_device(self.db, self.device_id, last_event=event["label"], last_event_at=_utc_now())
            print(f"[emulator] {self.device['name']} event: {event['label']}")

            if event.get("stop_after"):
                self.stop_event.set()

    def _send(self, pos, speed, progress, ignition=True, event=None):
        """Build one AVL record, send it and store the live state."""
        e = event or {}
        record = AvlRecord(
            timestamp_ms=geo.now_ms(),
            lat=pos["lat"], lon=pos["lon"], altitude=pos["altitude"],
            angle=pos["bearing"], speed_kmh=speed,
            priority=e.get("priority", 0),
            event_io_id=e.get("io_id", 0),
            io_1b={
                IO_IGNITION: e.get("ignition", int(ignition)),
                IO_MOVEMENT: e.get("movement", int(speed > 0)),
                IO_GNSS_STATUS: 1,
            },
            io_2b={IO_EXTERNAL_VOLTAGE: e.get("external_voltage", NORMAL_VOLTAGE_MV)},
            io_4b={IO_TOTAL_ODOMETER: int(progress)},
        )
        if event:
            # Overspeed (IO 255) carries the speed itself, the others their fixed value.
            record.io_1b[event["io_id"]] = round(speed) if event["value"] is None else event["value"]

        error = None
        try:
            self.client.send([record])
        except Exception as exc:
            # Keep moving even if the server is down - the UI shows the error.
            error = f"{type(exc).__name__}: {exc}"

        self.last = (pos, speed, progress)
        _update_device(
            self.db, self.device_id,
            current_lat=pos["lat"], current_lon=pos["lon"],
            current_speed=round(speed, 1), current_bearing=round(pos["bearing"], 1),
            progress_m=progress, last_error=error,
            last_sent_at=None if error else _utc_now(),
        )


class Emulator:
    """Keeps track of running DeviceWorkers. One global instance lives in app.py."""

    def __init__(self):
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
            worker = DeviceWorker(device_id)
            self.workers[device_id] = worker
            worker.start()
            return True

    def stop(self, device_id):
        self.stop_many([device_id])

    def start_all(self, device_ids):
        return [d for d in device_ids if self.start(d)]

    def stop_all(self):
        self.stop_many(list(self.workers))

    def stop_many(self, device_ids):
        with self.lock:
            workers = [self.workers.pop(d) for d in device_ids if d in self.workers]
        for w in workers:   # signal all first ...
            w.stop()
        for w in workers:   # ... then wait, so stopping many devices is fast
            w.join(timeout=config.TCP_TIMEOUT_S + 1)

    def trigger(self, device_id, event_type):
        """Queue an event for a running device. Returns False if it is not running."""
        if not self.is_running(device_id):
            return False
        self.workers[device_id].trigger(event_type)
        return True


def reset_stale_statuses():
    """After a restart no thread is running, so 'running' devices are really stopped."""
    db = database.connect()
    db.execute("UPDATE devices SET status = 'stopped' WHERE status = 'running'")
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
