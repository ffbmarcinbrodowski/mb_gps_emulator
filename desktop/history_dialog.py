"""
"Generate history" dialog: past data from the device's scenario (see core/history.py).
The job runs in the emulator's background thread; this dialog only shows its progress.
"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QSpinBox, QVBoxLayout,
)

from core import history, services

from .tasks import error_text


class HistoryDialog(QDialog):
    def __init__(self, parent, db, emulator, device):
        super().__init__(parent)
        self.db = db
        self.emulator = emulator
        self.device = device
        self.setWindowTitle(f"Generate history: {device['name']}")
        self.resize(520, 330)

        intro = QLabel(
            "Simulates the device's scenario in the past: the same trips, traffic stops, parking and "
            "GPS noise as live emulation. The records are sent with their real past timestamps, like "
            "a tracker that empties its buffer.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #555;")

        self.days = QSpinBox()
        self.days.setRange(1, history.MAX_DAYS)
        self.days.setValue(7)
        self.days.setSuffix(" days, until now")
        form = QFormLayout()
        form.addRow("Days back", self.days)

        warning = QLabel("Use it for a device that has no data in Traccar for this period (e.g. a new "
                         "IMEI), otherwise the tracks overlap.")
        warning.setWordWrap(True)
        warning.setStyleSheet("background: #fff3cd; color: #664d03; padding: 6px; border-radius: 4px;")

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.hide()
        self.status = QLabel()
        self.status.setWordWrap(True)

        self.start_btn = QPushButton("Generate")
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self._start)
        self.cancel_btn = QPushButton("Cancel generation")
        self.cancel_btn.clicked.connect(lambda: self.emulator.cancel_history(self.device["id"]))
        self.cancel_btn.hide()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch()
        for b in (close_btn, self.cancel_btn, self.start_btn):
            buttons.addWidget(b)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(QLabel(f"Scenario: {_scenario_text(device['scenario'])}"))
        layout.addLayout(form)
        layout.addWidget(warning)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addStretch()
        layout.addLayout(buttons)

        self.timer = QTimer(self, interval=500, timeout=self._poll)
        self._poll()   # a job may still be running from before

    def _start(self):
        try:
            services.start_history(self.db, self.emulator, self.device["id"], self.days.value())
        except services.ServiceError as err:
            QMessageBox.warning(self, "History", error_text(err))
            return
        self._poll()

    def _poll(self):
        s = self.emulator.history_status(self.device["id"])
        running = bool(s) and s["state"] == "running"
        self.start_btn.setEnabled(not running)
        self.cancel_btn.setVisible(running)
        if running and not self.timer.isActive():
            self.timer.start()
        elif not running:
            self.timer.stop()
        if not s:
            return
        self.progress.show()
        self.progress.setValue(int(s["progress"] * 10))
        text = {
            "running": f"Generating… {s['progress']}% · {s['sent']:,} records sent",
            "done": f"Done: {s['sent']:,} records sent",
            "cancelled": f"Cancelled after {s['sent']:,} records",
            "failed": f"Failed after {s['sent']:,} records: {s['error']}",
        }[s["state"]]
        self.status.setText(f"{text}<br><span style='color:#666'>{s['period']}</span>")

    def done(self, result):
        self.timer.stop()
        super().done(result)


def _scenario_text(sc):
    if sc["type"] == "times":
        return "fixed times " + ", ".join(sc["times"])
    if sc["type"] == "interval":
        return f"every {sc['every_min']} min {sc['from']}–{sc['to']}" + (" round trip" if sc.get("round_trip") else "")
    return f"back and forth {sc['from']}–{sc['to']}"
