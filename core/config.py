"""
Application configuration.

GraphHopper credentials are read from `graphhopper_api.txt` (project root),
which contains lines like:

    GRAPHHOPPER_ROUTE_URL = 'https://graphhopper.com/api/1/route'
    GRAPHHOPPER_API_KEY = 'your-key'

Environment variables with the same names override the file.
"""
import os
import re

# Project root (one level above core/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- Paths ------------------------------------------------------------------
DATABASE_PATH = os.environ.get("EMULATOR_DB", os.path.join(BASE_DIR, "emulator.db"))
MIGRATIONS_DIR = os.path.join(BASE_DIR, "core", "migrations")
API_KEYS_FILE = os.path.join(BASE_DIR, "graphhopper_api.txt")

# --- Web server -------------------------------------------------------------
# 0.0.0.0 so the app is reachable from an iPhone in the same network.
HOST = os.environ.get("EMULATOR_HOST", "0.0.0.0")
PORT = int(os.environ.get("EMULATOR_PORT", "5057"))

# --- Emulator defaults ------------------------------------------------------
DEFAULT_INTERVAL_S = 5        # how often a device sends its position
DEFAULT_SPEED_KMH = 50        # average speed along the route
SPEED_VARIATION = 0.15        # +/- 15 % random speed noise per tick
TCP_TIMEOUT_S = 5             # connect / ack timeout for the Teltonika client
GRAPHHOPPER_PROFILE = "car"   # car, bike, foot ... (depends on your plan)


def _local_timezone():
    """Name of the computer's time zone, e.g. 'Europe/Berlin' (None if unknown)."""
    try:
        path = os.path.realpath("/etc/localtime")
    except OSError:
        return None
    return path.split("zoneinfo/", 1)[1] if "zoneinfo/" in path else None


# One time zone for the whole app: scenario times ("07:30") are local times of this zone.
# In Docker the computer's zone is UTC, so set EMULATOR_TIMEZONE (e.g. Europe/Berlin).
TIMEZONE = os.environ.get("EMULATOR_TIMEZONE") or _local_timezone() or "UTC"


def _load_key_file(path):
    """Parse simple `NAME = 'value'` lines from the key file."""
    values = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding="utf-8") as f:
        for line in f:
            match = re.match(r"""\s*([A-Z_]+)\s*=\s*['"]?([^'"]*)['"]?\s*$""", line)
            if match:
                values[match.group(1)] = match.group(2).strip()
    return values


_keys = _load_key_file(API_KEYS_FILE)

GRAPHHOPPER_ROUTE_URL = os.environ.get(
    "GRAPHHOPPER_ROUTE_URL",
    _keys.get("GRAPHHOPPER_ROUTE_URL", "https://graphhopper.com/api/1/route"),
)
GRAPHHOPPER_API_KEY = os.environ.get("GRAPHHOPPER_API_KEY", _keys.get("GRAPHHOPPER_API_KEY", ""))
