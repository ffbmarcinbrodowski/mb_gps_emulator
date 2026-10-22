"""
Realistic vehicle motion along a route, simulated step by step (normally 1 s).

The vehicle:
    - starts with ignition on and idles for a moment before driving off,
    - drives at the typical speed of each road (GraphHopper average_speed,
      capped by the speed limit and the device's max speed),
    - accelerates and brakes smoothly and slows down before curves,
      lower speed limits, stops and the end of the route,
    - stops now and then at traffic lights / junctions (more often in town),
    - idles for a moment at the destination and turns the ignition off.

GpsReceiver turns the true state into what a tracker reports (GPS noise,
satellites, HDOP, frozen position while standing still).

Nothing here depends on the wall clock: the caller passes the time step.
That makes it easy to test and to generate history with real timestamps.
"""
import math
import random

from . import geo

# Typical speeds when GraphHopper gives no average_speed (km/h).
ROAD_CLASS_SPEED = {
    "motorway": 120, "trunk": 100, "primary": 70, "secondary": 60, "tertiary": 50,
    "unclassified": 45, "residential": 30, "living_street": 10, "service": 20, "track": 15,
}
DEFAULT_ROAD_SPEED = 50

ACCELERATION = 1.6      # m/s², normal acceleration
BRAKING = 2.0           # m/s², comfortable braking used to plan ahead
MAX_BRAKING = 3.5       # m/s², the most the driver brakes in normal traffic
LOOKAHEAD_M = 500       # how far ahead the driver "looks" for curves, limits and stops
CORNER_WINDOW_M = 15    # distance before/after a vertex used to measure the curve
LATERAL_ACCELERATION = 2.5   # m/s², comfortable sideways acceleration in curves
MIN_ROAD_SPEED_SHARE = 0.8   # never slower than 80 % of the typical speed of the road class

# Traffic stops per km by road speed (km/h): (max road speed, stops per km)
STOP_RATES = [(40, 1.0), (60, 0.45), (80, 0.15)]
STOP_DURATION_S = (8, 50)

IDLE_BEFORE_START_S = (30, 90)
IDLE_AFTER_ARRIVAL_S = (15, 45)


def expand_details(intervals, segments):
    """GraphHopper [[from, to, value], ...] -> one value per route segment (None if unknown)."""
    values = [None] * segments
    for start, end, value in intervals or []:
        for i in range(start, min(end, segments)):
            values[i] = value
    return values


def corner_speed(angle_deg, arc_m=2 * CORNER_WINDOW_M):
    """
    Highest comfortable speed (m/s) through a curve that turns `angle_deg` over `arc_m`.
    Radius r = arc / angle; v = sqrt(a_lateral * r). A 90° junction turn gives ~25 km/h,
    a gentle motorway curve still allows 120+.
    """
    angle = math.radians(angle_deg)
    if angle < 1e-3:
        return None
    return math.sqrt(LATERAL_ACCELERATION * arc_m / angle)


