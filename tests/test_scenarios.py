import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import scenarios

TZ = ZoneInfo("Europe/Berlin")
COMMUTE = {"type": "times", "times": ["07:30", "16:30"], "days": [0, 1, 2, 3, 4], "jitter_min": 0}
HOURLY = {"type": "interval", "every_min": 60, "from": "06:00", "to": "22:00", "round_trip": True,
          "dwell_min": 5, "days": scenarios.ALL_DAYS, "jitter_min": 0}
LOOP = {"type": "loop", "from": "06:00", "to": "22:00", "dwell_min": 5, "days": scenarios.ALL_DAYS}
LEG_S = 25 * 60


def at(day, hhmm):
    """Local time on a day of the week starting Monday 2026-10-19."""
    h, m = scenarios.parse_hhmm(hhmm)
    return datetime(2026, 10, 19 + day, h, m, tzinfo=TZ)


class DeparturesTest(unittest.TestCase):
    def test_fixed_times_only_on_selected_days(self):
        deps = scenarios.departures(COMMUTE, at(0, "00:00"), at(0, "00:00") + timedelta(days=7))
        self.assertEqual(len(deps), 10)                          # Mon-Fri, 2 per day
        self.assertTrue(all(d.weekday() < 5 for d in deps))
        self.assertEqual((deps[0].hour, deps[0].minute), (7, 30))

    def test_interval_inside_window(self):
        deps = scenarios.departures(HOURLY, at(2, "00:00"), at(3, "00:00"))
        self.assertEqual(len(deps), 16)                          # 06:00 ... 21:00
        self.assertEqual(deps[0].hour, 6)
        self.assertEqual(deps[-1].hour, 21)

    def test_jitter_is_stable_and_bounded(self):
        cfg = {**COMMUTE, "jitter_min": 5}
        first = scenarios.departures(cfg, at(0, "00:00"), at(1, "00:00"), seed=7)
        again = scenarios.departures(cfg, at(0, "00:00"), at(1, "00:00"), seed=7)
        self.assertEqual(first, again)                           # same device -> same plan
        self.assertTrue(abs(first[0] - at(0, "07:30")) <= timedelta(minutes=5))
        other = scenarios.departures(cfg, at(0, "00:00"), at(1, "00:00"), seed=8)
        self.assertNotEqual(first, other)                        # other device -> other shifts

    def test_daylight_saving_change(self):
        # 2026-10-25: clocks go back in Germany; 07:30 local is still 07:30 local.
        start = datetime(2026, 10, 24, 12, 0, tzinfo=TZ)
        deps = scenarios.departures({**COMMUTE, "days": scenarios.ALL_DAYS}, start, start + timedelta(days=2))
        morning = [d for d in deps if d.date().day == 25][0]
        self.assertEqual((morning.hour, morning.minute), (7, 30))
        self.assertEqual(morning.utcoffset(), timedelta(hours=1))


class NextDepartureTest(unittest.TestCase):
    def test_friday_evening_waits_for_monday(self):
        nxt = scenarios.next_departure(COMMUTE, at(4, "18:00"))
        self.assertEqual((nxt.weekday(), nxt.hour, nxt.minute), (0, 7, 30))

    def test_loop_inside_and_outside_window(self):
        self.assertEqual(scenarios.next_departure(LOOP, at(1, "12:00")), at(1, "12:00"))
        self.assertEqual(scenarios.next_departure(LOOP, at(1, "23:00")), at(2, "06:00"))

    def test_no_days_means_never(self):
        self.assertIsNone(scenarios.next_departure({**COMMUTE, "days": []}, at(0, "08:00")))


class InitialPositionTest(unittest.TestCase):
    def test_armed_at_ten_stands_at_b(self):
        # Left at 07:30, arrived long ago -> stands at B, waits for 16:30.
        self.assertEqual(scenarios.initial_position(COMMUTE, at(0, "10:00"), LEG_S), ("B", False))

    def test_armed_during_a_trip_drives_now(self):
        self.assertEqual(scenarios.initial_position(COMMUTE, at(0, "07:40"), LEG_S), ("A", True))

    def test_armed_in_the_evening_is_home(self):
        self.assertEqual(scenarios.initial_position(COMMUTE, at(0, "20:00"), LEG_S), ("A", False))

    def test_round_trip_positions(self):
        self.assertEqual(scenarios.initial_position(HOURLY, at(0, "10:10"), LEG_S), ("A", True))   # out
        self.assertEqual(scenarios.initial_position(HOURLY, at(0, "10:40"), LEG_S), ("B", True))   # back
        self.assertEqual(scenarios.initial_position(HOURLY, at(0, "23:30"), LEG_S), ("A", False))


class PreviewTest(unittest.TestCase):
    def test_commute_preview_alternates(self):
        trips, warnings = scenarios.preview(COMMUTE, at(0, "10:00"), LEG_S)
        self.assertEqual([t[1] for t in trips[:2]], ["B → A", "A → B"])
        self.assertEqual(warnings, [])

    def test_warning_when_trip_longer_than_interval(self):
        cfg = {**HOURLY, "every_min": 30}
        _trips, warnings = scenarios.preview(cfg, at(0, "05:00"), LEG_S)
        self.assertTrue(warnings)


if __name__ == "__main__":
    unittest.main()
