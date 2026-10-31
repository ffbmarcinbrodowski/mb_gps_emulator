"""
OBD-II data of a Teltonika FMB003 (OBD dongle), simulated together with the drive.

ObdState follows the vehicle: mileage and distance to service change with the
driven distance, RPM / engine load / fuel rate come from speed and acceleration,
fuel is used up (and refilled now and then while parked), the coolant warms up
after the start and cools down while parked. Fault codes (DTC) and a weak
battery are set by events or in the device settings.

ObdReporter decides which values go into a record. Like a real FMB configured
"on change", a value is only sent when it changed noticeably; the VIN is sent
when the ignition is switched on. (Traccar can copy missing attributes from the
previous position with `processing.copyAttributes`.)

Like core/motion.py nothing here reads the clock - the caller passes the time step.

IO elements (Teltonika wiki "FMB003 Teltonika Data Sending Parameters ID"):
    30  Number of DTC            1 B            Traccar: faultCount  (model FMBxxx)
    31  Engine Load              1 B  %         Traccar: engineLoad  (model FMBxxx)
    32  Coolant Temperature      1 B  °C signed Traccar: coolantTemp (model FMBxxx)
    36  Engine RPM               2 B  rpm       Traccar: rpm         (model FMBxxx)
    43  Distance Traveled MIL On 2 B  km        Traccar: milDistance (model FMBxxx)
    48  Fuel Level               1 B  %         Traccar: io48
    60  Fuel Rate                2 B  l/h * 100 Traccar: io60
    256 VIN                      NX  ASCII      Traccar: vin
    281 Fault Codes              NX  ASCII "P0420,P0171"   Traccar: dtcs ("P0420 P0171")
    389 OBD OEM Total Mileage    4 B  km        Traccar: obdOdometer in m (model exactly FMB003)
    402 OEM Distance Until Service 4 B km, UNSIGNED -> 0 = service due or overdue   Traccar: io402
IDs above 255 and the NX values need Codec 8 Extended.
"""
import math
import random
import re

IO_DTC_COUNT = 30
IO_ENGINE_LOAD = 31
IO_COOLANT_TEMP = 32
IO_ENGINE_RPM = 36
IO_MIL_DISTANCE = 43
IO_FUEL_LEVEL = 48
IO_FUEL_RATE = 60
IO_VIN = 256
IO_FAULT_CODES = 281
IO_OEM_MILEAGE = 389
IO_OEM_SERVICE = 402

# --- Vehicle model (a typical compact car) ------------------------------------
MASS_KG = 1500
ROLLING_RESISTANCE = 0.012
DRAG_AREA = 0.7                # Cd * frontal area (m²)
AIR_DENSITY = 1.2
MAX_POWER_KW = 100
ENGINE_EFFICIENCY = 0.25
FUEL_ENERGY_KWH_PER_L = 8.9    # petrol
IDLE_RPM = 800
IDLE_FUEL_LPH = 0.8
GEAR_KMH_PER_1000_RPM = [8, 14, 21, 29, 37, 45]   # 1st ... 6th gear

AMBIENT_C = 15
COOLANT_WARM_C = 90
WARM_UP_S = 300                # time constant of warming up
COOL_DOWN_S = 2400             # time constant of cooling down
REFUEL_BELOW_PCT = 15

# Common real fault codes, used when a "set fault code" event picks one.
TYPICAL_DTCS = ["P0420", "P0171", "P0300", "P0128", "P0442", "P0113", "P0401", "P0135"]
DTC_PATTERN = re.compile(r"^[PCBU][0-3][0-9A-F]{3}$")
VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")      # no I, O, Q


