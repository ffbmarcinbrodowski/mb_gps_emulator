"""
OBD settings of a device (see core/obd.py): codec, VIN, mileage, service, fuel, fault codes.
Edited in its own window like the scenario; the device dialog keeps the result until the device is saved.
"""
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout,
)

from core import services

DEFAULTS = {"enabled": False, "codec": "8", "vin": "", "odometer_km": 50000, "service_km": 15000,
            "fuel_pct": 70, "tank_l": 55, "dtcs": [], "weak_battery": False}


def summary(obd, codec="8"):
    """One-line description, e.g. 'On · 123,456 km · service in 2,300 km · fuel 70 %'."""
    if not obd or not obd["enabled"]:
        return f"Off · Codec {'8 Extended' if codec == '8E' else '8'}"
    service = f"service in {obd['service_km']:,.0f} km" if obd["service_km"] > 0 else "service due"
    parts = ["On", f"{obd['odometer_km']:,.0f} km", service, f"fuel {obd['fuel_pct']:.0f} %"]
    if obd["dtcs"]:
        parts.append(f"{len(obd['dtcs'])} fault code{'s' if len(obd['dtcs']) > 1 else ''}")
    if obd["weak_battery"]:
        parts.append("weak battery")
    return " · ".join(parts)


class ObdDialog(QDialog):
    def __init__(self, parent, obd):
        super().__init__(parent)
        self.setWindowTitle("OBD (Teltonika FMB003)")
        self.resize(480, 380)
        self.obd = obd
        o = {**DEFAULTS, **(obd or {})}

        intro = QLabel("OBD values are sent with Codec 8 Extended and change with the drive "
                       "(mileage, fuel, coolant, RPM). In Traccar set the device model to <b>FMB003</b>.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #666;")

        self.enabled = QCheckBox("Send OBD data")
        self.enabled.setChecked(o["enabled"])
        self.codec = QComboBox()
        self.codec.addItem("Codec 8", "8")
        self.codec.addItem("Codec 8 Extended", "8E")
        self.codec.setCurrentIndex(1 if o["codec"] == "8E" else 0)
        self.vin = QLineEdit(o["vin"] or "")
        self.vin.setMaxLength(17)
        vin_btn = QPushButton("Generate")
        vin_btn.clicked.connect(lambda: self.vin.setText(services.new_vin()))
        vin_row = QHBoxLayout()
        vin_row.addWidget(self.vin, 1)
        vin_row.addWidget(vin_btn)
        self.odometer = self._spin(0, 2_000_000, o["odometer_km"], " km")
        self.service = self._spin(0, 100_000, o["service_km"], " km")
        self.fuel = self._spin(0, 100, o["fuel_pct"], " %")
        self.tank = self._spin(20, 200, o["tank_l"], " l")
        fuel_row = QHBoxLayout()
        fuel_row.addWidget(self.fuel)
        fuel_row.addWidget(QLabel("of"))
        fuel_row.addWidget(self.tank)
        self.dtcs = QLineEdit(", ".join(o["dtcs"]))
        self.dtcs.setPlaceholderText("none, e.g. P0420, P0171")
        self.weak_battery = QCheckBox("Weak battery (low voltage while parked)")
        self.weak_battery.setChecked(o["weak_battery"])
        self.error = QLabel()
        self.error.setStyleSheet("color: #c0392b;")
        self.error.setWordWrap(True)

        form = QFormLayout()
        form.addRow(self.enabled)
        form.addRow("Codec", self.codec)
        form.addRow("VIN", vin_row)
        form.addRow("Mileage", self.odometer)
        form.addRow("Service in", self.service)
        form.addRow("Fuel", fuel_row)
        form.addRow("Fault codes", self.dtcs)
        form.addRow("", self.weak_battery)

        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addStretch()
        layout.addWidget(buttons)

        self.enabled.toggled.connect(self._enabled_changed)
        self._enabled_changed(self.enabled.isChecked())

    @staticmethod
    def _spin(minimum, maximum, value, suffix):
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(round(value))
        spin.setSuffix(suffix)
        spin.setGroupSeparatorShown(True)
        return spin

    def _enabled_changed(self, enabled):
        # OBD needs Codec 8 Extended (ids above 255, VIN and fault codes).
        if enabled:
            self.codec.setCurrentIndex(1)
        self.codec.setEnabled(not enabled)

    def _apply(self):
        try:
            self.obd = services.parse_obd({
                "enabled": self.enabled.isChecked(),
                "codec": self.codec.currentData(),
                "vin": self.vin.text(),
                "odometer_km": self.odometer.value(),
                "service_km": self.service.value(),
                "fuel_pct": self.fuel.value(),
                "tank_l": self.tank.value(),
                "dtcs": self.dtcs.text(),
                "weak_battery": self.weak_battery.isChecked(),
            })
        except services.ServiceError as err:
            self.error.setText(err.message)
            return
        self.accept()
