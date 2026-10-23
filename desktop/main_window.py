"""
Main window: device table on the left, live map on the right.

Live updates: emulator threads call Emulator.on_change(device_id), which emits a Qt
signal. Qt delivers it in the GUI thread, where changed devices are re-read from
the database (batched every 200 ms) and the table + map are updated.
"""
import webbrowser

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu, QMessageBox,
    QSplitter, QSystemTrayIcon, QTableView, QToolButton, QVBoxLayout, QWidget,
)

from core import config, database, events, models, services
from core.engine import Emulator

from . import settings
from .device_dialog import DeviceDialog
from .device_table import COL_PROGRESS, DeviceTableModel, ProgressDelegate
from .history_dialog import HistoryDialog
from .icons import BRAND, app_icon
from .map_view import MapView
from .servers_dialog import ServersDialog
from .settings_dialog import SettingsDialog
from .tasks import error_text, run_async

GITHUB_URL = "https://github.com/ffbmarcinbrodowski/mb_gps_emulator"
HEADER_STYLE = f"background: {BRAND}; color: white; font-size: 15px; padding: 8px 12px;"


def add_items(target, items):
    """Add actions to a menu/toolbar; None means a separator."""
    for item in items:
        if item is None:
            target.addSeparator()
        else:
            target.addAction(item)


