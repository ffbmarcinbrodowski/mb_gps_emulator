// Leaflet helpers shared by the main map and the device form map.

const DEFAULT_CENTER = [49.234, 6.997]; // Saarbrücken
const DEFAULT_ZOOM = 12;

export function createMap(elementId) {
  const map = L.map(elementId).setView(DEFAULT_CENTER, DEFAULT_ZOOM);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  return map;
}

// Green (start) / red (end) round marker, like in the mockup.
export function endpointMarker(latlng, kind, draggable = false) {
  const color = kind === 'start' ? '#4caf50' : '#e53935';
  const icon = L.divIcon({
    className: '',
    html: `<div style="width:18px;height:18px;border-radius:50%;background:${color};
                       opacity:.85;border:2px solid #fff;box-shadow:0 0 3px rgba(0,0,0,.5)"></div>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
  return L.marker(latlng, { icon, draggable, title: kind === 'start' ? 'Start' : 'End' });
}

export function routeLine(points) {
  return L.polyline(points.map((p) => [p[0], p[1]]), { color: '#1e88e5', weight: 4, opacity: 0.8 });
}

// Same colours as the status badges in the device list.
const STATUS_COLOR = {
  ready: '#6c757d', running: '#198754', parked: '#0aa2c0', paused: '#e0a800', finished: '#0d6efd',
};

// Arrow icon pointing in the driving direction (bearing in degrees).
export function deviceIcon(status, bearing = 0) {
  const color = STATUS_COLOR[status] || '#757575';
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
