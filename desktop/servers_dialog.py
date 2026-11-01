"""
Servers dialog: list, add, edit, delete (Traccar or any Teltonika server).

The form opens only for "Add server" or "Edit": 1. connection for devices, 2. optional Traccar
API access - and one button at the bottom saves both parts together.
"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core import database, models, services

from .tasks import error_text, run_async

GREY = "color: #666;"


class ServersDialog(QDialog):
    changed = Signal()

    def __init__(self, parent, db):
        super().__init__(parent)
        self.db = db
        self.editing = None        # server being edited (None = new server)
        self.setWindowTitle("Servers")
        self.resize(660, 640)

        # --- List ---
        intro = QLabel("Servers the emulated devices send their positions to.")
        intro.setStyleSheet(GREY)
        self.new_btn = QPushButton("+ Add server")
        self.new_btn.clicked.connect(lambda: self._open_form(None))
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.clicked.connect(self._edit_selected)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self._delete)
        top = QHBoxLayout()
        top.addWidget(intro, 1)
        top.addWidget(self.new_btn)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Address (devices)", "Traccar API"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(self._edit_selected)

        list_buttons = QHBoxLayout()
        list_buttons.addWidget(self.edit_btn)
        list_buttons.addWidget(self.delete_btn)
        list_buttons.addStretch()

        # --- Form ---
        self.form = QGroupBox()
        self.form.setStyleSheet("QGroupBox { font-weight: bold; }")

        connection = QGroupBox("1. Connection for devices")
        conn_hint = QLabel("Where the emulated trackers send their data (Teltonika TCP). "
                           "Traccar's Teltonika port is <b>5027</b>.")
        conn_hint.setWordWrap(True)
        conn_hint.setStyleSheet(GREY + " font-weight: normal;")
        self.name = QLineEdit()
        self.name.setPlaceholderText("My Traccar")
        self.host = QLineEdit()
        self.host.setPlaceholderText("10.40.1.114 or demo.traccar.org")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        conn_form = QFormLayout(connection)
        conn_form.addRow(conn_hint)
        conn_form.addRow("Name", self.name)
        conn_form.addRow("Host", self.host)
        conn_form.addRow("Port", self.port)

        api = QGroupBox("2. Traccar API access (optional)")
        self.api_enabled = QCheckBox("Use the Traccar API – the emulator then creates its devices in Traccar itself\n"
                                     "(it only creates devices, it never changes or deletes any)")
        self.api_enabled.setStyleSheet("font-weight: normal;")
        self.api_enabled.toggled.connect(self._api_toggled)
        self.api_url = QLineEdit()
        self.api_url.setPlaceholderText("http://10.40.1.114:8082")
        self.api_user = QLineEdit()
        self.api_password = QLineEdit()
        self.api_password.setEchoMode(QLineEdit.Password)
        test_btn = QPushButton("Test")
        test_btn.setToolTip("Check address and login without saving")
        test_btn.clicked.connect(self._test_api)
        password_row = QHBoxLayout()
        password_row.addWidget(self.api_password, 1)
        password_row.addWidget(test_btn)
        self.api_result = QLabel()
        self.api_result.setWordWrap(True)
        self.api_fields = QWidget()
        api_form = QFormLayout(self.api_fields)
        api_form.setContentsMargins(0, 0, 0, 0)
        api_form.addRow("Traccar web address", self.api_url)
        api_form.addRow("Login (e-mail)", self.api_user)
        api_form.addRow("Password", password_row)
        api_form.addRow(self.api_result)
        api_layout = QVBoxLayout(api)
        api_layout.addWidget(self.api_enabled)
        api_layout.addWidget(self.api_fields)

        self.save_hint = QLabel()
        self.save_hint.setStyleSheet(GREY + " font-weight: normal;")
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._close_form)
        self.save_btn = QPushButton("Add server")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._save)
        form_buttons = QHBoxLayout()
        form_buttons.addWidget(self.save_hint, 1)
        form_buttons.addWidget(cancel_btn)
        form_buttons.addWidget(self.save_btn)

        form_layout = QVBoxLayout(self.form)
        form_layout.addWidget(connection)
        form_layout.addWidget(api)
        form_layout.addLayout(form_buttons)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addLayout(list_buttons)
        layout.addWidget(self.form)
        layout.addStretch()
        layout.addLayout(bottom)

        self._load()
        self._close_form()

    def _load(self):
        self.servers = models.list_servers(self.db)
        self.table.setRowCount(len(self.servers))
        for row, s in enumerate(self.servers):
            api = f"✓ {s['api_url']}" if s["has_api"] else "–"
            for col, value in enumerate((s["name"], f"{s['host']}:{s['port']}", api)):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self._selection_changed()

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self.servers[rows[0].row()] if rows else None

    def _selection_changed(self):
        selected = self._selected() is not None
        self.edit_btn.setEnabled(selected)
        self.delete_btn.setEnabled(selected)

    # --- Form ------------------------------------------------------------------------------

    def _edit_selected(self):
        if self._selected():
            self._open_form(self._selected())

    def _open_form(self, server):
        s = server or {}
        self.editing = server
        self.form.setTitle(f'Edit server "{server["name"]}"' if server else "New server")
        self.save_btn.setText("Save changes" if server else "Add server")
        self.name.setText(s.get("name", ""))
        self.host.setText(s.get("host", ""))
        self.port.setValue(s.get("port", 5027))
        self.api_url.setText(s.get("api_url") or "")
        self.api_user.setText(s.get("api_user") or "")
        # The password is never shown: empty = keep the stored one.
        self.api_password.clear()
        self.api_password.setPlaceholderText("unchanged – type to replace" if s.get("api_password_set") else "")
        self.api_result.clear()
        self.api_enabled.setChecked(bool(s.get("has_api")))
        self._api_toggled(self.api_enabled.isChecked())
        self.form.show()
        self.new_btn.setEnabled(False)
        self.name.setFocus()

    def _close_form(self):
        self.form.hide()
        self.editing = None
        self.new_btn.setEnabled(True)

    def _api_toggled(self, on):
        self.api_fields.setVisible(on)
        # Traccar's web/API port is 8082 by default - suggest it for the host of the server.
        if on and not self.api_url.text() and self.host.text().strip():
            self.api_url.setText(f"http://{self.host.text().strip()}:8082")
        if on:
            self.save_hint.setText("Saves the server and its Traccar API access.")
        elif self.editing and self.editing["has_api"]:
            self.save_hint.setText("Saves the server and removes its Traccar API access.")
        else:
            self.save_hint.setText("Saves the server (without Traccar API access).")

    def _api_fields(self):
        # Switched off = no API access (an existing one is removed on save).
        if not self.api_enabled.isChecked():
            return {"api_url": "", "api_user": "", "api_password": ""}
        return {"api_url": self.api_url.text(), "api_user": self.api_user.text(),
                "api_password": self.api_password.text()}

    def _save(self):
        data = {"name": self.name.text(), "host": self.host.text(), "port": self.port.value(), **self._api_fields()}
        try:
            if self.editing:
                saved = services.update_server(self.db, self.editing["id"], data)
            else:
                saved = services.create_server(self.db, data)
        except services.ServiceError as err:
            QMessageBox.warning(self, "Server", error_text(err))
            return
        self._close_form()
        self._load()
        self.changed.emit()
        extra = " with Traccar API access" if saved["has_api"] else ""
        QMessageBox.information(self, "Server", f'Server "{saved["name"]}" saved{extra}.')

    def _test_api(self):
        data = {**self._api_fields(), "server_id": self.editing["id"] if self.editing else None}
        self.api_result.setStyleSheet("")
        self.api_result.setText("Connecting…")
        save_text = self.save_btn.text()

        def task():
            db = database.connect()     # background thread: own connection
            try:
                return services.test_traccar_api(db, data)
            finally:
                db.close()

        def done(result, error):
            if not self.isVisible():
                return
            if error:
                self.api_result.setStyleSheet("color: #c62828;")
                self.api_result.setText(error_text(error))
            else:
                self.api_result.setStyleSheet("color: #2e7d32;")
                self.api_result.setText(f"✓ Connected - Traccar {result['version'] or ''}. "
                                        f'Not saved yet - click "{save_text}".')

        run_async(task, done)

    def _delete(self):
        s = self._selected()
        if not s or QMessageBox.question(self, "Delete server", f'Delete server "{s["name"]}"?') != QMessageBox.Yes:
            return
        try:
            services.delete_server(self.db, s["id"])
        except services.ServiceError as err:
            QMessageBox.warning(self, "Server", error_text(err))
            return
        if self.editing and self.editing["id"] == s["id"]:
            self._close_form()
        self._load()
        self.changed.emit()