class ObdState:
    """OBD values of one vehicle. The persistent ones are stored at the device."""

    def __init__(self, vin="", odometer_km=50_000.0, service_km=15_000.0, fuel_pct=70.0, tank_l=55.0,
                 dtcs=(), mil_km=0.0, coolant_c=AMBIENT_C, weak_battery=False, rng=None):
        self.rng = rng or random.Random()
        self.vin = vin or ""
        self.odometer_km = odometer_km
        self.service_km = service_km      # may go below 0 internally; sent as 0 (unsigned on the device)
        self.fuel_pct = fuel_pct
        self.tank_l = tank_l
        self.dtcs = list(dtcs)
        self.mil_km = mil_km
        self.coolant_c = coolant_c
        self.weak_battery = weak_battery
        # live values (not stored)
        self.rpm = 0.0
        self.load_pct = 0.0
        self.fuel_rate_lph = 0.0

    # --- Simulation --------------------------------------------------------

    def step(self, dt, distance_m, speed_kmh, accel_ms2, ignition):
        """Advance by dt seconds: distance driven, current speed and acceleration, ignition on/off."""
        km = max(0.0, distance_m) / 1000
        self.odometer_km += km
        self.service_km -= km
        if self.dtcs:
            self.mil_km += km          # "distance traveled with MIL on" counts only with fault codes

        if not ignition:
            self.rpm = self.load_pct = self.fuel_rate_lph = 0.0
            self._approach_coolant(AMBIENT_C, COOL_DOWN_S, dt)
            return

        v = speed_kmh / 3.6
        self.rpm = self._rpm(speed_kmh, accel_ms2)
        if v < 0.5:                                    # idling
            self.load_pct = 20 + self.rng.uniform(-2, 2)
            self.fuel_rate_lph = IDLE_FUEL_LPH
        elif accel_ms2 < -0.3:                         # braking / coasting: fuel cut-off
            self.load_pct = 5.0
            self.fuel_rate_lph = 0.0
        else:
            force = MASS_KG * 9.81 * ROLLING_RESISTANCE + 0.5 * AIR_DENSITY * DRAG_AREA * v * v \
                + MASS_KG * max(0.0, accel_ms2)
            power_kw = force * v / 1000
            available_kw = MAX_POWER_KW * min(1.0, self.rpm / 4000)
            self.load_pct = min(100.0, 20 + 80 * power_kw / available_kw)
            self.fuel_rate_lph = IDLE_FUEL_LPH + power_kw / (ENGINE_EFFICIENCY * FUEL_ENERGY_KWH_PER_L)

        used_l = self.fuel_rate_lph * dt / 3600
        self.fuel_pct = max(0.0, self.fuel_pct - 100 * used_l / self.tank_l)
        target = COOLANT_WARM_C + 5 * self.load_pct / 100      # a bit warmer under load
        self._approach_coolant(target, WARM_UP_S, dt)

    def _approach_coolant(self, target, time_constant, dt):
        # Exponential approach - also correct for long steps (history jumps over parking time).
        self.coolant_c = target + (self.coolant_c - target) * math.exp(-dt / time_constant)

    @staticmethod
    def _rpm(speed_kmh, accel_ms2):
        if speed_kmh < 1:
            return IDLE_RPM
        # Highest gear that keeps the engine above a minimum RPM (higher when accelerating).
        min_rpm = 1300 + 700 * max(0.0, min(1.0, accel_ms2 / 1.6))
        for kmh_per_1000 in reversed(GEAR_KMH_PER_1000_RPM):
            rpm = speed_kmh / kmh_per_1000 * 1000
            if rpm >= min_rpm:
                return min(rpm, 4500)
        return max(IDLE_RPM + 200, speed_kmh / GEAR_KMH_PER_1000_RPM[0] * 1000)

    def maybe_refuel(self):
        """Called when the vehicle parks: an almost empty tank gets filled up."""
        if self.fuel_pct < REFUEL_BELOW_PCT:
            self.fuel_pct = self.rng.uniform(90, 98)
            return True
        return False

    # --- Events ----------------------------------------------------------------

    def add_fault_code(self, code=None):
        code = code or self.rng.choice([c for c in TYPICAL_DTCS if c not in self.dtcs] or TYPICAL_DTCS)
        if code not in self.dtcs:
            self.dtcs.append(code)

    def clear_fault_codes(self):
        self.dtcs = []
        self.mil_km = 0.0           # a scan tool reset also clears the MIL distance

    # --- Output ----------------------------------------------------------------

    def io(self):
        """All OBD IO elements: {"1b": {...}, "2b": {...}, "4b": {...}, "nx": {...}}."""
        return {
            "1b": {
                IO_DTC_COUNT: len(self.dtcs),
                IO_ENGINE_LOAD: round(self.load_pct),
                IO_COOLANT_TEMP: max(-128, min(127, round(self.coolant_c))),
                IO_FUEL_LEVEL: round(self.fuel_pct),
            },
            "2b": {
                IO_ENGINE_RPM: round(self.rpm),
                IO_MIL_DISTANCE: min(65535, int(self.mil_km)),
                IO_FUEL_RATE: min(32767, round(self.fuel_rate_lph * 100)),
            },
            "4b": {
                IO_OEM_MILEAGE: int(self.odometer_km),
                IO_OEM_SERVICE: max(0, int(self.service_km)),       # unsigned like the real device
            },
            "nx": {
                IO_VIN: self.vin.encode("ascii"),
                IO_FAULT_CODES: ",".join(self.dtcs).encode("ascii"),
            },
        }

    def stored_fields(self):
        """Values kept at the device (survive restarts)."""
        return {
            "obd_odometer_km": round(self.odometer_km, 3), "obd_service_km": round(self.service_km, 3),
            "obd_fuel_pct": round(self.fuel_pct, 2), "obd_dtcs": ",".join(self.dtcs),
            "obd_mil_km": round(self.mil_km, 3), "obd_coolant_c": round(self.coolant_c, 1),
            "obd_weak_battery": int(self.weak_battery),
        }

    @classmethod
    def from_device(cls, device, rng=None):
        """Build the state from a device row (core/migrations/005_obd.sql)."""
        return cls(vin=device["vin"], odometer_km=device["obd_odometer_km"], service_km=device["obd_service_km"],
                   fuel_pct=device["obd_fuel_pct"], tank_l=device["obd_tank_l"],
                   dtcs=[c for c in (device["obd_dtcs"] or "").split(",") if c],
                   mil_km=device["obd_mil_km"], coolant_c=device["obd_coolant_c"],
                   weak_battery=bool(device["obd_weak_battery"]), rng=rng)


