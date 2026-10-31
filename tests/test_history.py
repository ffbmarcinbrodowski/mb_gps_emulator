import json
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import scenarios
from core.history import HistorySimulator
from core.teltonika import IO_IGNITION
from tests.test_motion import PROFILE, straight_route

TZ = ZoneInfo("Europe/Berlin")
DEVICE = {"realistic": 1, "speed_kmh": 130, "interval_s": 5, "obd_enabled": 0}
OBD_DEVICE = {**DEVICE, "obd_enabled": 1, "vin": "WVWZZZ1KZ9W000123", "obd_odometer_km": 120_000.0,
              "obd_service_km": 3.0, "obd_fuel_pct": 60.0, "obd_tank_l": 55.0, "obd_dtcs": "", "obd_mil_km": 0.0,
              "obd_coolant_c": 15.0, "obd_weak_battery": 0}
POINTS = straight_route()
ROUTE = {"points": json.dumps(POINTS), "profile": json.dumps(PROFILE), "duration_s": 300,
         "return_points": json.dumps(list(reversed(POINTS))), "return_profile": json.dumps(PROFILE), "stops": None}
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

    def test_obd_values_follow_the_history(self):
        start = datetime(2026, 10, 19, 0, 0, tzinfo=TZ)
        sim = HistorySimulator(OBD_DEVICE, ROUTE, COMMUTE, seed=1)
        records = list(sim.records(start, start + timedelta(days=1)))
        mileage = [r.io_4b[389] for r in records if 389 in r.io_4b]
        self.assertEqual(mileage, sorted(mileage))                         # never goes back
        self.assertAlmostEqual(mileage[-1] - 120_000, 2 * 2.9, delta=1.1)  # two trips of ~2.9 km
        vin_records = [r for r in records if 256 in r.io_nx]
        ignition_on = [r for r in records if r.event_io_id == IO_IGNITION and r.io_1b[IO_IGNITION] == 1]
        self.assertEqual(len(vin_records), 1 + len(ignition_on))           # first record + every start
        self.assertEqual(sim.obd.state.io()["4b"][402], 0)                 # 3 km to service -> due (0)
        self.assertLess(sim.obd.state.coolant_c, 30)                       # cooled down at the end of the day
        self.assertLess(len([r for r in records if 36 in r.io_2b]), len(records))   # only on change

    def test_same_seed_same_history(self):
        _a, first = generate(COMMUTE, days=1, seed=5)
        _b, again = generate(COMMUTE, days=1, seed=5)
        self.assertEqual([(r.timestamp_ms, r.lat, r.lon) for r in first],
                         [(r.timestamp_ms, r.lat, r.lon) for r in again])


class HistoryStopsTest(unittest.TestCase):
    def test_stop_lengthens_the_trip_with_the_engine_running(self):
        def first_trip(route):
            sim = HistorySimulator(DEVICE, route, {**COMMUTE, "jitter_min": 0}, seed=1)
            start = datetime(2026, 10, 19, 0, 0, tzinfo=TZ)
            records = list(sim.records(start, start + timedelta(hours=12)))
            ignition = [r.timestamp_ms for r in records if r.event_io_id == IO_IGNITION]
            return ignition[:2]           # ignition on / off of the 07:30 trip

        plain = first_trip(ROUTE)
        with_stop = first_trip({**ROUTE, "stops": json.dumps([{"lat": 0, "lon": 0, "dwell_min": 20, "index": 20}])})
        # No extra ignition events: the engine runs at the stop. The trip takes ~20 min longer.
        self.assertGreater(with_stop[1] - with_stop[0], plain[1] - plain[0] + 19 * 60_000)


if __name__ == "__main__":
    unittest.main()