class EmulatorSignals(QObject):
    """Turns the emulator's on_change callback (worker threads) into a Qt signal."""
    device_changed = Signal(int)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MB GPS Emulator")
        self.setWindowIcon(app_icon())
        self.resize(1400, 860)

        self.db = database.connect()   # used only in the GUI thread
        self.signals = EmulatorSignals()
        self.signals.device_changed.connect(self._device_changed)
        self.emulator = Emulator(on_change=self.signals.device_changed.emit)

        self.selected_id = None
        self._route_key = None          # (device id, route id) drawn on the map
        self._dirty = set()             # devices changed since the last refresh
        self._refresh_timer = QTimer(self, singleShot=True, interval=200, timeout=self._refresh_dirty)

        self._build_ui()
        self._build_actions()
        self._build_tray()
        self.reload_all(fit=True)
        # Scenario devices that were armed when the app was closed continue on their own.
        self.emulator.start_all(services.armed_device_ids(self.db))

    # ======================================================================
    # UI
    # ======================================================================

    def _build_ui(self):
        # Left: device table
        self.model = DeviceTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(COL_PROGRESS, ProgressDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_PROGRESS, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_PROGRESS, 110)
        header.setStretchLastSection(True)
        self.table.selectionModel().currentRowChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(lambda _: self.edit_device())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)

        devices_header = QLabel("Devices")
        devices_header.setStyleSheet(HEADER_STYLE)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(devices_header)
        left_layout.addWidget(self.table)

        # Right: map with a header showing the selected device
        self.map_title = QLabel("Device: –")
        self.map_info = QLabel("")
        self.map_info.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        map_header = QWidget()
        map_header.setStyleSheet(HEADER_STYLE)
        header_layout = QHBoxLayout(map_header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(self.map_title)
        header_layout.addWidget(self.map_info, 1)

        self.map = MapView(self)
        self.map.device_clicked.connect(self.select_device)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(map_header)
        right_layout.addWidget(self.map, 1)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([560, 840])
        self.setCentralWidget(splitter)

        self.db_label = QLabel(f"Database: {config.DATABASE_PATH}")
        self.db_label.setStyleSheet("color: #777;")
        self.statusBar().addPermanentWidget(self.db_label)

    def _action(self, text, slot, shortcut=None, tip=None):
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if tip:
            action.setStatusTip(tip)
        return action

    def _build_actions(self):
        self.act_add = self._action("Add", self.add_device, "Ctrl+N", "Add a new device")
        self.act_edit = self._action("Edit", self.edit_device, "Ctrl+E", "Edit the selected device")
        self.act_delete = self._action("Delete", self.delete_device, QKeySequence.Delete, "Delete the selected device")
        self.act_start = self._action("Start", self.start_device, "Ctrl+R", "Start the selected device")
        self.act_stop = self._action("Stop", self.stop_device, "Ctrl+.", "Stop the selected device")
        self.act_history = self._action("History…", self.open_history, "Ctrl+H",
                                        "Generate past data from the device's scenario")
        self.act_start_all = self._action("Start all", self.start_all, "Ctrl+Shift+R")
        self.act_stop_all = self._action("Stop all", self.stop_all, "Ctrl+Shift+.")
        self.act_servers = self._action("Servers…", self.open_servers)
        self.act_settings = self._action("Settings…", self.open_settings, QKeySequence.Preferences)
        self.act_quit = self._action("Quit", self.close, QKeySequence.Quit)

        # Event menu: one entry per event in core/events.py
        self.event_menu = QMenu("Event", self)
        for e in events.catalog():
            action = self.event_menu.addAction(f"{e['label']}{' *' if e['needs_model'] else ''}")
            action.setStatusTip(f"Traccar: {e['traccar']}")
            action.triggered.connect(lambda _=False, t=e["type"], label=e["label"]: self.send_event(t, label))
        self.event_menu.addSeparator()
        note = self.event_menu.addAction("* Traccar decodes it only if the device's Model is set, e.g. FMB920")
        note.setEnabled(False)

        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        add_items(toolbar, [self.act_add, self.act_edit, self.act_delete, None,
                            self.act_start, self.act_stop, None, self.act_start_all, self.act_stop_all, None,
                            self.act_history])
        self.event_button = QToolButton()
        self.event_button.setText("Event")
        self.event_button.setMenu(self.event_menu)
        self.event_button.setPopupMode(QToolButton.InstantPopup)
        toolbar.addWidget(self.event_button)
        toolbar.addSeparator()
        toolbar.addAction(self.act_servers)
        toolbar.addAction(self.act_settings)

        menu = self.menuBar()
        file_menu = menu.addMenu("File")
        file_menu.addAction(self.act_settings)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)
        devices_menu = menu.addMenu("Devices")
        add_items(devices_menu, [self.act_add, self.act_edit, self.act_delete, None, self.act_start, self.act_stop,
                                 None, self.act_start_all, self.act_stop_all, None, self.act_history, None])
        devices_menu.addMenu(self.event_menu)
        menu.addMenu("Servers").addAction(self.act_servers)
        help_menu = menu.addMenu("Help")
        help_menu.addAction(self._action("Project on GitHub", lambda: webbrowser.open(GITHUB_URL)))
        help_menu.addAction(self._action("About", self.about))

    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("MB GPS Emulator")
        tray_menu = QMenu(self)
        tray_menu.addAction("Show window", self.showNormal)
        tray_menu.addAction(self.act_start_all)
        tray_menu.addAction(self.act_stop_all)
        tray_menu.addSeparator()
        tray_menu.addAction(self.act_quit)
        self.tray.setContextMenu(tray_menu)
        self.tray.show()

    def _context_menu(self, pos):
        if self.table.indexAt(pos).isValid():
            menu = QMenu(self)
            add_items(menu, [self.act_start, self.act_stop, None, self.act_edit, self.act_delete, None])
            menu.addMenu(self.event_menu)
            menu.exec(self.table.viewport().mapToGlobal(pos))

    # ======================================================================
    # Data -> UI
    # ======================================================================

    def reload_all(self, fit=False):
        """Re-read every device (after add/edit/delete)."""
        self.model.set_devices(models.list_devices(self.db))
        if self.model.row_of(self.selected_id) < 0:
            self.selected_id = self.model.devices[0]["id"] if self.model.devices else None
        self._restore_selection()
        self.map.set_devices(self.model.devices, fit=fit)
        self._refresh_selected(fit_route=fit)

    def _device_changed(self, device_id):
        # Called for every position update - collect and refresh in one batch.
        self._dirty.add(device_id)
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _refresh_dirty(self):
        for device_id in self._dirty:
            old = self.model.get(device_id)
            new = models.get_device(self.db, device_id, include_points=False)
            if new is None:
                self.model.remove(device_id)
                continue
            self.model.upsert(new)
            self._notify(old, new)
        self._dirty.clear()
        self.map.set_devices(self.model.devices)
        self._refresh_selected()

    def _restore_selection(self):
        row = self.model.row_of(self.selected_id)
        if row >= 0:
            self.table.selectRow(row)

    def select_device(self, device_id):
        row = self.model.row_of(device_id)
        if row >= 0:
            self.table.selectRow(row)

    def _selection_changed(self, current, _previous):
        if current.isValid():
            self.selected_id = self.model.devices[current.row()]["id"]
            self._refresh_selected(fit_route=True)

    def _selected(self):
        return self.model.get(self.selected_id)

    def _refresh_selected(self, fit_route=False):
        d = self._selected()
        self.map_title.setText(f"Device: {d['name']}" if d else "Device: –")
        if d:
            info = d["display_status"]
            if d["status"] == "running" and d["current"]:
                info += f" · {d['current']['speed']:.0f} km/h"
            info += f" · {d['progress_pct']:.1f}%"
            if d["display_status"] == "parked" and d["next_trip_at"]:
                info += f" · next {d['next_trip_at']}"
            if d["last_sent_local"]:
                info += f" · last sent {d['last_sent_local']}"
            self.map_info.setText(info)
        else:
            self.map_info.setText("")
        self._update_actions()

        # Route geometry is only loaded when the selection or the route changes.
        key = (d["id"], d["route"]["id"]) if d and d["route"] else None
        if key == self._route_key and not fit_route:
            return
        self._route_key = key
        if key is None:
            self.map.clear_route()
            return
        full = models.get_device(self.db, d["id"])
        route = full["route"]
        self.map.show_route(route["points"], route["start"], route["end"], fit=fit_route,
                            return_points=route["return_points"])

    def _update_actions(self):
        d = self._selected()
        running = bool(d) and d["status"] == "running"
        self.act_edit.setEnabled(bool(d))
        self.act_delete.setEnabled(bool(d))
        self.act_start.setEnabled(bool(d) and not running)
        self.act_stop.setEnabled(running)
        self.event_menu.setEnabled(running)
        self.event_button.setEnabled(running)
        # History follows the scenario and must not mix with live data.
        self.act_history.setEnabled(bool(d) and bool(d["scenario"]) and not running)
        running_count = sum(1 for x in self.model.devices if x["status"] == "running")
        self.statusBar().showMessage(f"{len(self.model.devices)} devices · {running_count} running")

    def _notify(self, old, new):
        """System notification when a route finishes or a connection error appears."""
        if not (self.tray and old and settings.notifications_enabled()):
            return
        if old["status"] != "finished" and new["status"] == "finished":
            self.tray.showMessage("Route finished", f"{new['name']} reached the end of its route.",
                                  QSystemTrayIcon.Information, 5000)
        elif not old["last_error"] and new["last_error"]:
            self.tray.showMessage(f"{new['name']}: connection problem", new["last_error"],
                                  QSystemTrayIcon.Warning, 8000)

    # ======================================================================
    # Actions
    # ======================================================================

    def add_device(self):
        if not models.list_servers(self.db):
            QMessageBox.information(self, "No servers", "Add a server first (Traccar host + port).")
            self.open_servers()
            if not models.list_servers(self.db):
                return
        self._open_device_dialog(None)

    def edit_device(self):
        if self.selected_id:
            self._open_device_dialog(self.selected_id)

    def _open_device_dialog(self, device_id):
        dialog = DeviceDialog(self, self.db, self.emulator, device_id)
        dialog.saved.connect(self._device_saved)
        dialog.exec()

    def _device_saved(self, device):
        self.selected_id = device["id"]
        self._route_key = None
        self.reload_all()
        self._refresh_selected(fit_route=True)

    def delete_device(self):
        d = self._selected()
        if not d or QMessageBox.question(self, "Delete device", f'Delete device "{d["name"]}"?') != QMessageBox.Yes:
            return
        emulator = self.emulator

        def task():
            db = database.connect()
            try:
                services.delete_device(db, emulator, d["id"])
            finally:
                db.close()

        run_async(task, self._after_background_change)

    def start_device(self):
        if self.selected_id and not self.emulator.start(self.selected_id) \
                and self.emulator.history_running(self.selected_id):
            QMessageBox.information(self, "Start", "History is being generated for this device - wait or cancel it.")

    def stop_device(self):
        if self.selected_id:
            device_id = self.selected_id
            # Stopping waits for the worker thread -> do it in the background.
            run_async(lambda: self.emulator.stop(device_id), self._after_background_change)

    def start_all(self):
        started = self.emulator.start_all(services.startable_device_ids(self.db))
        self.statusBar().showMessage(f"Started {len(started)} device(s)", 4000)

    def stop_all(self):
        run_async(self.emulator.stop_all, self._after_background_change)

    def _after_background_change(self, _result, error):
        if error:
            QMessageBox.warning(self, "Error", error_text(error))
        self.reload_all()

    def send_event(self, event_type, label):
        d = self._selected()
        if d and self.emulator.trigger(d["id"], event_type):
            self.statusBar().showMessage(f"{label} sent from {d['name']}", 5000)
        else:
            QMessageBox.information(self, "Event", "Start the device first - events need an open connection.")

    def open_history(self):
        d = self._selected()
        if d:
            HistoryDialog(self, self.db, self.emulator, d).exec()
            self.reload_all()

    def open_servers(self):
        dialog = ServersDialog(self, self.db)
        dialog.changed.connect(self.reload_all)
        dialog.exec()

    def open_settings(self):
        SettingsDialog(self).exec()

    def about(self):
        QMessageBox.about(
            self, "About MB GPS Emulator",
            "<b>MB GPS Emulator</b><br>Teltonika GPS tracker emulator (Codec 8 over TCP).<br><br>"
            f'<a href="{GITHUB_URL}">{GITHUB_URL}</a><br>MIT License',
        )

    def closeEvent(self, event):
        # Ask the emulator, not the table - the table may not be refreshed yet.
        running = [d for d in self.model.devices if self.emulator.is_running(d["id"])]
        if running:
            armed = sum(1 for d in running if d["scenario"])
            note = f"\n{armed} device(s) with a scenario will continue when the app is started again." if armed else ""
            answer = QMessageBox.question(
                self, "Quit", f"{len(running)} device(s) are running. Stop them and quit?{note}")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self.emulator.stop_all(disarm=False)
        if self.tray:
            self.tray.hide()
        self.db.close()
        event.accept()
