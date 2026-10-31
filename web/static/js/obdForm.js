// OBD settings of a device (see core/obd.py): a one-line summary in the device dialog,
// edited in its own "OBD" modal so the device dialog keeps its size.

import { api } from './api.js';
import { obdText } from './ui.js';

const $ = (id) => document.getElementById(id);
const DEFAULTS = {
  enabled: false, codec: '8', vin: '', odometer_km: 50000, service_km: 15000,
  fuel_pct: 70, tank_l: 55, dtcs: [], weak_battery: false,
};

export function initObdForm({ deviceModal }) {
  const modal = new bootstrap.Modal($('obdModal'));
  let current = { ...DEFAULTS };   // settings shown in the summary
  let edited = false;              // only edited settings are sent (the device updates the mileage while driving)

  function showSummary() {
    $('obdSummary').textContent = obdText(current, current.codec);
  }

  function load(obd, codec) {
    current = { ...DEFAULTS, ...(obd || {}), codec: codec || '8' };
    edited = false;
    showSummary();
  }

  // ---- OBD modal (the device dialog hides while it is open, like the scenario modal) ----

  $('btnObd').addEventListener('click', () => {
    fillFields(current);
    $('deviceModal').addEventListener('hidden.bs.modal', () => modal.show(), { once: true });
    deviceModal.hide();
  });
  $('obdModal').addEventListener('hidden.bs.modal', () => deviceModal.show());
  $('btnObdCancel').addEventListener('click', () => modal.hide());
  $('obdEnabled').addEventListener('change', showFields);

  $('btnGenerateVin').addEventListener('click', async () => {
    try {
      $('obdVin').value = (await api.devices.newVin()).vin;
    } catch (err) {
      $('obdError').textContent = err.message;
    }
  });

  $('btnObdApply').addEventListener('click', async () => {
    $('obdError').textContent = '';
    try {
      // The server validates and normalizes (VIN upper case, codec 8E when OBD is on, ...).
      current = await api.obd.check(readFields());
    } catch (err) {
      $('obdError').textContent = err.message;
      return;
    }
    edited = true;
    showSummary();
    modal.hide();
  });

  function showFields() {
    const enabled = $('obdEnabled').checked;
    $('obdFields').classList.toggle('opacity-50', !enabled);
    // OBD needs Codec 8 Extended (ids above 255, VIN and fault codes).
    if (enabled) $('obdCodec').value = '8E';
    $('obdCodec').disabled = enabled;
  }

  function fillFields(obd) {
    $('obdError').textContent = '';
    $('obdEnabled').checked = obd.enabled;
    $('obdCodec').value = obd.codec;
    $('obdVin').value = obd.vin || '';
    $('obdOdometer').value = Math.round(obd.odometer_km);
    $('obdService').value = Math.round(obd.service_km);
    $('obdFuel').value = Math.round(obd.fuel_pct);
    $('obdTank').value = Math.round(obd.tank_l);
    $('obdDtcs').value = obd.dtcs.join(', ');
    $('obdWeakBattery').checked = obd.weak_battery;
    showFields();
  }

  function readFields() {
    return {
      enabled: $('obdEnabled').checked,
      codec: $('obdCodec').value,
      vin: $('obdVin').value,
      odometer_km: Number($('obdOdometer').value),
      service_km: Number($('obdService').value),
      fuel_pct: Number($('obdFuel').value),
      tank_l: Number($('obdTank').value),
      dtcs: $('obdDtcs').value,
      weak_battery: $('obdWeakBattery').checked,
    };
  }

  return {
    load,
    reset: () => load(null, '8'),
    // null = not edited in this dialog -> the server keeps the stored values.
    value: () => (edited ? current : null),
  };
}
