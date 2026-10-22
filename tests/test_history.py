import json
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import scenarios
from core.history import HistorySimulator
from core.teltonika import IO_IGNITION
from tests.test_motion import PROFILE, straight_route

TZ = ZoneInfo("Europe/Berlin")
DEVICE = {"realistic": 1, "speed_kmh": 130, "interval_s": 5}
POINTS = straight_route()
ROUTE = {"points": json.dumps(POINTS), "profile": json.dumps(PROFILE), "duration_s": 300,
         "return_points": json.dumps(list(reversed(POINTS))), "return_profile": json.dumps(PROFILE)}
COMMUTE = {"type": "times", "times": ["07:30", "16:30"], "days": scenarios.ALL_DAYS, "jitter_min": 5}


def generate(cfg, days=3, seed=1):
    start = datetime(2026, 10, 19, 0, 0, tzinfo=TZ)
    sim = HistorySimulator(DEVICE, ROUTE, cfg, seed=seed)
    return sim, list(sim.records(start, start + timedelta(days=days)))


class HistoryTest(unittest.TestCase):
    def test_timestamps_increase_and_stay_in_period(self):
        _sim, records = generate(COMMUTE)
        times = [r.timestamp_ms for r in records]
        self.assertEqual(times, sorted(times))
        start = datetime(2026, 10, 19, tzinfo=TZ).timestamp() * 1000
        self.assertGreaterEqual(times[0], start)
        self.assertLess(times[-1], start + 3 * 86_400_000)

    def test_trips_follow_the_scenario(self):
        _sim, records = generate(COMMUTE)
        ignition_on = [datetime.fromtimestamp(r.timestamp_ms / 1000, TZ) for r in records
                       if r.event_io_id == IO_IGNITION and r.io_1b[IO_IGNITION] == 1]
        self.assertEqual(len(ignition_on), 6)                         # 2 trips a day for 3 days
        planned = scenarios.departures(COMMUTE, datetime(2026, 10, 19, tzinfo=TZ),
                                       datetime(2026, 10, 22, tzinfo=TZ), seed=1)
        for actual, plan in zip(ignition_on, planned):
            self.assertLess(abs((actual - plan).total_seconds()), 2)   # leaves exactly at the departure

    def test_parked_heartbeat_every_five_minutes(self):
        _sim, records = generate(COMMUTE, days=1)
        parked = [r.timestamp_ms for r in records if r.io_1b[IO_IGNITION] == 0 and r.event_io_id == 0]
        gaps = {round((b - a) / 1000) for a, b in zip(parked, parked[1:])}
        self.assertIn(300, gaps)

    def test_odometer_grows_by_the_driven_distance(self):
        sim, records = generate(COMMUTE, days=2)
        odometers = [r.io_4b[16] for r in records]
        self.assertEqual(odometers, sorted(odometers))
        self.assertAlmostEqual(sim.odometer, 4 * 2898, delta=50)      # 4 trips of ~2.9 km

    def test_same_seed_same_history(self):
        _a, first = generate(COMMUTE, days=1, seed=5)
        _b, again = generate(COMMUTE, days=1, seed=5)
        self.assertEqual([(r.timestamp_ms, r.lat, r.lon) for r in first],
                         [(r.timestamp_ms, r.lat, r.lon) for r in again])


if __name__ == "__main__":
    unittest.main()
