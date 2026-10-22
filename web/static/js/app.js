// Main window: device list + live map. Positions are refreshed by polling.

import { api } from './api.js';
import { initDeviceForm } from './deviceForm.js';
import { initHistory } from './history.js';
import { createMap, deviceIcon, endpointMarker, routeLine } from './map.js';
import { initServers } from './servers.js';
import { STATUS_BADGE, esc, fmtCoord, fmtKm, scenarioText, toast } from './ui.js';

const POLL_MS = 2000;
const $ = (id) => document.getElementById(id);

const state = {
  devices: [],
  selectedId: null,
  markers: {},        // device id -> Leaflet marker
  routeLayer: null,   // route of the selected device (polyline + start/end)
  routeKey: null,     // "<device id>:<route id>" currently drawn
  firstLoad: true,
};

const map = createMap('mainMap');
const deviceForm = initDeviceForm({
  onSaved: (device) => {
    state.selectedId = device.id;
    state.routeKey = null; // force redraw of the (possibly new) route
    refresh();
  },
});
initServers({ onChanged: refresh });
const historyDialog = initHistory({ onFinished: refresh });

// ---- Data loading -----------------------------------------------------------

async function refresh() {
  try {
    state.devices = await api.devices.list();
  } catch (err) {
    console.error(err);
    return;
  }
  if (!state.devices.some((d) => d.id === state.selectedId)) {
    state.selectedId = state.devices[0]?.id ?? null;
  }
  renderList();
  renderMarkers();
  renderSelected(state.firstLoad);
  state.firstLoad = false;
}

// ---- Device list ------------------------------------------------------------

function renderList() {
  const list = $('deviceList');
  if (!state.devices.length) {
    list.innerHTML = '<div class="text-secondary p-3">No devices yet – click <i class="bi bi-plus-lg"></i> to add one.</div>';
  } else {
    list.innerHTML = state.devices.map(deviceCard).join('');
  }
  const hasSelection = state.selectedId != null;
  $('btnEditDevice').disabled = !hasSelection;
  $('btnDeleteDevice').disabled = !hasSelection;
}

function deviceCard(d) {
  const r = d.route;
  const running = d.status === 'running';
  const status = d.display_status;
  return `
    <div class="device-card ${d.id === state.selectedId ? 'selected' : ''}" data-id="${d.id}">
      <div class="d-flex align-items-center">
        <span class="name me-3 text-truncate">${esc(d.name)}</span>
        <button class="action" data-action="start" ${running ? 'disabled' : ''}>Start</button>
        <button class="action" data-action="stop" ${running ? '' : 'disabled'}>Stop</button>
        <span class="ms-auto d-flex gap-1">
          <span class="badge text-bg-${STATUS_BADGE[status]} text-capitalize">${status}</span>
        </span>
      </div>
      <div class="coords mt-1">
        <span>Start Lat Lon</span><span>${fmtCoord(r?.start[0])}</span><span>${fmtCoord(r?.start[1])}</span>
        <span>End&nbsp;&nbsp; Lat Lon</span><span>${fmtCoord(r?.end[0])}</span><span>${fmtCoord(r?.end[1])}</span>
      </div>
      <div class="progress my-1" title="${d.progress_pct}%">
        <div class="progress-bar" style="width:${d.progress_pct}%"></div>
      </div>
      <div class="meta text-secondary">
        IMEI ${esc(d.imei)} · ${r ? fmtKm(r.distance_m) : 'no route'} · ${d.interval_s} s · max ${d.speed_kmh} km/h
        · odo ${fmtKm(d.odometer_m)}${d.realistic ? '' : ' · simple'}
        · ${d.server ? esc(`${d.server.host}:${d.server.port}`) : 'no server'}
      </div>
      ${d.scenario ? `<div class="meta text-info-emphasis">
        <i class="bi bi-calendar-week"></i> ${esc(scenarioText(d.scenario))}
        ${d.activity === 'driving' ? ` · driving ${d.direction === 'return' ? 'B → A' : 'A → B'}` : ''}
        ${d.next_trip_at ? ` · next ${esc(d.next_trip_at)}` : ''}</div>` : ''}
      ${d.last_event ? `<div class="meta text-warning-emphasis">
        <i class="bi bi-lightning-charge"></i> Last event: ${esc(d.last_event)} · ${esc(d.last_event_at)} UTC</div>` : ''}
      ${d.last_error ? `<div class="meta text-danger text-break">${esc(d.last_error)}</div>` : ''}
    </div>`;
}

$('deviceList').addEventListener('click', async (e) => {
  const card = e.target.closest('.device-card');
  if (!card) return;
  const id = Number(card.dataset.id);
  const action = e.target.closest('[data-action]')?.dataset.action;

  if (action) {
    try {
      if (action === 'start') await api.emulator.start(id);
      else await api.emulator.stop(id);
    } catch (err) {
      toast(err.message);
    }
    refresh();
    return;
  }
  selectDevice(id, true);
});

function selectDevice(id, fit) {
  state.selectedId = id;
  state.routeKey = null;
  renderList();
  renderSelected(fit);
}

// ---- Map --------------------------------------------------------------------

