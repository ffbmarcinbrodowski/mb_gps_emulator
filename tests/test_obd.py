import random
import unittest

from core import obd
from core.obd import ObdReporter, ObdState


def drive(state, seconds, speed_kmh, accel=0.0, ignition=True):
    """Drive at a constant speed for `seconds` (1 s steps)."""
    for _ in range(int(seconds)):
        state.step(1.0, speed_kmh / 3.6, speed_kmh, accel, ignition)


class ObdStateTest(unittest.TestCase):
    def test_fuel_consumption_is_realistic(self):
        for speed, low, high in ((100, 5.5, 8.5), (50, 3.5, 6.5)):
            state = ObdState(fuel_pct=100, tank_l=55, rng=random.Random(1))
            drive(state, 3600 * 100 / speed, speed)               # 100 km
            litres = (100 - state.fuel_pct) / 100 * 55
            self.assertTrue(low < litres < high, f"{litres:.1f} l/100 km at {speed} km/h")

    def test_mileage_and_service_follow_the_distance(self):
        state = ObdState(odometer_km=100_000, service_km=10, rng=random.Random(1))
        drive(state, 3600 * 0.25, 60)                              # 15 km
        self.assertAlmostEqual(state.odometer_km, 100_015, delta=0.1)
        self.assertLess(state.service_km, 0)                       # overdue internally ...
        self.assertEqual(state.io()["4b"][obd.IO_OEM_SERVICE], 0)  # ... but sent unsigned as 0

    def test_coolant_warms_up_and_cools_down(self):
        state = ObdState(coolant_c=15, rng=random.Random(1))
        drive(state, 15 * 60, 60)
        self.assertGreater(state.coolant_c, 85)
        state.step(3 * 3600, 0, 0, 0, ignition=False)             # one long step (history parking jump)
        self.assertLess(state.coolant_c, 40)

    def test_engine_values(self):
        state = ObdState(rng=random.Random(1))
        state.step(1, 0, 0, 0, ignition=True)
        self.assertEqual(state.rpm, obd.IDLE_RPM)
        state.step(1, 25, 90, 0, ignition=True)
        self.assertTrue(1300 <= state.rpm <= 3000)
        state.step(1, 25, 90, -1.5, ignition=True)                 # braking: fuel cut-off
        self.assertEqual(state.fuel_rate_lph, 0)
        state.step(1, 0, 0, 0, ignition=False)
        self.assertEqual((state.rpm, state.load_pct, state.fuel_rate_lph), (0, 0, 0))

    def test_fault_codes_and_mil_distance(self):
        state = ObdState(rng=random.Random(1))
        drive(state, 60, 60)
        self.assertEqual(state.mil_km, 0)                          # no codes, no MIL distance
        state.add_fault_code("P0420")
        drive(state, 60, 60)
        self.assertAlmostEqual(state.mil_km, 1.0, delta=0.01)
        self.assertEqual(state.io()["nx"][obd.IO_FAULT_CODES], b"P0420")
        state.clear_fault_codes()
        self.assertEqual((state.dtcs, state.mil_km), ([], 0))

    def test_refuel_only_when_almost_empty(self):
        state = ObdState(fuel_pct=40, rng=random.Random(1))
        self.assertFalse(state.maybe_refuel())
        state.fuel_pct = 10
        self.assertTrue(state.maybe_refuel())
        self.assertGreater(state.fuel_pct, 89)


class ObdReporterTest(unittest.TestCase):
    def test_only_changes_are_sent(self):
        state = ObdState(vin="WVWZZZ1KZ9W000123", rng=random.Random(1))
        reporter = ObdReporter()
        first = reporter.select(state.io(), everything=True)
        self.assertIn(obd.IO_VIN, first["nx"])
        self.assertIn(obd.IO_OEM_MILEAGE, first["4b"])
        second = reporter.select(state.io())
        self.assertEqual(sum(len(v) for v in second.values()), 0)  # nothing changed
        state.step(1, 0, 0, 0, ignition=True)                      # engine starts: rpm 0 -> 800
        third = reporter.select(state.io())
        self.assertIn(obd.IO_ENGINE_RPM, third["2b"])
        self.assertNotIn(obd.IO_VIN, third["nx"])

    def test_cleared_fault_codes_are_sent_empty(self):
        state = ObdState(dtcs=["P0420"], rng=random.Random(1))
        reporter = ObdReporter()
        reporter.select(state.io(), everything=True)
        state.clear_fault_codes()
        out = reporter.select(state.io())
        self.assertEqual(out["nx"][obd.IO_FAULT_CODES], b"")
        self.assertEqual(out["1b"][obd.IO_DTC_COUNT], 0)


class VinTest(unittest.TestCase):
    def test_check_digit(self):
        self.assertEqual(obd.vin_check_digit("1M8GDM9AXKP042788"), "X")    # textbook example
        self.assertEqual(obd.vin_check_digit("11111111111111111"), "1")

    def test_generated_vin_is_valid(self):
        rng = random.Random(3)
        for _ in range(50):
            vin = obd.generate_vin(rng)
            self.assertRegex(vin, obd.VIN_PATTERN)
            self.assertEqual(vin[8], obd.vin_check_digit(vin))
            self.assertIn(vin[:3], obd.WMIS)


if __name__ == "__main__":
    unittest.main()
