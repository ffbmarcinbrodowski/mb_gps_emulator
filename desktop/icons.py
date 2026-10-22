"""Colors and icons, drawn in code so the app needs no image files."""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF

BRAND = "#0b5e86"

# Same colors as the status badges in the web UI.
STATUS_COLORS = {
    "ready": "#6c757d",
    "running": "#198754",
    "parked": "#0aa2c0",
    "paused": "#e0a800",
    "finished": "#0d6efd",
}


def app_icon():
    """Blue circle with a white navigation arrow."""
    pixmap = QPixmap(256, 256)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(BRAND))
    p.drawEllipse(8, 8, 240, 240)
    p.setBrush(QColor("white"))
    p.drawPolygon(QPolygonF([QPointF(128, 40), QPointF(196, 206), QPointF(128, 166), QPointF(60, 206)]))
    p.end()
    return QIcon(pixmap)


def status_dot(status):
    """Small colored circle for the status column."""
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(STATUS_COLORS.get(status, "#6c757d")))
    p.drawEllipse(1, 1, 12, 12)
    p.end()
    return QIcon(pixmap)
