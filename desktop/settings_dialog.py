"""Settings dialog: GraphHopper API key, time zone and notifications."""
from zoneinfo import ZoneInfo, available_timezones

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QVBoxLayout,
)

from core import config

from . import settings


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(560, 260)

        self.key = QLineEdit(config.GRAPHHOPPER_API_KEY)
        self.key.setEchoMode(QLineEdit.Password)
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(show)

        link = QLabel('No key yet? Create a free one in the '
                      '<a href="https://graphhopper.com/dashboard/">GraphHopper dashboard</a> '
                      '(Sign up → API Keys → Add API Key).')
        link.setOpenExternalLinks(True)
        link.setWordWrap(True)

        self.url = QLineEdit(config.GRAPHHOPPER_ROUTE_URL)
        self.url.setPlaceholderText("https://graphhopper.com/api/1/route")

        self.timezone = QComboBox()
        self.timezone.setEditable(True)
        self.timezone.addItems(sorted(available_timezones()))
        self.timezone.setCurrentText(config.TIMEZONE)
        self.timezone.setToolTip("Scenario times (e.g. 07:30) are local times of this zone.\n"
                                 "Running devices use the new zone after Stop / Start.")

        self.notifications = QCheckBox("Show system notifications (route finished, connection errors)")
        self.notifications.setChecked(settings.notifications_enabled())

        db_path = QLabel(f"<small>Database: {config.DATABASE_PATH}</small>")
        db_path.setWordWrap(True)
        db_path.setStyleSheet("color: #666;")

        form = QFormLayout()
        form.addRow("GraphHopper API key", key_row)
        form.addRow("", link)
        form.addRow("Routing URL", self.url)
        form.addRow("Time zone", self.timezone)
        form.addRow("", self.notifications)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(db_path)
        layout.addStretch()
        layout.addWidget(buttons)

    def _save(self):
        zone = self.timezone.currentText().strip()
        try:
            ZoneInfo(zone)
        except (ValueError, KeyError):
            QMessageBox.warning(self, "Settings", f"Unknown time zone: {zone}")
            return
        settings.save_timezone(zone)
        settings.save_graphhopper(self.key.text().strip(), self.url.text().strip())
        settings.set_notifications_enabled(self.notifications.isChecked())
        self.accept()
