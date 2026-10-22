// Small UI helpers: toasts, escaping and formatting.

export function toast(message, type = 'danger') {
  const el = document.createElement('div');
  el.className = `toast align-items-center text-bg-${type} border-0`;
  el.setAttribute('role', 'alert');
  el.innerHTML = `
    <div class="d-flex">
      <div class="toast-body">${esc(message)}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
    </div>`;
  document.getElementById('toasts').appendChild(el);
  el.addEventListener('hidden.bs.toast', () => el.remove());
  new bootstrap.Toast(el, { delay: 5000 }).show();
}

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

export const fmtCoord = (v, digits = 4) => (v == null ? '–' : Number(v).toFixed(digits));
export const fmtKm = (m) => `${(m / 1000).toFixed(1)} km`;

export function fmtDuration(seconds) {
  const min = Math.round(seconds / 60);
  return min < 60 ? `${min} min` : `${Math.floor(min / 60)} h ${min % 60} min`;
}

// "49.2270, 6.9475" -> [49.227, 6.9475] (or null if invalid)
export function parseLatLon(text) {
  const parts = String(text).split(/[,;\s]+/).filter(Boolean).map(Number);
  if (parts.length !== 2 || parts.some(Number.isNaN)) return null;
  const [lat, lon] = parts;
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? [lat, lon] : null;
}

export const STATUS_BADGE = {
  ready: 'secondary', running: 'success', parked: 'info', paused: 'warning', finished: 'primary',
};

const DAY_NAMES = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];

// Short description of a scenario, e.g. "07:30, 16:30 · Mo-Fr"
export function scenarioText(sc) {
  if (!sc) return 'manual';
  const days = sc.days.length === 7 ? 'daily'
    : sc.days.join(',') === '0,1,2,3,4' ? 'Mo-Fr' : sc.days.map((d) => DAY_NAMES[d]).join(' ');
  if (sc.type === 'times') return `${sc.times.join(', ')} · ${days}`;
  if (sc.type === 'interval') {
    return `every ${sc.every_min} min ${sc.from}-${sc.to}${sc.round_trip ? ' round trip' : ''} · ${days}`;
  }
  return `back and forth ${sc.from}-${sc.to} · ${days}`;
}
