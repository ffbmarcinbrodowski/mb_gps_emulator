"""
"New device" / "Edit device" dialog: form on the left, route picker map on the right
(same workflow as the web UI: click start, click end, drag to adjust).
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from core import database, models, services

from .map_view import MapView
from .scenario_widget import ScenarioDialog, summary
from .tasks import error_text, run_async

HINT = "Click the map to set the start, then the end point."


def parse_lat_lon(text):
    """'49.2270, 6.9475' -> [49.227, 6.9475] or None."""
    parts = text.replace(";", ",").replace(",", " ").split()
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except (IndexError, ValueError):
        return None
    return [lat, lon] if len(parts) == 2 and abs(lat) <= 90 and abs(lon) <= 180 else None


def fmt_point(point):
    return f"{point[0]:.6f}, {point[1]:.6f}" if point else ""


class DeviceDialog(QDialog):
    saved = Signal(dict)   # the saved device

    def __init__(self, parent, db, emulator, device_id=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)   # frees the map view when closed
        self.db = db
        self.emulator = emulator
        self.device_id = device_id
        self.start = None        # [lat, lon]
        self.end = None
        self.preview = None      # route generated in this dialog
        self.saved_duration_s = 0  # duration of the saved route (edit), for the scenario preview
        self.scenario = None       # scenario saved with the device (None = manual)
        self._route_request = 0  # ignores answers to outdated route requests
        self._alive = True       # background results may arrive after the dialog closed

        self.setWindowTitle("Edit device" if device_id else "New device")
        self.resize(1150, 680)
        self._build_ui()
        self._load_servers()
        if device_id:
            self._load_device(device_id)

    # --- UI ------------------------------------------------------------------

    def _build_ui(self):
        form = QFormLayout()
        self.name = QLineEdit()
        self.imei = QLineEdit()
        self.imei.setMaxLength(20)
        self.imei.setPlaceholderText("15 digits, e.g. 356307042441001")
        self.generate_btn = QPushButton("Generate")
        self.generate_btn.setToolTip("Generate a valid test IMEI that no real device uses")
        self.generate_btn.clicked.connect(lambda: self.imei.setText(services.new_imei(self.db)))
        # Changing the IMEI of an existing device would break its link in Traccar.
        self.generate_btn.setVisible(self.device_id is None)
        imei_row = QHBoxLayout()
        imei_row.addWidget(self.imei, 1)
        imei_row.addWidget(self.generate_btn)
        form.addRow("Name", self.name)
        form.addRow("IMEI", imei_row)

        form.addRow(self._section("Route"))
        self.start_edit = QLineEdit()
        self.start_edit.setPlaceholderText("click on the map")
        self.end_edit = QLineEdit()
        self.end_edit.setPlaceholderText("click on the map")
        self.start_edit.editingFinished.connect(lambda: self._typed_point("start"))
        self.end_edit.editingFinished.connect(lambda: self._typed_point("end"))
        form.addRow("🟢 Start", self.start_edit)
        form.addRow("🔴 End", self.end_edit)
        self.route_info = QLabel(HINT)
        self.route_info.setWordWrap(True)
        form.addRow("", self.route_info)

        self.interval = QSpinBox()
        self.interval.setRange(1, 3600)
        self.interval.setValue(5)
        self.interval.setSuffix(" s")
        self.speed = QDoubleSpinBox()
        self.speed.setRange(1, 300)
        self.speed.setDecimals(0)
        self.speed.setValue(130)
        self.speed.setSuffix(" km/h")
        self.realistic = QCheckBox("Realistic driving")
        self.realistic.setChecked(True)
        self.realistic.setToolTip("Road speeds, acceleration, curves, traffic stops, idling and GPS noise.\n"
                                  "Max speed caps the vehicle (e.g. 80 for a truck).")
        form.addRow("Interval", self.interval)
        form.addRow("Max speed", self.speed)
        form.addRow("", self.realistic)

        form.addRow(self._section("Scenario"))
        # Edited in its own dialog, so this dialog keeps its size.
        self.scenario_label = QLabel(summary(None))
        self.scenario_label.setWordWrap(True)
        scenario_btn = QPushButton("Edit…")
        scenario_btn.clicked.connect(self._edit_scenario)
        scenario_row = QHBoxLayout()
        scenario_row.addWidget(self.scenario_label, 1)
        scenario_row.addWidget(scenario_btn)
        form.addRow(scenario_row)

        form.addRow(self._section("Server"))
        self.server = QComboBox()
        self.server.currentIndexChanged.connect(self._show_server_details)
        self.server_host = QLabel()
        self.server_port = QLabel()
        form.addRow("Server", self.server)
        form.addRow("Address", self.server_host)
        form.addRow("Port", self.server_port)

        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #c62828;")
        self.error.hide()

        left = QVBoxLayout()
        left.addLayout(form)
        left.addWidget(self.error)
        left.addStretch()
        left_widget = QWidget()
        left_widget.setLayout(left)
        # The form is taller than the dialog -> scrollable column.
        left_scroll = QScrollArea()
        left_scroll.setWidget(left_widget)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setFixedWidth(400)

        self.map = MapView(self)
        self.map.clicked.connect(self._map_clicked)
        self.map.marker_moved.connect(self._marker_moved)
        reset = QPushButton("Reset points")
        reset.clicked.connect(self._reset_points)
        right = QVBoxLayout()
        right.addWidget(self.map, 1)
        right.addWidget(reset, 0, Qt.AlignLeft)

        body = QHBoxLayout()
        body.addWidget(left_scroll)
        body.addLayout(right, 1)

        self.cancel_btn = QPushButton("Cancel")
        self.save_new_btn = QPushButton("Save and new")
        self.save_btn = QPushButton("Save")
        self.save_btn.setDefault(True)
        self.cancel_btn.clicked.connect(self.reject)
        self.save_new_btn.clicked.connect(lambda: self._save(keep_open=True))
        self.save_btn.clicked.connect(lambda: self._save(keep_open=False))
        self.save_new_btn.setVisible(self.device_id is None)
        buttons = QHBoxLayout()
        buttons.addStretch()
        for b in (self.cancel_btn, self.save_new_btn, self.save_btn):
            buttons.addWidget(b)

        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addLayout(buttons)

    @staticmethod
    def _section(title):
        label = QLabel(title)
        label.setStyleSheet("font-weight: 600; color: #555; margin-top: 10px;")
        return label

    def done(self, result):
        self._alive = False
        super().done(result)

    # --- Servers ---------------------------------------------------------------

    def _load_servers(self, selected_id=None):
        self.servers = models.list_servers(self.db)
        self.server.clear()
        for s in self.servers:
            self.server.addItem(s["name"], s["id"])
        if selected_id:
            self.server.setCurrentIndex(max(0, self.server.findData(selected_id)))
        self._show_server_details()

    def _show_server_details(self):
        server = next((s for s in self.servers if s["id"] == self.server.currentData()), None)
        self.server_host.setText(server["host"] if server else "–")
        self.server_port.setText(str(server["port"]) if server else "–")

    # --- Editing an existing device ---------------------------------------------

    def _load_device(self, device_id):
        d = models.get_device(self.db, device_id)
        self.name.setText(d["name"])
        self.imei.setText(d["imei"])
        self.interval.setValue(d["interval_s"])
        self.speed.setValue(d["speed_kmh"])
        self.realistic.setChecked(d["realistic"])
        self._load_servers(d["server_id"])
        self._set_scenario(d["scenario"])
        if d["route"]:
            self.saved_duration_s = d["route"]["duration_s"]
            self.start, self.end = d["route"]["start"], d["route"]["end"]
            self._show_points()
            self.map.show_route(d["route"]["points"], fit=True)
            self.route_info.setText(f"{d['route']['distance_m'] / 1000:.1f} km · saved route")

    # --- Route picking -----------------------------------------------------------

    def _map_clicked(self, lat, lon):
        if self.start is None:
            self.start = [lat, lon]
        else:
            self.end = [lat, lon]   # first click after start = end, later clicks move the end
        self._points_changed()

    def _marker_moved(self, kind, lat, lon):
        setattr(self, kind, [lat, lon])
        self._points_changed()

    def _typed_point(self, kind):
        edit = self.start_edit if kind == "start" else self.end_edit
        if not edit.text().strip() or edit.text() == fmt_point(getattr(self, kind)):
            return
        point = parse_lat_lon(edit.text())
        if point is None:
            self._show_error(f'Invalid {kind} coordinates - use "lat, lon"')
            return
        setattr(self, kind, point)
        self.map.center(point)
        self._points_changed()

    def _reset_points(self):
        self.start = self.end = self.preview = None
        self._route_request += 1
        self.map.clear_route()
        self._show_points()
        self.route_info.setText(HINT)

    def _show_points(self):
        self.start_edit.setText(fmt_point(self.start))
        self.end_edit.setText(fmt_point(self.end))
        self.map.set_picker(self.start, self.end)

    def _points_changed(self):
        self._show_error(None)
        self._show_points()
        if self.start and self.end:
            self._generate_route()

    def _generate_route(self):
        self._route_request += 1
        request = self._route_request
        start, end = self.start, self.end
        self.route_info.setStyleSheet("")
        self.route_info.setText("Generating route…")
        self._set_busy(True)
        run_async(lambda: services.generate_route(start, end),
                  lambda route, error: self._route_ready(request, route, error))

    def _route_ready(self, request, route, error):
        if not self._alive or request != self._route_request:
            return
        self._set_busy(False)
        if error:
            self.preview = None
            self.map.clear_route()
            self.route_info.setStyleSheet("color: #c62828;")
            self.route_info.setText(f"Route error: {error_text(error)}")
            return
        self.preview = route
        self.map.show_route(route["points"])
        minutes = round(route["duration_s"] / 60)
        self.route_info.setText(f"{route['distance_m'] / 1000:.1f} km · ~{minutes} min by car")

    # --- Scenario ------------------------------------------------------------------------

    def _set_scenario(self, scenario):
        self.scenario = scenario
        self.scenario_label.setText(summary(scenario))

    def _edit_scenario(self):
        duration = (self.preview or {}).get("duration_s") or self.saved_duration_s
        dialog = ScenarioDialog(self, self.scenario, duration, self.device_id)
        if dialog.exec() == QDialog.Accepted:
            self._set_scenario(dialog.scenario)

    # --- Save --------------------------------------------------------------------------

    def _set_busy(self, busy):
        self.save_btn.setEnabled(not busy)
        self.save_new_btn.setEnabled(not busy)

    def _show_error(self, message):
        self.error.setText(message or "")
        self.error.setVisible(bool(message))

    def _save(self, keep_open):
        self._show_error(None)
        if not self.start or not self.end:
            self._show_error("Please select start and end point on the map.")
            return
        if self.server.currentData() is None:
            QMessageBox.information(self, "No server", "Add a server first (Servers… in the main window).")
            return

        payload = {
            "name": self.name.text().strip(),
            "imei": self.imei.text().strip(),
            "interval_s": self.interval.value(),
            "speed_kmh": self.speed.value(),
            "realistic": self.realistic.isChecked(),
            "scenario": self.scenario,
            "server_id": self.server.currentData(),
            "start": self.start,
            "end": self.end,
        }
        # Send the previewed route so GraphHopper is not called twice.
        if self.preview:
            payload["route"] = self.preview

        device_id, emulator = self.device_id, self.emulator

        def task():
            # Own connection: this runs in a background thread.
            db = database.connect()
            try:
                if device_id:
                    return services.update_device(db, emulator, device_id, payload)
                return services.create_device(db, payload)
            finally:
                db.close()

        self._set_busy(True)
        run_async(task, lambda device, error: self._saved(device, error, keep_open))

    def _saved(self, device, error, keep_open):
        if not self._alive:
            return
        self._set_busy(False)
        if error:
            self._show_error(error_text(error))
            return
        self.saved.emit(device)
        if keep_open:
            self.name.clear()
            self.imei.clear()
            self._set_scenario(None)
            self._reset_points()
            self.name.setFocus()
        else:
            self.accept()
