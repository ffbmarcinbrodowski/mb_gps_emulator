// "Generate history" dialog: past data from the device's scenario (see core/history.py).

import { api } from './api.js';
import { esc, scenarioText } from './ui.js';

const $ = (id) => document.getElementById(id);
const POLL_MS = 1000;

export function initHistory({ onFinished }) {
  const modal = new bootstrap.Modal($('historyModal'));
  let device = null;
  let timer = null;

  function open(d) {
    device = d;
    $('historyTitle').textContent = `Generate history: ${d.name}`;
    $('historyScenario').innerHTML = `<i class="bi bi-calendar-week"></i> Scenario: ${esc(scenarioText(d.scenario))}`;
    modal.show();
    poll();   // a job may still be running from before
  }

  async function poll() {
    clearTimeout(timer);
    if (!device) return;
    let s;
    try {
      s = await api.history.status(device.id);
    } catch (err) {
      showStatus(null, err.message);
      return;
    }
    showStatus(s);
    if (s.state === 'running') timer = setTimeout(poll, POLL_MS);
    else if (s.state) onFinished();
  }

  function showStatus(s, error) {
    const running = s?.state === 'running';
    $('historyProgressBox').classList.toggle('d-none', !s?.state && !error);
    $('btnHistoryStart').disabled = running;
    $('btnHistoryCancel').classList.toggle('d-none', !running);
    if (error) {
      $('historyStatus').innerHTML = `<span class="text-danger">${esc(error)}</span>`;
      return;
    }
    if (!s?.state) return;
    const bar = $('historyProgress');
    bar.style.width = `${s.progress}%`;
    bar.classList.toggle('progress-bar-animated', running);
    bar.classList.toggle('bg-danger', s.state === 'failed');
    const text = {
      running: `Generating… ${s.progress}% · ${s.sent.toLocaleString()} records sent`,
      done: `Done: ${s.sent.toLocaleString()} records sent`,
      cancelled: `Cancelled after ${s.sent.toLocaleString()} records`,
      failed: `Failed after ${s.sent.toLocaleString()} records: ${s.error}`,
    }[s.state];
    $('historyStatus').innerHTML = `${esc(text)}<div class="text-secondary">${esc(s.period)}</div>`;
  }

  $('btnHistoryStart').addEventListener('click', async () => {
    // Clear the result of a previous run before the new one reports.
    showStatus({ state: 'running', progress: 0, sent: 0, period: '' });
    try {
      await api.history.start(device.id, Number($('historyDays').value));
      poll();
    } catch (err) {
      showStatus(null, err.message);
    }
  });

  $('btnHistoryCancel').addEventListener('click', () => api.history.cancel(device.id));
  $('historyModal').addEventListener('hidden.bs.modal', () => { clearTimeout(timer); device = null; });

  return { open };
}
