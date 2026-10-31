"""
"New device" / "Edit device" dialog: form on the left, route picker map on the right
(same workflow as the web UI: click start, click end, drag to adjust).
"""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from core import database, models, services

from .map_view import MapView
from .obd_dialog import ObdDialog, summary as obd_summary
from .scenario_widget import ScenarioDialog, summary
from .tasks import error_text, run_async

HINT = ("Click the map to set the start, then the end. Every further click adds a new end - "
        "the previous end becomes a stop.")
DEFAULT_DWELL_MIN = 5   # waiting time of a new stop


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


def fmt_minutes(minutes):
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"


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
        self.stops = []          # between start and end, in driving order: [{"point": [lat, lon], "dwell_min"}]
        self.stop_rows = []      # (coordinates edit, waiting time spin box) of every stop row
        self._saved_distance_m = None   # length of the saved route (edit), shown until a new preview
        self.preview = None      # route generated in this dialog
        self.saved_duration_s = 0  # duration of the saved route (edit), for the scenario preview
        self.scenario = None       # scenario saved with the device (None = manual)
        self.obd, self.codec = None, "8"   # OBD settings shown in the summary
        self.obd_edited = False    # only edited OBD settings are saved (a running device counts its mileage)
        self._route_request = 0  # ignores answers to outdated route requests
        self._alive = True       # background results may arrive after the dialog closed

        self.setWindowTitle("Edit device" if device_id else "New device")
        self.resize(1150, 680)
        self._build_ui()
        self._build_stop_rows()
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
        # Stop rows are inserted below the start row (see _build_stop_rows).
        self.form = form
        self.stops_row = form.rowCount()
        self.remove_end_btn = self._remove_button("Remove the end - the last stop becomes the end")
        self.remove_end_btn.clicked.connect(self._remove_end)
        end_row = QHBoxLayout()
        end_row.addWidget(self.end_edit, 1)
        end_row.addWidget(self.remove_end_btn)
        form.addRow("🔴 End", end_row)
        self.stops_help = QLabel("Waiting time at each stop (engine running) · 0 min = only drive through.")
        self.stops_help.setWordWrap(True)
        self.stops_help.setStyleSheet("color: #666; font-size: 11px;")
        form.addRow("", self.stops_help)
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

        form.addRow(self._section("OBD (FMB003)"))
        self.obd_label = QLabel(obd_summary(None))
        self.obd_label.setWordWrap(True)
        obd_btn = QPushButton("Edit…")
        obd_btn.clicked.connect(self._edit_obd)
        obd_row = QHBoxLayout()
        obd_row.addWidget(self.obd_label, 1)
        obd_row.addWidget(obd_btn)
        form.addRow(obd_row)

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
    def _remove_button(tooltip):
        button = QToolButton()
        button.setText("✕")
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        return button

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
        self._set_obd(d["obd"], d["codec"])
        if d["route"]:
            self.saved_duration_s = d["route"]["duration_s"]
            self.start, self.end = d["route"]["start"], d["route"]["end"]
            self.stops = [dict(s) for s in d["route"]["stops"]]
            self._build_stop_rows()
            self._show_points()
            self.map.show_route(d["route"]["points"], fit=True)
            self._saved_distance_m = d["route"]["distance_m"]
            self._show_route_summary()

    # --- Route picking -----------------------------------------------------------

    def _map_clicked(self, lat, lon):
        if self.start is None:
            self.start = [lat, lon]
        elif self.end is None:
            self.end = [lat, lon]
        else:
            # The last point is always the end: the previous end becomes a stop.
            self.stops.append({"point": self.end, "dwell_min": DEFAULT_DWELL_MIN})
            self.end = [lat, lon]
            self._build_stop_rows()
        self._points_changed()

    def _marker_moved(self, kind, lat, lon):
        if kind.startswith("stop:"):
            self.stops[int(kind[5:])]["point"] = [lat, lon]
        else:
            setattr(self, kind, [lat, lon])
        self._points_changed()

    # --- Stops ---------------------------------------------------------------------------

    def _build_stop_rows(self):
        """One form row per stop: coordinates, waiting time, remove button."""
        for _ in self.stop_rows:
            self.form.removeRow(self.stops_row)
        self.stop_rows = []
        for i, stop in enumerate(self.stops):
            edit = QLineEdit(fmt_point(stop["point"]))
            edit.editingFinished.connect(lambda i=i: self._typed_stop(i))
            dwell = QSpinBox()
            dwell.setRange(0, 720)
            dwell.setSuffix(" min")
            dwell.setValue(stop["dwell_min"])
            dwell.setToolTip("Waiting time (0 = only drive through)")
            dwell.valueChanged.connect(lambda value, i=i: self._dwell_changed(i, value))
            remove = self._remove_button("Remove stop")
            # Removing rebuilds the rows (and deletes this button) - do it after the click is handled.
            remove.clicked.connect(lambda _=False, i=i: QTimer.singleShot(0, lambda: self._remove_stop(i)))
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            row.addWidget(dwell)
            row.addWidget(remove)
            self.form.insertRow(self.stops_row + i, f"🔵 Stop {i + 1}", row)
            self.stop_rows.append((edit, dwell))
        self.remove_end_btn.setVisible(bool(self.stops))
        self.stops_help.setVisible(bool(self.stops))

    def _typed_stop(self, i):
        if i >= len(self.stop_rows) or i >= len(self.stops):
            return    # the row is being removed
        edit = self.stop_rows[i][0]
        if edit.text() == fmt_point(self.stops[i]["point"]):
            return
        point = parse_lat_lon(edit.text())
        if point is None:
            self._show_error('Invalid stop coordinates - use "lat, lon"')
            return
        self.stops[i]["point"] = point
        self.map.center(point)
        self._points_changed()

    def _dwell_changed(self, i, value):
        # Only the waiting time changed - the route stays the same.
        self.stops[i]["dwell_min"] = value
        self._show_route_summary()

    def _remove_stop(self, i):
        if not self._alive or i >= len(self.stops):
            return
        del self.stops[i]
        self._build_stop_rows()
        self._points_changed()

    def _remove_end(self):
        """The last stop becomes the end."""
        if self.stops:
            self.end = self.stops.pop()["point"]
            QTimer.singleShot(0, self._build_stop_rows)
            self._points_changed()

    def _stops_minutes(self):
        return sum(s["dwell_min"] for s in self.stops)

    def _show_route_summary(self):
        """"15.5 km · ~33 min by car · 2 stops, 15 min waiting" """
        if self.preview:
            text = f"{self.preview['distance_m'] / 1000:.1f} km · ~{round(self.preview['duration_s'] / 60)} min by car"
        elif self._saved_distance_m is not None:
            text = f"{self._saved_distance_m / 1000:.1f} km · saved route"
        else:
            return
        if self.stops:
            n = len(self.stops)
            text += f" · {n} stop{'s' if n > 1 else ''}, {fmt_minutes(self._stops_minutes())} waiting"
        self.route_info.setStyleSheet("")
        self.route_info.setText(text)

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
        self._saved_distance_m = None
        self.stops = []
        self._build_stop_rows()
        self._route_request += 1
        self._set_busy(False)
        self.map.clear_route()
        self._show_points()
        self.route_info.setStyleSheet("")
        self.route_info.setText(HINT)

    def _show_points(self):
        self.start_edit.setText(fmt_point(self.start))
        self.end_edit.setText(fmt_point(self.end))
        for (edit, _dwell), stop in zip(self.stop_rows, self.stops):
            edit.setText(fmt_point(stop["point"]))
        self.map.set_picker(self.start, self.end, [s["point"] for s in self.stops])

    def _points_changed(self):
        self._show_error(None)
        self._show_points()
        if self.start and self.end:
            self._generate_route()

    def _generate_route(self):
        self._route_request += 1
        request = self._route_request
        start, end, via = self.start, self.end, [s["point"] for s in self.stops]
        self.route_info.setStyleSheet("")
        self.route_info.setText("Generating route…")
        self._set_busy(True)
        run_async(lambda: services.generate_route(start, end, via),
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
        self._show_route_summary()

    # --- Scenario ------------------------------------------------------------------------

    def _set_scenario(self, scenario):
        self.scenario = scenario
        self.scenario_label.setText(summary(scenario))

    def _edit_scenario(self):
        duration = (self.preview or {}).get("duration_s") or self.saved_duration_s
        dialog = ScenarioDialog(self, self.scenario, duration, self.device_id, self._stops_minutes() * 60)
        if dialog.exec() == QDialog.Accepted:
            self._set_scenario(dialog.scenario)

    # --- OBD -----------------------------------------------------------------------------

    def _set_obd(self, obd, codec, edited=False):
        self.obd, self.codec, self.obd_edited = obd, codec, edited
        self.obd_label.setText(obd_summary(obd, codec))

    def _edit_obd(self):
        dialog = ObdDialog(self, {**self.obd, "codec": self.codec} if self.obd else None)
        if dialog.exec() == QDialog.Accepted:
            self._set_obd(dialog.obd, dialog.obd["codec"], edited=True)

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
            "stops": [{"point": s["point"], "dwell_min": s["dwell_min"]} for s in self.stops],
        }
        # Send the previewed route so GraphHopper is not called twice.
        if self.preview:
            payload["route"] = self.preview
        if self.obd_edited:
            payload["obd"] = self.obd

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
            self._set_obd(None, "8")
            self._reset_points()
            self.name.setFocus()
        else:
            self.accept()
