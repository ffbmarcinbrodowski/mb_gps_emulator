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

// One status for the UI, combined from device status + route status:
//   stopped + planned  -> ready   (not started yet)
//   running            -> running
//   stopped + running  -> paused  (stopped halfway, Start resumes)
//   finished           -> finished (Start begins the route again)
export function displayStatus(device) {
  if (device.status === 'running') return 'running';
  if (device.status === 'finished') return 'finished';
  return device.route?.status === 'running' ? 'paused' : 'ready';
}

export const STATUS_BADGE = {
  ready: 'secondary', running: 'success', paused: 'warning', finished: 'primary',
};
