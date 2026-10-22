// "New device" / "Edit device" modal with the route picker map.

import { api } from './api.js';
import { createMap, endpointMarker, routeLine } from './map.js';
import { esc, fmtCoord, fmtDuration, fmtKm, parseLatLon } from './ui.js';

const $ = (id) => document.getElementById(id);

export function initDeviceForm({ onSaved }) {
  const modalEl = $('deviceModal');
  const modal = new bootstrap.Modal(modalEl);

  let map = null;
  let servers = [];
  const state = {
    editingId: null,
    start: null,        // [lat, lon]
    end: null,
    preview: null,      // route generated in this dialog: {points, distance_m, duration_s}
    generating: false,
    layers: { start: null, end: null, line: null },
  };

  // Leaflet needs a visible container -> create/resize the map once the modal is shown.
  modalEl.addEventListener('shown.bs.modal', () => {
    if (!map) {
      map = createMap('formMap');
      map.on('click', onMapClick);
    }
    map.invalidateSize();
    redraw(true);
  });

  // ---- Map interaction ----------------------------------------------------

  function onMapClick(e) {
    const point = [e.latlng.lat, e.latlng.lng];
    if (!state.start) {
      state.start = point;
    } else {
      state.end = point; // first click after start = end, later clicks move the end
    }
    redraw(false);
    generateRoute();
  }

  function setEndpoint(kind, point) {
    state[kind] = point;
    redraw(false);
    generateRoute();
  }

  function redraw(fit) {
    if (!map) return;
    const { layers } = state;
    for (const key of ['start', 'end']) {
      if (layers[key]) map.removeLayer(layers[key]);
      layers[key] = null;
      if (state[key]) {
        layers[key] = endpointMarker(state[key], key, true).addTo(map);
        layers[key].on('dragend', (e) => {
          const ll = e.target.getLatLng();
          setEndpoint(key, [ll.lat, ll.lng]);
        });
      }
    }
    $('fStart').value = state.start ? `${fmtCoord(state.start[0], 6)}, ${fmtCoord(state.start[1], 6)}` : '';
    $('fEnd').value = state.end ? `${fmtCoord(state.end[0], 6)}, ${fmtCoord(state.end[1], 6)}` : '';

    if (fit) {
      if (layers.line) map.fitBounds(layers.line.getBounds(), { padding: [30, 30] });
      else if (state.start) map.setView(state.start, 13);
    }
  }

  function drawLine(points) {
    if (state.layers.line) map.removeLayer(state.layers.line);
    state.layers.line = points ? routeLine(points).addTo(map) : null;
  }

  async function generateRoute() {
    if (!state.start || !state.end) return;
    state.generating = true;
    setRouteInfo('Generating route…');
    toggleSaveButtons();
    try {
      const route = await api.routes.generate(state.start, state.end);
      state.preview = route;
      drawLine(route.points);
      setRouteInfo(`${fmtKm(route.distance_m)} · ~${fmtDuration(route.duration_s)} by car`);
    } catch (err) {
      state.preview = null;
      drawLine(null);
      setRouteInfo(`Route error: ${err.message}`, true);
    } finally {
      state.generating = false;
      toggleSaveButtons();
    }
  }

  function setRouteInfo(text, isError = false) {
    const el = $('routeInfo');
    el.textContent = text;
    el.classList.toggle('text-danger', isError);
    el.classList.toggle('text-secondary', !isError);
  }

  function toggleSaveButtons() {
    $('btnSave').disabled = state.generating;
    $('btnSaveNew').disabled = state.generating;
  }

  // Typing coordinates by hand works too ("49.2270, 6.9475").
  for (const [inputId, key] of [['fStart', 'start'], ['fEnd', 'end']]) {
    $(inputId).addEventListener('change', (e) => {
      const point = parseLatLon(e.target.value);
      if (point) {
        setEndpoint(key, point);
        map.setView(point, map.getZoom());
      } else {
        showError(`Invalid ${key} coordinates – use "lat, lon"`);
      }
    });
  }

  $('btnResetPoints').addEventListener('click', () => {
    state.start = state.end = state.preview = null;
    drawLine(null);
    redraw(false);
    setRouteInfo('Click the map to set the start, then the end point.');
  });

  // ---- Server select ------------------------------------------------------

  async function loadServers(selectedId) {
    servers = await api.servers.list();
    const select = $('fServer');
    select.innerHTML = '<option value="">Server list…</option>' +
      servers.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
    select.value = selectedId ?? (servers[0]?.id ?? '');
    showServerDetails();
  }

  function showServerDetails() {
    const server = servers.find((s) => String(s.id) === $('fServer').value);
    $('fServerHost').value = server?.host ?? '';
    $('fServerPort').value = server?.port ?? '';
  }
  $('fServer').addEventListener('change', showServerDetails);

  // ---- Open / save --------------------------------------------------------

  function resetForm() {
    $('deviceForm').reset();
    $('fInterval').value = 5;
    $('fSpeed').value = 50;
    state.start = state.end = state.preview = null;
    if (map) drawLine(null);
    setRouteInfo('Click the map to set the start, then the end point.');
    showError(null);
  }

  async function openNew() {
    state.editingId = null;
    $('deviceModalTitle').textContent = 'New device';
    $('btnSaveNew').classList.remove('d-none');
    resetForm();
    await loadServers();
    modal.show();
    redraw(false);
  }

  async function openEdit(deviceId) {
    const device = await api.devices.get(deviceId);
    state.editingId = device.id;
    $('deviceModalTitle').textContent = `Edit device: ${device.name}`;
    $('btnSaveNew').classList.add('d-none');
    resetForm();

    $('fName').value = device.name;
    $('fImei').value = device.imei;
    $('fInterval').value = device.interval_s;
    $('fSpeed').value = device.speed_kmh;
    await loadServers(device.server_id);

    if (device.route) {
      state.start = device.route.start;
      state.end = device.route.end;
      setRouteInfo(`${fmtKm(device.route.distance_m)} · saved route`);
    }
    // The map may only be created on 'shown', so draw the saved route after that.
    modalEl.addEventListener('shown.bs.modal', () => {
      if (device.route) drawLine(device.route.points);
      redraw(true);
    }, { once: true });
    modal.show();
  }

  function showError(message) {
    const el = $('deviceFormError');
    el.textContent = message || '';
    el.classList.toggle('d-none', !message);
  }

  async function save(keepOpen) {
    showError(null);
    if (!state.start || !state.end) return showError('Please select start and end point on the map.');

    const payload = {
      name: $('fName').value.trim(),
      imei: $('fImei').value.trim(),
      interval_s: Number($('fInterval').value),
      speed_kmh: Number($('fSpeed').value),
      server_id: Number($('fServer').value) || null,
      start: state.start,
      end: state.end,
    };
    // Send the previewed route so the backend does not call GraphHopper twice.
    if (state.preview) payload.route = state.preview;

    try {
      const saved = state.editingId
        ? await api.devices.update(state.editingId, payload)
        : await api.devices.create(payload);
      onSaved(saved);
      if (keepOpen) {
        resetForm();
        redraw(false);
        $('fName').focus();
      } else {
        modal.hide();
      }
    } catch (err) {
      showError(err.message);
    }
  }

  $('btnSave').addEventListener('click', () => save(false));
  $('btnSaveNew').addEventListener('click', () => save(true));

  return { openNew, openEdit };
}
