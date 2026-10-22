"""
Map widget: Leaflet + OpenStreetMap in a QWebEngineView.

Python talks to the page with runJavaScript("mapApi...."), the page talks back
through QWebChannel by calling slots of the _Bridge object (signals below).
"""
import json
import os

from PySide6.QtCore import QFile, QIODevice, QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from .icons import STATUS_COLORS

MAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map")


def _page_html():
    """map.html with map.js inlined (a local page does not reliably load local script files)."""
    with open(os.path.join(MAP_DIR, "map.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(MAP_DIR, "map.js"), encoding="utf-8") as f:
        script = f.read()
    return html.replace('<script src="map.js"></script>', f"<script>\n{script}\n</script>")


def _qwebchannel_script():
    resource = QFile(":/qtwebchannel/qwebchannel.js")
    resource.open(QIODevice.ReadOnly)
    script = QWebEngineScript()
    script.setName("qwebchannel")
    script.setSourceCode(bytes(resource.readAll()).decode("utf-8"))
    script.setInjectionPoint(QWebEngineScript.DocumentCreation)
    script.setWorldId(QWebEngineScript.MainWorld)
    return script


class _Bridge(QObject):
    """Object visible in JavaScript as `bridge`."""
    clicked = Signal(float, float)               # lat, lon
    marker_moved = Signal(str, float, float)     # "start"/"end", lat, lon
    device_clicked = Signal(int)                 # device id

    @Slot(float, float)
    def mapClicked(self, lat, lon):
        self.clicked.emit(lat, lon)

    @Slot(str, float, float)
    def markerMoved(self, kind, lat, lon):
        self.marker_moved.emit(kind, lat, lon)

    @Slot(int)
    def deviceClicked(self, device_id):
        self.device_clicked.emit(device_id)


class MapView(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bridge = _Bridge(self)
        self.clicked = self.bridge.clicked
        self.marker_moved = self.bridge.marker_moved
        self.device_clicked = self.bridge.device_clicked

        self._channel = QWebChannel(self.page())
        self._channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self._channel)
        # qwebchannel.js (the JS side of QWebChannel) is a Qt resource; a page loaded
        # from a local file can't fetch qrc:// URLs, so inject it before the page scripts run.
        self.page().scripts().insert(_qwebchannel_script())

        # The page is a local file but loads Leaflet and the map tiles from the internet.
        self.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)

        # JavaScript calls made before the page has loaded are queued.
        self._ready = False
        self._pending = []
        self.loadFinished.connect(self._on_loaded)
        self.setHtml(_page_html(), QUrl.fromLocalFile(MAP_DIR + os.sep))

    def _on_loaded(self, ok):
        self._ready = True
        for js in self._pending:
            self.page().runJavaScript(js)
        self._pending.clear()

    def _call(self, function, *args):
        js = f"mapApi.{function}({', '.join(json.dumps(a) for a in args)})"
        if self._ready:
            self.page().runJavaScript(js)
        else:
            self._pending.append(js)

    # --- API used by the windows ---------------------------------------------

    def set_devices(self, devices, fit=False):
        """Show an arrow marker for every device (at its current position or route start)."""
        markers = []
        for d in devices:
            pos = [d["current"]["lat"], d["current"]["lon"]] if d["current"] else (d["route"] or {}).get("start")
            if not pos:
                continue
            markers.append({
                "id": d["id"], "name": d["name"], "lat": pos[0], "lon": pos[1],
                "bearing": (d["current"] or {}).get("bearing") or 0,
                "color": STATUS_COLORS.get(d["display_status"], "#6c757d"),
            })
        self._call("setDevices", markers, fit)

    def show_route(self, points, start=None, end=None, fit=False, return_points=None):
        self._call("showRoute", points, start, end, fit, return_points)

    def clear_route(self):
        self._call("clearRoute")

    def set_picker(self, start, end):
        self._call("setPicker", start, end)

    def center(self, point, zoom=None):
        self._call("center", point, zoom)