class Trip:
    """One drive along a route. Call step(dt) repeatedly, read state()."""

    def __init__(self, points, profile=None, max_speed_kmh=90, realistic=True, start_m=0.0, rng=None):
        self.rng = rng or random.Random()
        self.points = points
        self.cum = geo.cumulative_distances(points)
        self.total = self.cum[-1]
        self.realistic = realistic
        self.max_speed = max_speed_kmh / 3.6

        self.s = min(max(start_m, 0.0), self.total)   # distance driven on the route (m)
        self.v = 0.0                                    # speed (m/s)
        self.seg = 0                                    # current segment index
        self._advance_segment()

        self.seg_speed = self._segment_speeds(profile)
        self.corner_v = self._corner_speeds() if realistic else {}
        self.stops = self._traffic_stops() if realistic else []
        self.driver = self.rng.uniform(0.92, 1.04) if realistic else 1.0   # this driver's pace
        self.wobble = 1.0                                                  # small speed variation
        self.override = None    # [speed m/s, seconds left] while an "overspeed" event is active

        # phases: idle_start -> driving <-> traffic_stop -> idle_end -> done
        if realistic:
            self.phase, self.timer = "idle_start", self.rng.uniform(*IDLE_BEFORE_START_S)
        else:
            self.phase, self.timer = "driving", 0.0

    # --- Route preparation -------------------------------------------------

    def _segment_speeds(self, profile):
        """Target cruising speed (m/s) for every segment."""
        n = len(self.points) - 1
        if not self.realistic:
            return [self.max_speed] * n
        profile = profile or {}
        average = expand_details(profile.get("average_speed"), n)
        limit = expand_details(profile.get("max_speed"), n)
        road = expand_details(profile.get("road_class"), n)
        speeds = []
        for i in range(n):
            typical = ROAD_CLASS_SPEED.get(road[i], DEFAULT_ROAD_SPEED)
            # GraphHopper's average already includes some slow-downs that we simulate
            # ourselves (stops, curves), so don't go far below the typical road speed.
            kmh = max(average[i] or typical, MIN_ROAD_SPEED_SHARE * typical)
            if limit[i]:
                kmh = min(kmh, limit[i])
            speeds.append(min(kmh / 3.6, self.max_speed))
        return speeds

    def _point_at(self, index, distance_back):
        """Point `distance_back` meters before (negative: after) route vertex `index`."""
        target = self.cum[index] - distance_back
        target = max(0.0, min(self.total, target))
        i = index
        while i > 0 and self.cum[i] > target:
            i -= 1
        while i < len(self.points) - 2 and self.cum[i + 1] < target:
            i += 1
        a, b = self.points[i], self.points[i + 1]
        seg_len = self.cum[i + 1] - self.cum[i]
        t = 0.0 if seg_len == 0 else (target - self.cum[i]) / seg_len
        return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t

    def _corner_speeds(self):
        """{vertex index: max speed m/s} for every noticeable curve."""
        corners = {}
        for j in range(1, len(self.points) - 1):
            before = self._point_at(j, CORNER_WINDOW_M)
            after = self._point_at(j, -CORNER_WINDOW_M)
            vertex = self.points[j]
            h_in = geo.bearing_deg(before[0], before[1], vertex[0], vertex[1])
            h_out = geo.bearing_deg(vertex[0], vertex[1], after[0], after[1])
            angle = abs((h_out - h_in + 180) % 360 - 180)
            v = corner_speed(angle)
            if v is not None and v < 150 / 3.6:
                corners[j] = v
        return corners

    def _traffic_stops(self):
        """Random stop positions (traffic lights, junctions), more on slow town roads."""
        stops = []
        for i, speed in enumerate(self.seg_speed):
            seg_len = self.cum[i + 1] - self.cum[i]
            kmh = speed * 3.6
            rate = next((r for max_kmh, r in STOP_RATES if kmh <= max_kmh), 0)
            if self.rng.random() < rate * seg_len / 1000:
                stops.append(self.cum[i] + self.rng.random() * seg_len)
        # Sharp turns in town: often a junction where you have to stop or give way.
        for j, v in self.corner_v.items():
            if v * 3.6 <= 25 and self.seg_speed[j] * 3.6 <= 70 and self.rng.random() < 0.35:
                stops.append(self.cum[j])
        stops = sorted(p for p in stops if 40 < p < self.total - 40 and p > self.s)
        merged = []
        for p in stops:                     # no two stops closer than 80 m
            if not merged or p - merged[-1] > 80:
                merged.append(p)
        return merged

    # --- Simulation --------------------------------------------------------

    def _advance_segment(self):
        while self.seg < len(self.points) - 2 and self.cum[self.seg + 1] <= self.s:
            self.seg += 1

    def _target_speed(self, dt):
        """Highest speed allowed now, so the driver can still slow down for everything ahead."""
        cruise = self.seg_speed[self.seg] * self.driver * self.wobble
        if self.override:
            cruise = self.override[0]
        target = cruise

        def limit_ahead(position, speed):
            # Distance left after this step, at the current speed - plan one step ahead.
            d = position - self.s - self.v * dt
            return math.sqrt(speed * speed + 2 * BRAKING * max(d, 0))

        j = self.seg + 1
        while j < len(self.points) and self.cum[j] - self.s < LOOKAHEAD_M:
            if j < len(self.seg_speed) and not self.override:
                target = min(target, limit_ahead(self.cum[j], self.seg_speed[j] * self.driver))
            if j in self.corner_v:
                target = min(target, limit_ahead(self.cum[j], self.corner_v[j]))
            j += 1
        if self.stops and self.stops[0] - self.s < LOOKAHEAD_M:
            target = min(target, limit_ahead(self.stops[0], 0))
        target = min(target, limit_ahead(self.total, 0))
        # Creep the last meters to a stop instead of stopping short.
        return max(target, 1.0) if self.realistic else target

    def step(self, dt):
        """Advance the simulation by dt seconds."""
        if self.phase in ("idle_start", "traffic_stop", "idle_end"):
            self.timer -= dt
            if self.timer <= 0:
                self.phase = "done" if self.phase == "idle_end" else "driving"
            return
        if self.phase != "driving":
            return

        if self.override:
            self.override[1] -= dt
            if self.override[1] <= 0:
                self.override = None
        if self.realistic:
            self.wobble = min(1.05, max(0.95, self.wobble + self.rng.gauss(0, 0.004)))
            target = self._target_speed(dt)
            if self.v < target:
                self.v = min(target, self.v + ACCELERATION * dt)
            else:
                self.v = max(target, self.v - MAX_BRAKING * dt)
        else:
            self.v = self.override[0] if self.override else self.max_speed

        new_s = self.s + self.v * dt
        if self.stops and new_s >= self.stops[0]:
            # Reached a traffic stop: stand still for a while.
            self.s, self.v = self.stops.pop(0), 0.0
            self.phase, self.timer = "traffic_stop", self.rng.uniform(*STOP_DURATION_S)
        elif new_s >= self.total:
            self.s, self.v = self.total, 0.0
            if self.realistic:
                self.phase, self.timer = "idle_end", self.rng.uniform(*IDLE_AFTER_ARRIVAL_S)
            else:
                self.phase = "done"
        else:
            self.s = new_s
        self._advance_segment()

    # --- Events --------------------------------------------------------------

    def brake_hard(self):
        self.v = max(0.0, self.v - 7.0)

    def accelerate_hard(self):
        self.v = min(self.v + 4.0, self.seg_speed[self.seg] * 1.3)

    def overspeed(self, speed_kmh, seconds):
        self.override = [speed_kmh / 3.6, seconds]

    def crash(self):
        self.v = 0.0
        self.phase = "crashed"

    # --- State ---------------------------------------------------------------

    @property
    def finished(self):
        return self.phase == "done"

    def state(self):
        """True position and motion (before GPS noise)."""
        a, b = self.points[self.seg], self.points[self.seg + 1]
        seg_len = self.cum[self.seg + 1] - self.cum[self.seg]
        t = 0.0 if seg_len == 0 else min(1.0, (self.s - self.cum[self.seg]) / seg_len)
        ele_a = a[2] if len(a) > 2 and a[2] is not None else 0.0
        ele_b = b[2] if len(b) > 2 and b[2] is not None else 0.0
        return {
            "lat": a[0] + (b[0] - a[0]) * t,
            "lon": a[1] + (b[1] - a[1]) * t,
            "altitude": ele_a + (ele_b - ele_a) * t,
            "heading": geo.bearing_deg(a[0], a[1], b[0], b[1]),
            "speed_kmh": self.v * 3.6,
            "ignition": self.phase not in ("done",),
            "moving": self.v > 0.5,
            "distance_m": self.s,
            "phase": self.phase,
        }