class ObdReporter:
    """Which OBD values go into the next record ("on change", like a real FMB)."""

    # Smallest change that is worth a new value (in the unit of the IO element).
    THRESHOLDS = {
        IO_ENGINE_RPM: 150, IO_ENGINE_LOAD: 10, IO_COOLANT_TEMP: 2, IO_FUEL_LEVEL: 1,
        IO_FUEL_RATE: 100, IO_OEM_MILEAGE: 1, IO_OEM_SERVICE: 1, IO_DTC_COUNT: 1, IO_MIL_DISTANCE: 1,
    }

    def __init__(self):
        self.sent = {}       # io id -> last value sent

    def select(self, io, everything=False, send_vin=False):
        """
        Return the subset of `io` (same structure as ObdState.io()) to put into the record.
        everything=True sends all values (first record, events); send_vin adds the VIN.
        """
        out = {"1b": {}, "2b": {}, "4b": {}, "nx": {}}
        for group in ("1b", "2b", "4b"):
            for io_id, value in io[group].items():
                last = self.sent.get(io_id)
                if everything or last is None or abs(value - last) >= self.THRESHOLDS.get(io_id, 1):
                    out[group][io_id] = value
                    self.sent[io_id] = value
        vin = io["nx"][IO_VIN]
        if vin and (everything or send_vin or IO_VIN not in self.sent):
            out["nx"][IO_VIN] = vin
            self.sent[IO_VIN] = vin
        # Fault codes are also sent when they were cleared (empty), otherwise a server that copies
        # attributes from the previous position would keep showing the old codes.
        codes = io["nx"][IO_FAULT_CODES]
        if everything or self.sent.get(IO_FAULT_CODES, b"") != codes:
            out["nx"][IO_FAULT_CODES] = codes
        self.sent[IO_FAULT_CODES] = codes
        return out


class ObdUnit:
    """
    The OBD part of one tracker, fed by the drive simulation. Used the same way by the
    live engine (core/engine.py) and the history generator (core/history.py).
    """

    def __init__(self, device, rng=None):
        self.state = ObdState.from_device(device, rng)
        self.reporter = ObdReporter()
        self._last_speed_ms = 0.0

    def step(self, dt, distance_m, vehicle):
        """vehicle = Trip.state() / Parked.state() after the step."""
        speed_ms = vehicle["speed_kmh"] / 3.6
        accel = (speed_ms - self._last_speed_ms) / dt if dt > 0 else 0.0
        self._last_speed_ms = speed_ms
        self.state.step(dt, distance_m, vehicle["speed_kmh"], accel, vehicle["ignition"])

    def park(self):
        """The vehicle parks: maybe fill up the tank."""
        self._last_speed_ms = 0.0
        self.state.maybe_refuel()

    def apply(self, action):
        """Events from core/events.py ("obd_action")."""
        if action == "dtc_set":
            self.state.add_fault_code()
        elif action == "dtc_clear":
            self.state.clear_fault_codes()
        elif action == "battery_weak":
            self.state.weak_battery = True
        elif action == "battery_ok":
            self.state.weak_battery = False
        elif action == "service_due":
            self.state.service_km = 0.0

    def record_io(self, ignition_switched_on=False, everything=False):
        """IO elements for the next record: changed values; VIN when the ignition is switched on."""
        return self.reporter.select(self.state.io(), everything=everything, send_vin=ignition_switched_on)


# --- VIN -------------------------------------------------------------------------

# World manufacturer identifiers of common brands (first 3 characters of a VIN).
WMIS = ["WVW", "WAU", "WBA", "WDD", "VF1", "VF3", "W0L", "TMB", "ZFA", "YV1", "VSS", "WF0"]
_VIN_VALUES = {**{str(d): d for d in range(10)},
               **dict(zip("ABCDEFGH", range(1, 9))), **dict(zip("JKLMN", range(1, 6))), "P": 7, "R": 9,
               **dict(zip("STUVWXYZ", range(2, 10)))}
_VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def vin_check_digit(vin):
    """ISO 3779 / North American check digit (position 9)."""
    total = sum(_VIN_VALUES[c] * w for c, w in zip(vin, _VIN_WEIGHTS))
    return "X" if total % 11 == 10 else str(total % 11)


def generate_vin(rng=None):
    """Realistic 17-character VIN: real manufacturer prefix, model year, valid check digit."""
    rng = rng or random.Random()
    chars = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    body = rng.choice(WMIS) + "".join(rng.choice(chars) for _ in range(5))        # WMI + vehicle descriptor
    year = rng.choice("EFGHJKLMNPRS")                                             # model year 2014-2025
    tail = year + rng.choice(chars) + "".join(rng.choice("0123456789") for _ in range(6))
    vin = body + "0" + tail
    return vin[:8] + vin_check_digit(vin) + vin[9:]
