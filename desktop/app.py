"""
Desktop app entry point.

    python -m desktop
"""
import sys

from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWidgets import QApplication

from core import database
from core.engine import reset_stale_statuses

from . import settings
from .icons import app_icon
from .main_window import GITHUB_URL, MainWindow


def main():
    app = QApplication(sys.argv)
    # Names used by QSettings and for the app data folder.
    app.setOrganizationName("mb_gps_emulator")
    app.setApplicationName("MB GPS Emulator")
    app.setWindowIcon(app_icon())

    # Identify the app to the OpenStreetMap tile servers (their usage policy asks for it).
    profile = QWebEngineProfile.defaultProfile()
    profile.setHttpUserAgent(f"{profile.httpUserAgent()} MB-GPS-Emulator/1.0 (+{GITHUB_URL})")

    settings.apply_to_config()
    database.run_migrations()
    reset_stale_statuses()

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
