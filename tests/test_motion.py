import math
import random
import unittest

from core import geo
from core.motion import GpsReceiver, Trip
from core.recorder import RecordPolicy
from core.teltonika import IO_IGNITION


def straight_route(km=3, step_m=50):
    """A straight road going east, with a 90° turn to the north in the middle."""
    points, lat, lon = [], 49.2, 6.9
    n = int(km * 1000 / step_m)
    for i in range(n // 2):
        points.append([lat, lon + i * step_m / 72_700, 200])
    corner_lon = points[-1][1]
    for i in range(1, n // 2):
        points.append([lat + i * step_m / 111_320, corner_lon, 200])
    return points


PROFILE = {"average_speed": [[0, 200, 50.0]], "max_speed": [[0, 200, 50.0]], "road_class": [[0, 200, "primary"]]}


def drive(trip, max_s=3 * 3600):
    states, t = [], 0
    while not trip.finished and t < max_s:
        trip.step(1.0)
        t += 1
        states.append(trip.state())
    return states


class TripTest(unittest.TestCase):
    def test_reaches_the_end_with_ignition_off(self):
        states = drive(Trip(straight_route(), PROFILE, 130, rng=random.Random(1)))
        self.assertEqual(states[-1]["phase"], "done")
        self.assertFalse(states[-1]["ignition"])
        self.assertAlmostEqual(states[-1]["distance_m"], geo.cumulative_distances(straight_route())[-1], delta=1)

    def test_physical_limits(self):
        for seed in range(5):
            trip = Trip(straight_route(), PROFILE, 130, rng=random.Random(seed))
            prev_v, max_acc, max_dec, top = 0.0, 0.0, 0.0, 0.0
            while not trip.finished:
                trip.step(1.0)
                max_acc = max(max_acc, trip.v - prev_v)
                max_dec = max(max_dec, prev_v - trip.v)
                top = max(top, trip.v * 3.6)
                prev_v = trip.v
            self.assertLessEqual(max_acc, 1.61)
            self.assertLessEqual(max_dec, 3.51)
            self.assertLessEqual(top, 50 * 1.05 * 1.05 + 1)        # speed limit (+ driver variation)

    def test_slows_down_for_the_corner(self):
        trip = Trip(straight_route(), PROFILE, 130, rng=random.Random(3))
        trip.stops = []                                            # no random traffic stops
        corner_index = int(3 * 1000 / 50) // 2 - 1                 # last point of the eastward part
        corner_m = geo.cumulative_distances(straight_route())[corner_index]
        speed_at_corner = None
        while not trip.finished:
            trip.step(1.0)
            if speed_at_corner is None and trip.s >= corner_m:
                speed_at_corner = trip.v * 3.6
        self.assertLess(speed_at_corner, 35)

    def test_simple_mode_constant_speed(self):
        trip = Trip(straight_route(), None, 60, realistic=False)
        trip.step(1.0)
        self.assertAlmostEqual(trip.v * 3.6, 60)
        self.assertEqual(trip.stops, [])


class GpsAndRecordsTest(unittest.TestCase):
    def test_position_frozen_while_standing(self):
        gps = GpsReceiver(True, random.Random(1))
        state = {"lat": 49.2, "lon": 6.9, "altitude": 200, "heading": 90, "speed_kmh": 0,
                 "ignition": True, "moving": False}
        first, second = gps.fix(state), gps.fix(state)
        self.assertEqual((first["lat"], first["lon"]), (second["lat"], second["lon"]))

    def test_noise_is_a_few_meters(self):
        gps = GpsReceiver(True, random.Random(2))
        state = {"lat": 49.2, "lon": 6.9, "altitude": 200, "heading": 90, "speed_kmh": 50,
                 "ignition": True, "moving": True}
        errors = []
        for _ in range(600):
            f = gps.fix(state)
            errors.append(geo.haversine_m(49.2, 6.9, f["lat"], f["lon"]))
        self.assertLess(sum(errors) / len(errors), 6)
        self.assertLess(max(errors), 20)

    def test_record_policy(self):
        policy = RecordPolicy(period_s=10)
        fix = {"ignition": True, "moving": True, "speed_kmh": 50, "heading": 90}
        self.assertEqual(policy.check(fix, 0), IO_IGNITION)              # first record: ignition on
        policy.saved(fix, 0)
        self.assertIsNone(policy.check(fix, 5))                            # nothing changed
        self.assertEqual(policy.check({**fix, "heading": 105}, 5), 0)      # heading changed 15°
        self.assertEqual(policy.check({**fix, "speed_kmh": 65}, 5), 0)     # speed changed 15 km/h
        self.assertEqual(policy.check(fix, 10), 0)                         # period
        self.assertEqual(policy.check({**fix, "ignition": False}, 6), IO_IGNITION)
        parked = {**fix, "ignition": False, "moving": False, "speed_kmh": 0}
        policy.saved(parked, 100)
        self.assertIsNone(policy.check(parked, 200))
        self.assertEqual(policy.check(parked, 400), 0)                     # parked heartbeat (300 s)


if __name__ == "__main__":
    unittest.main()
