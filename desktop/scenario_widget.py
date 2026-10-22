"""
"Scenario" section of the device dialog: when the device drives (see core/scenarios.py).
"""
from PySide6.QtCore import QTime, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QTimeEdit, QWidget,
)

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
TYPES = [
    ("Manual – drive once (Start / Stop)", None),
    ("Fixed times – e.g. to work and back", "times"),
    ("Every N minutes", "interval"),
    ("Back and forth continuously", "loop"),
]
DEFAULTS = {"times": ["07:30", "16:30"], "every_min": 60, "from": "06:00", "to": "22:00",
            "round_trip": True, "dwell_min": 5, "jitter_min": 5, "days": [0, 1, 2, 3, 4]}


def _time(text):
    hours, minutes = (int(x) for x in text.split(":"))
    return QTime(23, 59) if hours == 24 else QTime(hours, minutes)


class ScenarioWidget(QWidget):
    changed = Signal()   # emitted (debounced) whenever the user edits the scenario

    def __init__(self, parent=None):
        super().__init__(parent)
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)

        self.type = QComboBox()
        for label, value in TYPES:
            self.type.addItem(label, value)
        self.times = QLineEdit()
        self.times.setPlaceholderText("07:30, 16:30")
        self.every = self._minutes(5, 1440)
        self.from_time, self.to_time = QTimeEdit(), QTimeEdit()
        for edit in (self.from_time, self.to_time):
            edit.setDisplayFormat("HH:mm")
        window = QHBoxLayout()
        window.addWidget(self.from_time)
        window.addWidget(QLabel("–"))
        window.addWidget(self.to_time)
        self.round_trip = QCheckBox("Round trip (A → B, wait, back to A)")
        self.dwell = self._minutes(0, 720)
        self.days = [QCheckBox(d) for d in DAYS]
        days_row = QHBoxLayout()
        for box in self.days:
            days_row.addWidget(box)
        self.jitter = self._minutes(0, 60)
        self.preview = QLabel()
        self.preview.setWordWrap(True)

        self.form.addRow(self.type)
        self.form.addRow("Departures", self.times)
        self.form.addRow("Every", self.every)
        self.form.addRow("Active", window)
        self.form.addRow("", self.round_trip)
        self.form.addRow("Wait at end", self.dwell)
        self.form.addRow("Days", days_row)
        self.form.addRow("Random ±", self.jitter)
        self.form.addRow(self.preview)

        # Which rows belong to which scenario type
        self._rows = {
            self.times: {"times"}, self.every: {"interval"}, window: {"interval", "loop"},
            self.round_trip: {"interval"}, self.dwell: {"interval", "loop"},
            days_row: {"times", "interval", "loop"}, self.jitter: {"times", "interval"},
            self.preview: {"times", "interval", "loop"},
        }

        # Recalculate the preview 300 ms after the last change.
        self._timer = QTimer(self, singleShot=True, interval=300, timeout=self.changed.emit)
        self.type.currentIndexChanged.connect(self._type_changed)
        self.times.textChanged.connect(self._timer.start)
        for widget in (self.every, self.dwell, self.jitter):
            widget.valueChanged.connect(self._timer.start)
        for widget in (self.from_time, self.to_time):
            widget.timeChanged.connect(self._timer.start)
        for box in [self.round_trip, *self.days]:
            box.toggled.connect(self._timer.start)

        self.load(None)

    @staticmethod
    def _minutes(minimum, maximum):
        box = QSpinBox()
        box.setRange(minimum, maximum)
        box.setSuffix(" min")
        return box

    def _type_changed(self):
        kind = self.type.currentData()
        for row, kinds in self._rows.items():
            self.form.setRowVisible(row, kind in kinds)
        self._timer.start()

    def load(self, scenario):
        s = {**DEFAULTS, **(scenario or {})}
        self.type.setCurrentIndex(max(0, self.type.findData(scenario["type"] if scenario else None)))
        self.times.setText(", ".join(s["times"]) if isinstance(s["times"], list) else s["times"])
        self.every.setValue(s["every_min"])
        self.from_time.setTime(_time(s["from"]))
        self.to_time.setTime(_time(s["to"]))
        self.round_trip.setChecked(s["round_trip"])
        self.dwell.setValue(s["dwell_min"])
        self.jitter.setValue(s["jitter_min"])
        for i, box in enumerate(self.days):
            box.setChecked(i in s["days"])
        self._type_changed()

    def value(self):
        """The scenario as stored on the device (None = manual)."""
        kind = self.type.currentData()
        if not kind:
            return None
        scenario = {"type": kind, "days": [i for i, box in enumerate(self.days) if box.isChecked()],
                    "jitter_min": self.jitter.value()}
        if kind == "times":
            scenario["times"] = self.times.text()
        else:
            scenario["from"] = self.from_time.time().toString("HH:mm")
            scenario["to"] = self.to_time.time().toString("HH:mm")
            scenario["dwell_min"] = self.dwell.value()
        if kind == "interval":
            scenario["every_min"] = self.every.value()
            scenario["round_trip"] = self.round_trip.isChecked()
        return scenario

    def show_preview(self, result=None, error=None):
        if error:
            self.preview.setText(f'<span style="color:#c62828">{error}</span>')
            return
        lines = [f"<span style='color:#666'>Next departures ({result['timezone']}):</span>"]
        lines += [f"{t['at']} &nbsp; {t['legs']}" for t in result["trips"]]
        lines += [f"<span style='color:#b8860b'>⚠ {w}</span>" for w in result["warnings"]]
        self.preview.setText("<br>".join(lines))
