// Scenario of a device (see core/scenarios.py): a one-line summary in the device dialog,
// edited in its own "Scenario" modal so the device dialog keeps its size.

import { api } from './api.js';
import { esc, scenarioText } from './ui.js';

const $ = (id) => document.getElementById(id);
const DAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];
const DEFAULTS = {
  times: '07:30, 16:30', every_min: 60, from: '06:00', to: '22:00',
  round_trip: true, dwell_min: 5, jitter_min: 5, days: [0, 1, 2, 3, 4],
};

export function initScenarioForm({ deviceModal, durationS, deviceId }) {
  const modal = new bootstrap.Modal($('scenarioModal'));
  let current = null;   // scenario saved with the device (null = manual)

  // Day toggle buttons (Bootstrap "btn-check")
  $('scDays').innerHTML = DAYS.map((d, i) => `
    <input type="checkbox" class="btn-check" id="scDay${i}" autocomplete="off">
    <label class="btn btn-outline-secondary btn-sm px-2 py-0" for="scDay${i}">${d}</label>`).join('');

  let previewTimer = null;
  const schedulePreview = () => {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(preview, 300);
  };
  $('scType').addEventListener('change', () => { showFields(); schedulePreview(); });
  for (const el of $('scFields').querySelectorAll('input')) {
    el.addEventListener('change', schedulePreview);
  }

  // ---- Summary in the device dialog -----------------------------------------

  function showSummary() {
    $('scSummary').textContent = current ? scenarioText(current) : 'Manual – drive once';
  }

  function load(scenario) {
    current = scenario || null;
    showSummary();
  }

  // ---- Scenario modal ----------------------------------------------------------
  // Bootstrap can't show two modals at once: the device dialog hides while this one is open
  // and comes back (with its form and map untouched) when this one closes.

  $('btnScenario').addEventListener('click', () => {
    fillFields(current);
    $('deviceModal').addEventListener('hidden.bs.modal', () => modal.show(), { once: true });
    deviceModal.hide();
  });
  $('scenarioModal').addEventListener('hidden.bs.modal', () => deviceModal.show());
  $('btnScenarioCancel').addEventListener('click', () => modal.hide());

  $('btnScenarioApply').addEventListener('click', async () => {
    let scenario = readFields();
    if (scenario) {
      try {
        // The server validates and normalizes it (e.g. "7:30,16:30" -> ["07:30", "16:30"]).
        scenario = (await api.scenarios.preview(scenario, durationS(), deviceId())).scenario;
      } catch (err) {
        $('scPreview').innerHTML = `<div class="text-danger">${esc(err.message)}</div>`;
        return;
      }
    }
    current = scenario;
    showSummary();
    modal.hide();
  });

  function showFields() {
    const type = $('scType').value;
    $('scFields').classList.toggle('d-none', !type);
    for (const el of $('scFields').querySelectorAll('.sc-times, .sc-interval, .sc-loop')) {
      el.classList.toggle('d-none', !type || !el.classList.contains(`sc-${type}`));
    }
  }

  function fillFields(scenario) {
    const s = { ...DEFAULTS, ...(scenario || {}) };
    $('scType').value = scenario ? scenario.type : '';
    $('scTimes').value = Array.isArray(s.times) ? s.times.join(', ') : s.times;
    $('scEvery').value = s.every_min;
    $('scFrom').value = s.from;
    $('scTo').value = s.to === '24:00' ? '23:59' : s.to;
    $('scRoundTrip').checked = s.round_trip;
    $('scDwell').value = s.dwell_min;
    $('scJitter').value = s.jitter_min;
    DAYS.forEach((_, i) => { $(`scDay${i}`).checked = s.days.includes(i); });
    showFields();
    preview();
  }

  // The scenario as sent to the API (null = manual).
  function readFields() {
    const type = $('scType').value;
    if (!type) return null;
    const scenario = {
      type,
      days: DAYS.map((_, i) => i).filter((i) => $(`scDay${i}`).checked),
      jitter_min: Number($('scJitter').value) || 0,
    };
    if (type === 'times') scenario.times = $('scTimes').value;
    if (type !== 'times') {
      scenario.from = $('scFrom').value || '00:00';
      scenario.to = $('scTo').value || '23:59';
      scenario.dwell_min = Number($('scDwell').value) || 0;
    }
    if (type === 'interval') {
      scenario.every_min = Number($('scEvery').value);
      scenario.round_trip = $('scRoundTrip').checked;
    }
    return scenario;
  }

  // Upcoming departures + warnings, calculated by the server (same code as the emulator).
  async function preview() {
    const scenario = readFields();
    const el = $('scPreview');
    if (!scenario) {
      el.innerHTML = '';
      return;
    }
    try {
      // The device id makes the random shifts the same as the emulator will use.
      const p = await api.scenarios.preview(scenario, durationS(), deviceId());
      el.innerHTML = `<div class="text-secondary">Next departures (${esc(p.timezone)}):</div>`
        + p.trips.map((t) => `<div>${esc(t.at)} &nbsp; ${esc(t.legs)}</div>`).join('')
        + p.warnings.map((w) => `<div class="text-warning-emphasis">⚠ ${esc(w)}</div>`).join('');
    } catch (err) {
      el.innerHTML = `<div class="text-danger">${esc(err.message)}</div>`;
    }
  }

  return { load, value: () => current, reset: () => load(null) };
}
