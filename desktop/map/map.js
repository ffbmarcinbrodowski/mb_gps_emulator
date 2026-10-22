// Leaflet map inside the Qt window.
// Python -> JS: calls window.mapApi.<function>(...) via runJavaScript().
// JS -> Python: calls methods of the "bridge" object via QWebChannel
// (qwebchannel.js is injected by map_view.py).

let bridge = null;
new QWebChannel(qt.webChannelTransport, (channel) => { bridge = channel.objects.bridge; });

const map = L.map('map').setView([49.234, 6.997], 12); // Saarbrücken
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

// The Qt widget can change size at any time (splitter, dialog) -> let Leaflet re-measure.
window.addEventListener('resize', () => map.invalidateSize());
map.on('click', (e) => bridge && bridge.mapClicked(e.latlng.lat, e.latlng.lng));

const deviceMarkers = {};   // device id -> marker
let routeLayer = null;      // route polyline + start/end dots
const picker = { start: null, end: null };

function dotIcon(color) {
  return L.divIcon({
    className: '',
    html: `<div style="width:18px;height:18px;border-radius:50%;background:${color};
                       opacity:.85;border:2px solid #fff;box-shadow:0 0 3px rgba(0,0,0,.5)"></div>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
}
const START_COLOR = '#4caf50';
const END_COLOR = '#e53935';

function arrowIcon(color, bearing) {
  return L.divIcon({
    className: 'device-marker',
    html: `<svg width="30" height="30" viewBox="0 0 30 30" style="transform:rotate(${bearing}deg)">
             <circle cx="15" cy="15" r="13" fill="#fff"/>
             <path d="M15 4 L23 24 L15 19 L7 24 Z" fill="${color}"/>
           </svg>`,
    iconSize: [30, 30],
    iconAnchor: [15, 15],
  });
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

window.mapApi = {
  // devices: [{id, name, lat, lon, bearing, color}]
  setDevices(devices, fit) {
    const seen = new Set();
    for (const d of devices) {
      seen.add(d.id);
      let marker = deviceMarkers[d.id];
      if (!marker) {
        marker = L.marker([d.lat, d.lon], { zIndexOffset: 1000 }).addTo(map);
        marker.bindTooltip('', { permanent: true, direction: 'right', offset: [14, 0], className: 'device-label' });
        marker.on('click', () => bridge && bridge.deviceClicked(d.id));
        deviceMarkers[d.id] = marker;
      }
      marker.setLatLng([d.lat, d.lon]);
      marker.setIcon(arrowIcon(d.color, d.bearing));
      marker.setTooltipContent(escapeHtml(d.name));
    }
    for (const id of Object.keys(deviceMarkers)) {
      if (!seen.has(Number(id))) {
        map.removeLayer(deviceMarkers[id]);
        delete deviceMarkers[id];
      }
    }
    if (fit && devices.length) {
      map.fitBounds(L.latLngBounds(devices.map((d) => [d.lat, d.lon])), { padding: [60, 60], maxZoom: 14 });
    }
  },

  // points: [[lat, lon, ele], ...]; start/end: [lat, lon] (dots drawn unless the picker shows them)
  // returnPoints: optional return route B -> A (scenarios), drawn orange and dashed
  showRoute(points, start, end, fit, returnPoints) {
    this.clearRoute();
    const line = L.polyline(points.map((p) => [p[0], p[1]]), { color: '#1e88e5', weight: 4, opacity: 0.8 });
    const layers = [line];
    if (returnPoints) {
      layers.unshift(L.polyline(returnPoints.map((p) => [p[0], p[1]]),
        { color: '#fb8c00', weight: 3, opacity: 0.8, dashArray: '6 6' }));
    }
    if (start) layers.push(L.marker(start, { icon: dotIcon(START_COLOR), interactive: false }));
    if (end) layers.push(L.marker(end, { icon: dotIcon(END_COLOR), interactive: false }));
    routeLayer = L.layerGroup(layers).addTo(map);
    if (fit) map.fitBounds(line.getBounds(), { padding: [40, 40] });
  },

  clearRoute() {
    if (routeLayer) map.removeLayer(routeLayer);
    routeLayer = null;
  },

  // Draggable start/end markers for the device dialog.
  setPicker(start, end) {
    for (const [kind, point, color] of [['start', start, START_COLOR], ['end', end, END_COLOR]]) {
      if (picker[kind]) map.removeLayer(picker[kind]);
      picker[kind] = null;
      if (point) {
        picker[kind] = L.marker(point, { icon: dotIcon(color), draggable: true }).addTo(map);
        picker[kind].on('dragend', (e) => {
          const ll = e.target.getLatLng();
          if (bridge) bridge.markerMoved(kind, ll.lat, ll.lng);
        });
      }
    }
  },

  center(point, zoom) {
    map.setView(point, zoom || map.getZoom());
  },
};
