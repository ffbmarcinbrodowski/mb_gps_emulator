"""
Scenarios: WHEN an emulated vehicle drives its route.

A device without a scenario drives A -> B once (manual). With a scenario it is
"armed": it parks at one end of the route and drives according to the plan,
day after day, until it is stopped. The configuration is stored as JSON:

    {"type": "times", "times": ["07:30", "16:30"], "days": [0, 1, 2, 3, 4], "jitter_min": 5}
        departures at fixed times (e.g. to work and back home)

    {"type": "interval", "every_min": 60, "from": "06:00", "to": "22:00",
     "round_trip": true, "dwell_min": 5, "days": [0, 1, 2, 3, 4, 5, 6], "jitter_min": 2}
        a departure every N minutes inside a time window; with round_trip the
        vehicle drives A -> B, waits dwell_min and comes back B -> A

    {"type": "loop", "from": "06:00", "to": "22:00", "dwell_min": 5, "days": [...]}
        back and forth all the time (inside the window), waiting dwell_min at each end

Days: 0 = Monday ... 6 = Sunday. Times are local times of the app time zone.
Every departure drives to the other end of the route (A -> B, then B -> A),
except round-trip intervals, which always start and end at A.

Everything here is pure calculation: the current time is a parameter, so the
plan can be tested for any day without waiting.
"""
import random
from datetime import datetime, time, timedelta

TYPES = ("times", "interval", "loop")
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def leg_seconds(route_duration_s):
    """Expected duration of one drive: GraphHopper time + traffic stops + idling at both ends."""
    return (route_duration_s or 0) * 1.15 + 120


def parse_hhmm(text, allow_24=False):
    """'07:30' -> (7, 30). Raises ValueError for invalid times."""
    hours, minutes = (int(x) for x in str(text).strip().split(":"))
    if allow_24 and (hours, minutes) == (24, 0):
        return 24, 0
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(text)
    return hours, minutes


def _at(day, hhmm, tz, allow_24=False):
    """Local datetime for a date and 'HH:MM' ('24:00' = midnight at the end of the day)."""
    hours, minutes = parse_hhmm(hhmm, allow_24)
    if hours == 24:
        return datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=tz)
    return datetime.combine(day, time(hours, minutes), tzinfo=tz)


def _window(cfg, day, tz):
    return _at(day, cfg.get("from", "00:00"), tz), _at(day, cfg.get("to", "24:00"), tz, allow_24=True)


def _jitter(cfg, planned, seed):
    """Random shift of a departure - always the same for the same device and departure."""
    minutes = cfg.get("jitter_min", 0)
    if not minutes:
        return timedelta(0)
    rng = random.Random(f"{seed}|{planned.isoformat()}")
    return timedelta(minutes=rng.uniform(-minutes, minutes))


def _planned_times(cfg, day, tz):
    """Departures of one day before jitter."""
    if cfg["type"] == "times":
        return [_at(day, t, tz) for t in cfg["times"]]
    if cfg["type"] == "interval":
        start, end = _window(cfg, day, tz)
        result, t = [], start
        while t < end:
            result.append(t)
            t += timedelta(minutes=cfg["every_min"])
        return result
    return []


def departures(cfg, start, end, seed=0):
    """All departures in [start, end) for 'times' and 'interval' scenarios (sorted)."""
    tz = start.tzinfo
    result = []
    day = (start - timedelta(days=1)).date()     # jitter can move yesterday's late trip into range
    while day <= end.date():
        if day.weekday() in cfg.get("days", ALL_DAYS):
            for planned in _planned_times(cfg, day, tz):
                actual = planned + _jitter(cfg, planned, seed)
                if start <= actual < end:
                    result.append(actual)
        day += timedelta(days=1)
    return sorted(result)


def in_window(cfg, moment):
    """Is a 'loop' scenario active at this moment?"""
    if moment.weekday() not in cfg.get("days", ALL_DAYS):
        return False
    start, end = _window(cfg, moment.date(), moment.tzinfo)
    return start <= moment < end


def _next_window_start(cfg, after):
    day = after.date()
    for _ in range(8):
        if day.weekday() in cfg.get("days", ALL_DAYS):
            start, _end = _window(cfg, day, after.tzinfo)
            if start >= after:
                return start
        day += timedelta(days=1)
    return None


def next_departure(cfg, now, seed=0):
    """When does the vehicle leave next (None if never, e.g. no days selected)?"""
    if cfg["type"] == "loop":
        return now if in_window(cfg, now) else _next_window_start(cfg, now)
    upcoming = departures(cfg, now, now + timedelta(days=8), seed)
    return upcoming[0] if upcoming else None


def initial_position(cfg, now, leg_s, seed=0):
    """
    Where should the vehicle be when the scenario is armed at `now`?

    Returns (location, drive_now): location "A" or "B" is where it stands;
    drive_now=True means a trip should be running right now, so it leaves at once.
    leg_s is the expected duration of one drive (incl. idling).
    """
    leg = timedelta(seconds=leg_s)
    if cfg["type"] == "loop":
        return "A", in_window(cfg, now)

    if cfg["type"] == "interval" and cfg.get("round_trip"):
        recent = departures(cfg, now - timedelta(days=1), now, seed)
        if recent:
            left = recent[-1]
            if now < left + leg:
                return "A", True                     # on the way out
            if now < left + 2 * leg + timedelta(minutes=cfg.get("dwell_min", 0)):
                return "B", True                     # on the way back
        return "A", False

    # Alternating A -> B -> A ...: count today's departures so far (the day starts at A).
    today = datetime.combine(now.date(), time(0, 0), tzinfo=now.tzinfo)
    done = departures(cfg, today, now, seed)
    if done and now < done[-1] + leg:
        # The last trip should still be on its way: start it now from its origin.
        return ("A" if (len(done) - 1) % 2 == 0 else "B"), True
    return ("A" if len(done) % 2 == 0 else "B"), False


def preview(cfg, now, leg_s, seed=0, count=4):
    """
    Upcoming trips for the UI: [(departure datetime, "A → B"), ...] and a list of warnings.
    """
    warnings = []
    leg = timedelta(seconds=leg_s)
    location, drive_now = initial_position(cfg, now, leg_s, seed)
    other = {"A": "B", "B": "A"}
    trips = []

    if cfg["type"] == "loop":
        moment = next_departure(cfg, now, seed)
        if moment:
            trips.append((moment, f"{location} ⇄ {other[location]} continuously until {cfg.get('to', '24:00')}"))
    elif cfg["type"] == "interval" and cfg.get("round_trip"):
        for moment in departures(cfg, now, now + timedelta(days=8), seed)[:count]:
            trips.append((moment, "A → B → A"))
        every = timedelta(minutes=cfg["every_min"])
        if 2 * leg + timedelta(minutes=cfg.get("dwell_min", 0)) > every:
            warnings.append("A round trip takes longer than the interval - some departures will be skipped.")
    else:
        if drive_now:
            trips.append((now, f"{location} → {other[location]} (now)"))
            location = other[location]
        for moment in departures(cfg, now, now + timedelta(days=8), seed)[:count]:
            trips.append((moment, f"{location} → {other[location]}"))
            location = other[location]
        moments = departures(cfg, now, now + timedelta(days=2), seed)
        if any(b - a < leg for a, b in zip(moments, moments[1:])):
            warnings.append("Some departures are closer together than one trip takes - "
                            "a departure is skipped while the vehicle is still driving.")
    if not trips:
        warnings.append("No upcoming departures - check the days and times.")
    return trips[:count], warnings
