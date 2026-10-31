// "New device" / "Edit device" modal with the route picker map.

import { api } from './api.js';
import { createMap, endpointMarker, routeLine, stopMarker } from './map.js';
import { initObdForm } from './obdForm.js';
import { initScenarioForm } from './scenarioForm.js';
import { esc, fmtCoord, fmtDuration, fmtKm, parseLatLon } from './ui.js';

const $ = (id) => document.getElementById(id);

const HINT = 'Click the map to set the start, then the end. Every further click adds a new end – '
  + 'the previous end becomes a stop.';
const DEFAULT_DWELL_MIN = 5;   // waiting time of a new stop

export function initDeviceForm({ onSaved }) {
  const modalEl = $('deviceModal');
  const modal = new bootstrap.Modal(modalEl);

  let map = null;
  let servers = [];
  const state = {
    editingId: null,
    start: null,        // [lat, lon]
    end: null,
    stops: [],          // between start and end, in driving order: [{point: [lat, lon], dwell_min}]
    preview: null,      // route generated in this dialog: {points, distance_m, duration_s, stop_indices}
    generating: false,
    routeRequest: 0,    // answers to outdated route requests are ignored
    layers: { start: null, end: null, stops: [], line: null },
    savedDurationS: 0,  // duration of the saved route (edit), used by the scenario preview
    savedRoute: null,   // the saved route (edit), summarized until a new route is generated
  };
  const scenario = initScenarioForm({
    deviceModal: modal,
    durationS: () => state.preview?.duration_s ?? state.savedDurationS,
    deviceId: () => state.editingId,
    stopsS: () => stopsMinutes() * 60,
  });
  const obd = initObdForm({ deviceModal: modal });

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
    } else if (!state.end) {
      state.end = point;
    } else {
      // The last point is always the end: the previous end becomes a stop.
      state.stops.push({ point: state.end, dwell_min: DEFAULT_DWELL_MIN });
      state.end = point;
    }
    pointsChanged();
  }

  function setEndpoint(kind, point) {
    state[kind] = point;
    pointsChanged();
  }

  function pointsChanged() {
    renderStops();
    redraw(false);
    generateRoute();
  }

  const fmtPoint = (p, digits = 6) => (p ? `${fmtCoord(p[0], digits)}, ${fmtCoord(p[1], digits)}` : '');
  const stopsMinutes = () => state.stops.reduce((sum, s) => sum + (Number(s.dwell_min) || 0), 0);

  function draggable(marker, onMoved) {
    marker.on('dragend', (e) => {
      const ll = e.target.getLatLng();
      onMoved([ll.lat, ll.lng]);
    });
    return marker.addTo(map);
  }

  function redraw(fit) {
    if (!map) return;
    const { layers } = state;
    for (const key of ['start', 'end']) {
      if (layers[key]) map.removeLayer(layers[key]);
      layers[key] = state[key]
        ? draggable(endpointMarker(state[key], key, true), (p) => setEndpoint(key, p)) : null;
    }
    layers.stops.forEach((m) => map.removeLayer(m));
    layers.stops = state.stops.map((stop, i) => draggable(stopMarker(stop.point, i + 1, true), (p) => {
      stop.point = p;
      pointsChanged();
    }));
    $('fStart').value = fmtPoint(state.start);
    $('fEnd').value = fmtPoint(state.end);

    // Without animation: when opening an edit, the start is shown first and the route right
    // after - an animated first move would swallow the second one.
    if (fit) {
      if (layers.line) map.fitBounds(layers.line.getBounds(), { padding: [30, 30], animate: false });
      else if (state.start) map.setView(state.start, 13, { animate: false });
    }
  }

  function drawLine(points) {
    if (state.layers.line) map.removeLayer(state.layers.line);
    state.layers.line = points ? routeLine(points).addTo(map) : null;
  }

  // ---- Stops between start and end -----------------------------------------

  function renderStops() {
    $('stopRows').innerHTML = state.stops.map((stop, i) => `
      <div class="form-row-label stop-row" data-index="${i}">
        <label><span class="dot dot-stop">${i + 1}</span>Stop ${i + 1}</label>
        <input class="form-control form-control-sm stop-point" value="${fmtPoint(stop.point, 5)}"
               aria-label="Stop ${i + 1} coordinates">
        <div class="input-group input-group-sm stop-dwell" title="Waiting time (0 = only drive through)">
          <input type="number" class="form-control" min="0" max="720" value="${stop.dwell_min}"
                 aria-label="Stop ${i + 1} waiting time">
          <span class="input-group-text px-1">min</span>
        </div>
        <button type="button" class="btn btn-sm stop-remove" title="Remove stop"><i class="bi bi-x-lg"></i></button>
      </div>`).join('');
    $('btnRemoveEnd').classList.toggle('d-none', !state.stops.length);
    $('stopsHelp').classList.toggle('d-none', !state.stops.length);
  }

  $('stopRows').addEventListener('change', (e) => {
    const stop = state.stops[Number(e.target.closest('.stop-row')?.dataset.index)];
    if (!stop) return;
    if (e.target.classList.contains('stop-point')) {
      const point = parseLatLon(e.target.value);
      if (!point) return showError('Invalid stop coordinates – use "lat, lon"');
      stop.point = point;
      map.setView(point, map.getZoom());
      pointsChanged();
    } else {
      // Only the waiting time changed - the route stays the same.
      stop.dwell_min = Math.max(0, Math.round(Number(e.target.value) || 0));
      e.target.value = stop.dwell_min;
      showRouteSummary();
    }
  });

  $('stopRows').addEventListener('click', (e) => {
    if (!e.target.closest('.stop-remove')) return;
    state.stops.splice(Number(e.target.closest('.stop-row').dataset.index), 1);
    pointsChanged();
  });

  // Removing the end makes the last stop the new end.
  $('btnRemoveEnd').addEventListener('click', () => {
    if (!state.stops.length) return;
    state.end = state.stops.pop().point;
    pointsChanged();
  });

  // ---- Route ----------------------------------------------------------------

  async function generateRoute() {
    if (!state.start || !state.end) return;
    const request = ++state.routeRequest;
    state.generating = true;
    setRouteInfo('Generating route…');
    toggleSaveButtons();
    try {
      const route = await api.routes.generate(state.start, state.end, state.stops.map((s) => s.point));
      if (request !== state.routeRequest) return;
      state.preview = route;
      drawLine(route.points);
      showRouteSummary();
    } catch (err) {
      if (request !== state.routeRequest) return;
      state.preview = null;
      drawLine(null);
      setRouteInfo(`Route error: ${err.message}`, true);
    } finally {
      if (request === state.routeRequest) {
        state.generating = false;
        toggleSaveButtons();
      }
    }
  }

  // "15.5 km · ~33 min by car · 2 stops, 15 min waiting"
  function showRouteSummary() {
    const route = state.preview ?? state.savedRoute;
    if (!route) return;
    let text = state.preview
      ? `${fmtKm(route.distance_m)} · ~${fmtDuration(route.duration_s)} by car`
      : `${fmtKm(route.distance_m)} · saved route`;
    if (state.stops.length) {
      const n = state.stops.length;
      text += ` · ${n} stop${n > 1 ? 's' : ''}, ${fmtDuration(stopsMinutes() * 60)} waiting`;
    }
    setRouteInfo(text);
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
  // (Only when the text really changed - the field shows the point rounded to 6 digits.)
  for (const [inputId, key] of [['fStart', 'start'], ['fEnd', 'end']]) {
    $(inputId).addEventListener('change', (e) => {
      if (!e.target.value.trim() && !state[key]) return;
      const point = parseLatLon(e.target.value);
      if (point) {
        setEndpoint(key, point);
        map.setView(point, map.getZoom());
      } else {
        showError(`Invalid ${key} coordinates – use "lat, lon"`);
      }
    });
  }

  $('btnGenerateImei').addEventListener('click', async () => {
    try {
      $('fImei').value = (await api.devices.newImei()).imei;
    } catch (err) {
      showError(err.message);
    }
  });

  $('btnResetPoints').addEventListener('click', () => {
    state.start = state.end = state.preview = state.savedRoute = null;
    state.stops = [];
    state.routeRequest++;
    state.generating = false;
    toggleSaveButtons();
    drawLine(null);
    renderStops();
    redraw(false);
    setRouteInfo(HINT);
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
    $('fSpeed').value = 130;
    $('fRealistic').checked = true;
    state.start = state.end = state.preview = null;
    state.stops = [];
    state.routeRequest++;
    state.generating = false;
    toggleSaveButtons();
    state.savedDurationS = 0;
    state.savedRoute = null;
    if (map) drawLine(null);
    renderStops();
    setRouteInfo(HINT);
    scenario.reset();
    obd.reset();
    showError(null);
  }

  async function openNew() {
    state.editingId = null;
    $('deviceModalTitle').textContent = 'New device';
    $('btnSaveNew').classList.remove('d-none');
    $('btnGenerateImei').classList.remove('d-none');
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
    // Changing the IMEI of an existing device would break its link in Traccar.
    $('btnGenerateImei').classList.add('d-none');
    resetForm();

    $('fName').value = device.name;
    $('fImei').value = device.imei;
    $('fInterval').value = device.interval_s;
    $('fSpeed').value = device.speed_kmh;
    $('fRealistic').checked = device.realistic;
    await loadServers(device.server_id);

    if (device.route) {
      state.start = device.route.start;
      state.end = device.route.end;
      state.stops = device.route.stops.map((s) => ({ point: s.point, dwell_min: s.dwell_min }));
      state.savedDurationS = device.route.duration_s;
      state.savedRoute = device.route;
      renderStops();
      showRouteSummary();
    }
    scenario.load(device.scenario);
    obd.load(device.obd, device.codec);
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
      realistic: $('fRealistic').checked,
      scenario: scenario.value(),
      server_id: Number($('fServer').value) || null,
      start: state.start,
      end: state.end,
      stops: state.stops.map((s) => ({ point: s.point, dwell_min: s.dwell_min })),
    };
    // Send the previewed route so the backend does not call GraphHopper twice.
    if (state.preview) payload.route = state.preview;
    // OBD only when edited: a running device keeps counting its mileage meanwhile.
    if (obd.value()) payload.obd = obd.value();

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