class GpsReceiver:
    """What the tracker's GPS reports: small correlated position noise, satellites, HDOP."""

    NOISE_M = 2.5          # typical position error (m)
    CORRELATION = 0.95     # the error changes slowly, it doesn't jump every second

    def __init__(self, realistic=True, rng=None):
        self.realistic = realistic
        self.rng = rng or random.Random()
        self.err_n = self.err_e = 0.0
        self.satellites = self.rng.randint(9, 13)
        self.hdop = self.rng.uniform(0.7, 1.1)
        self.frozen = None      # last fix while standing ("static navigation")

    def fix(self, state):
        if not self.realistic:
            return {**state, "satellites": 10, "hdop": 1.0, "pdop": 1.5}

        # Satellites and HDOP drift slowly.
        if self.rng.random() < 0.1:
            self.satellites = min(15, max(6, self.satellites + self.rng.choice((-1, 1))))
        self.hdop = min(2.5, max(0.6, self.hdop + self.rng.gauss(0, 0.03)))

        # Static navigation: like Teltonika's default, the position is frozen while
        # the vehicle stands still, so parked tracks don't "wander".
        if not state["moving"]:
            if self.frozen is None:
                self.frozen = self._noisy(state)
            return {**state, **self.frozen, "speed_kmh": 0.0,
                    "satellites": self.satellites, "hdop": self.hdop, "pdop": self.hdop * 1.4}
        self.frozen = None

        noisy = self._noisy(state)
        speed = max(0.0, state["speed_kmh"] + self.rng.gauss(0, 0.8))
        return {**state, **noisy, "speed_kmh": speed,
                "satellites": self.satellites, "hdop": self.hdop, "pdop": self.hdop * 1.4}

    def _noisy(self, state):
        sigma = self.NOISE_M * self.hdop
        k = math.sqrt(1 - self.CORRELATION ** 2)
        self.err_n = self.CORRELATION * self.err_n + self.rng.gauss(0, sigma * k)
        self.err_e = self.CORRELATION * self.err_e + self.rng.gauss(0, sigma * k)
        lat = state["lat"] + self.err_n / 111_320
        lon = state["lon"] + self.err_e / (111_320 * math.cos(math.radians(state["lat"])))
        heading = (state["heading"] + self.rng.gauss(0, 1.5)) % 360
        return {"lat": lat, "lon": lon, "heading": heading}


class Parked:
    """
    A vehicle standing at one end of the route with the ignition off (scenarios).
    Same interface as Trip, so the engine treats driving and parking alike.
    """

    def __init__(self, point, heading=0.0, distance_m=0.0, realistic=True):
        self.point = point
        self.heading = heading
        self.s = distance_m
        self.v = 0.0
        self.realistic = realistic
        self.override = None
        self.phase = "parked"

    @property
    def finished(self):
        return False

    def step(self, dt):
        pass

    # Driving events do nothing while parked (towing, power cut, ... still work - see engine).
    def brake_hard(self):
        pass

    def accelerate_hard(self):
        pass

    def overspeed(self, speed_kmh, seconds):
        pass

    def crash(self):
        self.phase = "crashed"

    def state(self):
        return {
            "lat": self.point[0], "lon": self.point[1],
            "altitude": self.point[2] if len(self.point) > 2 and self.point[2] is not None else 0.0,
            "heading": self.heading, "speed_kmh": 0.0,
            "ignition": False, "moving": False,
            "distance_m": self.s, "phase": self.phase,
        }
