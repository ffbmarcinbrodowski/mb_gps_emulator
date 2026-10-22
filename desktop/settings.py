"""
User settings of the desktop app, stored with QSettings
(macOS: ~/Library/Preferences, Windows: registry, Linux: ~/.config).
"""
import os

from PySide6.QtCore import QSettings, QStandardPaths

from core import config

TIMEZONE = "app/timezone"
GRAPHHOPPER_KEY = "graphhopper/api_key"
GRAPHHOPPER_URL = "graphhopper/route_url"
NOTIFICATIONS = "ui/notifications"


def default_db_path():
    """Desktop database lives in the user's app data folder, separate from the web app."""
    folder = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    return os.path.join(folder, "emulator.db")


def apply_to_config():
    """Copy settings into core.config (environment variables still win for the DB path)."""
    if "EMULATOR_DB" not in os.environ:
        config.DATABASE_PATH = default_db_path()

    s = QSettings()
    # Time zone for scenarios: EMULATOR_TIMEZONE > saved setting > the computer's zone.
    if "EMULATOR_TIMEZONE" not in os.environ and s.value(TIMEZONE, "", str):
        config.TIMEZONE = s.value(TIMEZONE, "", str)
    # A key saved in the app overrides GRAPHHOPPER_API_KEY / graphhopper_api.txt.
    key = s.value(GRAPHHOPPER_KEY, "", str)
    if key:
        config.GRAPHHOPPER_API_KEY = key
    url = s.value(GRAPHHOPPER_URL, "", str)
    if url:
        config.GRAPHHOPPER_ROUTE_URL = url


def save_graphhopper(key, url):
    s = QSettings()
    s.setValue(GRAPHHOPPER_KEY, key)
    s.setValue(GRAPHHOPPER_URL, url)
    config.GRAPHHOPPER_API_KEY = key
    config.GRAPHHOPPER_ROUTE_URL = url or "https://graphhopper.com/api/1/route"


def save_timezone(name):
    QSettings().setValue(TIMEZONE, name)
    config.TIMEZONE = name


def notifications_enabled():
    return QSettings().value(NOTIFICATIONS, True, bool)


def set_notifications_enabled(enabled):
    QSettings().setValue(NOTIFICATIONS, enabled)
