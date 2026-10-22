"""
When does the tracker save a record? Modelled on Teltonika FMB "data acquisition":

    while the ignition is on, a record is saved when ANY of these is true:
        - `period` seconds passed since the last record,
        - the heading changed by `min_angle` degrees (more points in curves),
        - the speed changed by `min_speed_delta` km/h,
    while the ignition is off: one record every `parked_period` seconds,
    and an event record (event IO 239) every time the ignition changes.

Like core/motion.py it does not read the clock - the caller passes the time.

RecordBuilder turns a fix into an AVL record (IO elements, voltage, GSM signal).
"""
import random

from .teltonika import (
    AvlRecord, IO_EXTERNAL_VOLTAGE, IO_GNSS_STATUS, IO_GSM_SIGNAL, IO_HDOP, IO_IGNITION, IO_MOVEMENT,
    IO_PDOP, IO_TOTAL_ODOMETER, IO_TRIP_ODOMETER,
)


class RecordPolicy:
    def __init__(self, period_s, realistic=True, min_angle=10, min_speed_delta=10, parked_period_s=300):
        self.period = period_s
        self.realistic = realistic
        self.min_angle = min_angle
        self.min_speed_delta = min_speed_delta
        self.parked_period = parked_period_s
        self.last = None        # (time, fix) of the last saved record

    def check(self, fix, now):
        """
        Returns None (no record), 0 (periodic record) or the event IO ID
        that triggered the record (IO_IGNITION when the ignition changed).
        """
        if self.last is None:
            # The first record of a drive is the "ignition on" event.
            return IO_IGNITION if fix["ignition"] else 0
        last_time, last_fix = self.last
        elapsed = now - last_time

        if fix["ignition"] != last_fix["ignition"]:
            return IO_IGNITION
        if not fix["ignition"]:
            return 0 if elapsed >= self.parked_period else None
        if elapsed >= self.period:
            return 0
        if self.realistic and fix["moving"] and fix["speed_kmh"] > 5:
            turn = abs((fix["heading"] - last_fix["heading"] + 180) % 360 - 180)
            if turn >= self.min_angle or abs(fix["speed_kmh"] - last_fix["speed_kmh"]) >= self.min_speed_delta:
                return 0
        return None

    def saved(self, fix, now):
        self.last = (now, fix)


class RecordBuilder:
    """
    Turns a GPS fix + vehicle state into a Teltonika AVL record with the IO elements
    a real FMB sends. Used by the live engine and by the history generator, so both
    produce exactly the same kind of data.
    """

    def __init__(self, realistic=True, rng=None):
        self.realistic = realistic
        self.rng = rng or random.Random()
        self.gsm = 5     # GSM signal 0-5, changes slowly

    def build(self, fix, timestamp_ms, odometer_m, trip_odometer_m, event=None, event_io_id=0):
        e = event or {}
        ignition = e.get("ignition", int(fix["ignition"]))
        speed = e.get("speed", fix["speed_kmh"])
        if self.realistic and self.rng.random() < 0.05:
            self.gsm = min(5, max(2, self.gsm + self.rng.choice((-1, 1))))

        record = AvlRecord(
            timestamp_ms=timestamp_ms,
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
                IO_TOTAL_ODOMETER: int(odometer_m),
                IO_TRIP_ODOMETER: int(trip_odometer_m),
            },
        )
        if event:
            # Overspeed (IO 255) carries the speed itself, the others their fixed value.
            record.io_1b[event["io_id"]] = round(speed) if event["value"] is None else event["value"]
        return record

    def _voltage_mv(self, ignition):
        """Car battery: ~14 V while the engine runs (alternator), ~12.5 V when off."""
        if not self.realistic:
            return 12_600
        base = 14_050 if ignition else 12_500
        return int(base + self.rng.gauss(0, 50))
