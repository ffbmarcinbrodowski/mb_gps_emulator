// Servers modal: list, add, edit, delete.

import { api } from './api.js';
import { esc, toast } from './ui.js';

const $ = (id) => document.getElementById(id);

export function initServers({ onChanged }) {
  let servers = [];

  async function load() {
    servers = await api.servers.list();
    $('serverTable').innerHTML = servers.length
      ? servers.map((s) => `
          <tr>
            <td>${esc(s.name)}${s.has_api
              ? ` <span class="badge text-bg-light border" title="Traccar API: ${esc(s.api_url)}">API</span>` : ''}</td>
            <td class="text-break">${esc(s.host)}</td>
            <td>${s.port}</td>
            <td class="text-end text-nowrap">
              <button class="btn btn-sm btn-link" data-edit="${s.id}" title="Edit"><i class="bi bi-pencil"></i></button>
              <button class="btn btn-sm btn-link text-danger" data-delete="${s.id}" title="Delete"><i class="bi bi-trash"></i></button>
            </td>
          </tr>`).join('')
      : '<tr><td colspan="4" class="text-secondary">No servers yet.</td></tr>';
  }

  function resetForm() {
    $('serverForm').reset();
    $('sId').value = '';
    $('sApiPassword').placeholder = 'Password';
    showApiResult('');
    $('btnServerSave').textContent = 'Add';
    $('btnServerCancel').classList.add('d-none');
  }

  $('serversModal').addEventListener('show.bs.modal', () => {
    resetForm();
    load().catch((err) => toast(err.message));
  });

  $('serverTable').addEventListener('click', async (e) => {
    const editBtn = e.target.closest('[data-edit]');
    const deleteBtn = e.target.closest('[data-delete]');

    if (editBtn) {
      const s = servers.find((x) => x.id === Number(editBtn.dataset.edit));
      $('sId').value = s.id;
      $('sName').value = s.name;
      $('sHost').value = s.host;
      $('sPort').value = s.port;
      $('sApiUrl').value = s.api_url;
      $('sApiUser').value = s.api_user;
      // The password is never sent to the browser: empty = keep the stored one.
      $('sApiPassword').value = '';
      $('sApiPassword').placeholder = s.api_password_set ? 'Password (unchanged)' : 'Password';
      showApiResult('');
      $('btnServerSave').textContent = 'Save';
      $('btnServerCancel').classList.remove('d-none');
    }

    if (deleteBtn) {
      const s = servers.find((x) => x.id === Number(deleteBtn.dataset.delete));
      if (!confirm(`Delete server "${s.name}"?`)) return;
      try {
        await api.servers.remove(s.id);
        await load();
        onChanged();
      } catch (err) {
        toast(err.message);
      }
    }
  });

  $('btnServerCancel').addEventListener('click', resetForm);

  function apiFields() {
    return {
      api_url: $('sApiUrl').value.trim(),
      api_user: $('sApiUser').value.trim(),
      api_password: $('sApiPassword').value,
    };
  }

  function showApiResult(text, ok = true) {
    const el = $('serverApiResult');
    el.textContent = text;
    el.classList.toggle('text-success', ok);
    el.classList.toggle('text-danger', !ok);
  }

  $('btnServerTestApi').addEventListener('click', async () => {
    showApiResult('Connecting…');
    try {
      const res = await api.servers.testApi({ ...apiFields(), server_id: Number($('sId').value) || null });
      showApiResult(`✓ Connected – Traccar ${res.version || ''}`);
    } catch (err) {
      showApiResult(err.message, false);
    }
  });

  $('serverForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = {
      name: $('sName').value.trim(), host: $('sHost').value.trim(), port: Number($('sPort').value), ...apiFields(),
    };
    try {
      const id = $('sId').value;
      if (id) await api.servers.update(id, data);
      else await api.servers.create(data);
      resetForm();
      await load();
      onChanged();
    } catch (err) {
      toast(err.message);
    }
  });
}
