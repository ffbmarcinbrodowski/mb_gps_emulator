// Thin wrapper around fetch() for the Flask REST API.

async function request(method, url, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const res = await fetch(url, options);
  if (res.status === 204) return null;

  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

export const api = {
  devices: {
    list: () => request('GET', '/api/devices'),
    get: (id) => request('GET', `/api/devices/${id}`),
    newImei: () => request('GET', '/api/devices/new-imei'),
    create: (data) => request('POST', '/api/devices', data),
    update: (id, data) => request('PUT', `/api/devices/${id}`, data),
    remove: (id) => request('DELETE', `/api/devices/${id}`),
  },
  servers: {
    list: () => request('GET', '/api/servers'),
    create: (data) => request('POST', '/api/servers', data),
    update: (id, data) => request('PUT', `/api/servers/${id}`, data),
    remove: (id) => request('DELETE', `/api/servers/${id}`),
  },
  scenarios: {
    preview: (scenario, durationS, deviceId) => request('POST', '/api/scenarios/preview',
      { scenario, duration_s: durationS, device_id: deviceId }),
  },
  routes: {
    generate: (start, end) => request('POST', '/api/routes', { start, end }),
  },
  emulator: {
    start: (deviceId) => request('POST', '/api/emulator/start', { device_id: deviceId }),
    stop: (deviceId) => request('POST', '/api/emulator/stop', { device_id: deviceId }),
    startAll: () => request('POST', '/api/emulator/start', { all: true }),
    stopAll: () => request('POST', '/api/emulator/stop', { all: true }),
    events: () => request('GET', '/api/emulator/events'),
    sendEvent: (deviceId, type) => request('POST', '/api/emulator/event', { device_id: deviceId, type }),
  },
};