function renderMarkers() {
  const seen = new Set();
  for (const d of state.devices) {
    // Before the first start the device sits at the route start.
    const pos = d.current ? [d.current.lat, d.current.lon] : d.route?.start;
    if (!pos) continue;
    seen.add(d.id);

    let marker = state.markers[d.id];
    if (!marker) {
      marker = L.marker(pos, { zIndexOffset: 1000 }).addTo(map);
      marker.bindTooltip('', { permanent: true, direction: 'right', offset: [14, 0], className: 'device-label' });
      marker.on('click', () => selectDevice(d.id, false));
      state.markers[d.id] = marker;
    }
    marker.setLatLng(pos);
    marker.setIcon(deviceIcon(d.display_status, d.current?.bearing ?? 0));
    marker.setTooltipContent(esc(d.name));
  }
  // Remove markers of deleted devices.
  for (const id of Object.keys(state.markers)) {
    if (!seen.has(Number(id))) {
      map.removeLayer(state.markers[id]);
      delete state.markers[id];
    }
  }
  if (state.firstLoad && seen.size) {
    const bounds = L.latLngBounds(Object.values(state.markers).map((m) => m.getLatLng()));
    map.fitBounds(bounds, { padding: [60, 60], maxZoom: 14 });
  }
}

async function renderSelected(fit = false) {
  const d = state.devices.find((x) => x.id === state.selectedId);
  $('mapTitle').textContent = d ? `Device: ${d.name}` : 'Device: –';
  $('mapInfo').textContent = d
    ? `${d.display_status}${d.current?.speed ? ` · ${Math.round(d.current.speed)} km/h` : ''} · ${d.progress_pct}%`
      + (d.last_sent_at ? ` · last sent ${d.last_sent_at} UTC` : '')
    : '';
  // Events need an open connection, so only a running device can send them.
  $('btnEvent').disabled = d?.status !== 'running';
  // History follows the scenario and must not mix with live data.
  $('btnHistory').disabled = !d?.scenario || d.status === 'running';

  // The route geometry is only fetched when the selection or the route changes.
  const key = d?.route ? `${d.id}:${d.route.id}` : null;
  if (key === state.routeKey) return;
  state.routeKey = key;

  if (state.routeLayer) map.removeLayer(state.routeLayer);
  state.routeLayer = null;
  if (!key) return;

  const full = await api.devices.get(d.id);
  if (state.routeKey !== key) return; // selection changed while loading
  const line = routeLine(full.route.points);
  const layers = [line, endpointMarker(full.route.start, 'start'), endpointMarker(full.route.end, 'end')];
  if (full.route.return_points) {
    // Return route B -> A (scenarios): orange, dashed.
    layers.unshift(L.polyline(full.route.return_points.map((p) => [p[0], p[1]]),
      { color: '#fb8c00', weight: 3, opacity: 0.8, dashArray: '6 6' }));
  }
  state.routeLayer = L.layerGroup(layers).addTo(map);
  if (fit) map.fitBounds(line.getBounds(), { padding: [40, 40] });
}

// ---- Toolbar buttons --------------------------------------------------------

$('btnAddDevice').addEventListener('click', () => deviceForm.openNew().catch((e) => toast(e.message)));

$('btnEditDevice').addEventListener('click', () => {
  if (state.selectedId) deviceForm.openEdit(state.selectedId).catch((e) => toast(e.message));
});

$('btnDeleteDevice').addEventListener('click', async () => {
  const d = state.devices.find((x) => x.id === state.selectedId);
  if (!d || !confirm(`Delete device "${d.name}"?`)) return;
  try {
    await api.devices.remove(d.id);
    state.selectedId = null;
    state.routeKey = null;
    refresh();
  } catch (err) {
    toast(err.message);
  }
});

$('btnStartAll').addEventListener('click', async () => {
  try {
    const res = await api.emulator.startAll();
    toast(`Started ${res.started.length} device(s)`, 'success');
  } catch (err) {
    toast(err.message);
  }
  refresh();
});

$('btnStopAll').addEventListener('click', async () => {
  try {
    await api.emulator.stopAll();
  } catch (err) {
    toast(err.message);
  }
  refresh();
});

// ---- Events (crash, towing, ...) ---------------------------------------------

async function initEventMenu() {
  const events = await api.emulator.events();
  $('eventMenu').innerHTML = events.map((e) => `
      <li><button class="dropdown-item" data-event="${e.type}" data-label="${esc(e.label)}">
        ${esc(e.label)}${e.needs_model ? ' *' : ''}
        <div class="traccar text-secondary">Traccar: ${esc(e.traccar)}</div>
      </button></li>`).join('')
    + `<li><hr class="dropdown-divider"></li>
       <li><span class="dropdown-item-text small text-secondary">
         * Traccar decodes it only if the device's <b>Model</b> is set, e.g. FMB920
       </span></li>`;
}

$('eventMenu').addEventListener('click', async (e) => {
  const item = e.target.closest('[data-event]');
  const d = state.devices.find((x) => x.id === state.selectedId);
  if (!item || !d) return;
  try {
    await api.emulator.sendEvent(d.id, item.dataset.event);
    toast(`${item.dataset.label} sent from ${d.name}`, 'success');
  } catch (err) {
    toast(err.message);
  }
  refresh();
});

$('btnHistory').addEventListener('click', () => {
  const d = state.devices.find((x) => x.id === state.selectedId);
  if (d) historyDialog.open(d);
});

// ---- Start ------------------------------------------------------------------

initEventMenu().catch((err) => toast(err.message));
refresh();
setInterval(refresh, POLL_MS);
