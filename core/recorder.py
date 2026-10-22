"""
When does the tracker save a record? Modelled on Teltonika FMB "data acquisition":

    while the ignition is on, a record is saved when ANY of these is true:
        - `period` seconds passed since the last record,
        - the heading changed by `min_angle` degrees (more points in curves),
        - the speed changed by `min_speed_delta` km/h,
    while the ignition is off: one record every `parked_period` seconds,
    and an event record (event IO 239) every time the ignition changes.

Like core/motion.py it does not read the clock - the caller passes the time.
"""
from .teltonika import IO_IGNITION


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
