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
    newVin: () => request('GET', '/api/devices/new-vin'),
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
  history: {
    start: (deviceId, days) => request('POST', '/api/history', { device_id: deviceId, days }),
    status: (deviceId) => request('GET', `/api/history/${deviceId}`),
    cancel: (deviceId) => request('POST', `/api/history/${deviceId}/cancel`),
  },
  obd: {
    check: (obd) => request('POST', '/api/obd/check', obd),
  },
  scenarios: {
    preview: (scenario, durationS, deviceId, stopsS = 0) => request('POST', '/api/scenarios/preview',
      { scenario, duration_s: durationS, device_id: deviceId, stops_s: stopsS }),
  },
  routes: {
    generate: (start, end, via = []) => request('POST', '/api/routes', { start, end, via }),
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
