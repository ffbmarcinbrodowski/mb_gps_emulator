"""Device list: a table model over the device dicts from core.models + a progress bar delegate."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionProgressBar

from .icons import status_dot

COLUMNS = ["Name", "Status", "Progress", "Speed", "Server", "Last event", "Error"]
COL_STATUS, COL_PROGRESS, COL_ERROR = 1, 2, 6


class DeviceTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.devices = []

    # --- Qt model interface --------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.devices)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        d = self.devices[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            return self._text(d, col)
        if role == Qt.DecorationRole and col == COL_STATUS:
            return status_dot(d["display_status"])
        if role == Qt.UserRole and col == COL_PROGRESS:
            return d["progress_pct"]
        if role == Qt.ForegroundRole and col == COL_ERROR:
            return QColor("#c62828")
        if role == Qt.ToolTipRole:
            if col == 0:
                mode = "realistic" if d["realistic"] else "simple"
                return f"IMEI {d['imei']}\nOdometer {d['odometer_m'] / 1000:.1f} km\n{mode} driving, max {d['speed_kmh']:.0f} km/h"
            if col == COL_ERROR and d["last_error"]:
                return d["last_error"]
        return None

    @staticmethod
    def _text(d, col):
        if col == 0:
            return d["name"]
        if col == COL_STATUS:
            text = d["display_status"].capitalize()
            if d["display_status"] == "parked" and d["next_trip_at"]:
                return f"{text} · next {d['next_trip_at']}"
            if d["display_status"] == "running" and d["scenario"]:
                return f"{text} {'B→A' if d['direction'] == 'return' else 'A→B'}"
            return text
        if col == COL_PROGRESS:
            return f"{d['progress_pct']:.0f} %"
        if col == 3:
            running = d["status"] == "running" and d["activity"] != "parked" and d["current"]
            return f"{d['current']['speed']:.0f} km/h" if running else "–"
        if col == 4:
            return f"{d['server']['host']}:{d['server']['port']}" if d["server"] else "no server"
        if col == 5:
            # "2026-10-22 16:54:24" -> "Crash 16:54:24"
            return f"{d['last_event']} {d['last_event_at'][11:]}" if d["last_event"] else ""
        if col == COL_ERROR:
            return d["last_error"] or ""
        return None

    # --- Updates ---------------------------------------------------------------

    def set_devices(self, devices):
        self.beginResetModel()
        self.devices = list(devices)
        self.endResetModel()

    def row_of(self, device_id):
        for row, d in enumerate(self.devices):
            if d["id"] == device_id:
                return row
        return -1

    def get(self, device_id):
        row = self.row_of(device_id)
        return self.devices[row] if row >= 0 else None

    def upsert(self, device):
        """Replace the row of this device, or append it."""
        row = self.row_of(device["id"])
        if row >= 0:
            self.devices[row] = device
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))
        else:
            self.beginInsertRows(QModelIndex(), len(self.devices), len(self.devices))
            self.devices.append(device)
            self.endInsertRows()

    def remove(self, device_id):
        row = self.row_of(device_id)
        if row >= 0:
            self.beginRemoveRows(QModelIndex(), row, row)
            del self.devices[row]
            self.endRemoveRows()


class ProgressDelegate(QStyledItemDelegate):
    """Draws the progress column as a small progress bar."""

    def paint(self, painter, option, index):
        value = index.data(Qt.UserRole) or 0
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        bar = QStyleOptionProgressBar()
        bar.rect = option.rect.adjusted(4, 6, -4, -6)
        bar.minimum, bar.maximum = 0, 100
        bar.progress = int(value)
        bar.text = f"{value:.0f} %"
        bar.textVisible = True
        QApplication.style().drawControl(QStyle.CE_ProgressBar, bar, painter)
