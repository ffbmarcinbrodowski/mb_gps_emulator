"""Servers dialog: list, add, edit, delete (Traccar or any Teltonika server)."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core import models, services

from .tasks import error_text


class ServersDialog(QDialog):
    changed = Signal()

    def __init__(self, parent, db):
        super().__init__(parent)
        self.db = db
        self.editing_id = None
        self.setWindowTitle("Servers")
        self.resize(640, 420)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Host", "Port"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._selection_changed)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete)

        self.name = QLineEdit()
        self.name.setPlaceholderText("Name")
        self.host = QLineEdit()
        self.host.setPlaceholderText("Host, e.g. demo.traccar.org")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(5027)
        self.save_btn = QPushButton("Add")
        self.save_btn.clicked.connect(self._save)
        self.cancel_btn = QPushButton("New")
        self.cancel_btn.setToolTip("Clear the form to add a new server")
        self.cancel_btn.clicked.connect(self._clear_form)

        form = QHBoxLayout()
        form.addWidget(self.name, 2)
        form.addWidget(self.host, 3)
        form.addWidget(self.port, 1)
        form.addWidget(self.save_btn)
        form.addWidget(self.cancel_btn)

        hint = QLabel("Traccar listens for Teltonika devices on port <b>5027</b> by default. "
                      "Each device's IMEI must also be added as a device in Traccar.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.delete_btn)
        bottom.addStretch()
        bottom.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addLayout(bottom)
        self._load()

    def _load(self):
        self.servers = models.list_servers(self.db)
        self.table.setRowCount(len(self.servers))
        for row, s in enumerate(self.servers):
            for col, value in enumerate((s["name"], s["host"], str(s["port"]))):
                self.table.setItem(row, col, QTableWidgetItem(value))

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self.servers[rows[0].row()] if rows else None

    def _selection_changed(self):
        s = self._selected()
        self.delete_btn.setEnabled(s is not None)
        if s:
            self.editing_id = s["id"]
            self.name.setText(s["name"])
            self.host.setText(s["host"])
            self.port.setValue(s["port"])
            self.save_btn.setText("Save")

    def _clear_form(self):
        self.table.clearSelection()
        self.editing_id = None
        self.name.clear()
        self.host.clear()
        self.port.setValue(5027)
        self.save_btn.setText("Add")
        self.name.setFocus()

    def _save(self):
        data = {"name": self.name.text(), "host": self.host.text(), "port": self.port.value()}
        try:
            if self.editing_id:
                services.update_server(self.db, self.editing_id, data)
            else:
                services.create_server(self.db, data)
        except services.ServiceError as err:
            QMessageBox.warning(self, "Server", error_text(err))
            return
        self._load()
        self._clear_form()
        self.changed.emit()

    def _delete(self):
        s = self._selected()
        if not s or QMessageBox.question(self, "Delete server", f'Delete server "{s["name"]}"?') != QMessageBox.Yes:
            return
        try:
            services.delete_server(self.db, s["id"])
        except services.ServiceError as err:
            QMessageBox.warning(self, "Server", error_text(err))
            return
        self._load()
        self._clear_form()
        self.changed.emit()
