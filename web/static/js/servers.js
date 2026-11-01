// Servers modal: list, add, edit, delete.
// The form (connection for devices + optional Traccar API access) opens only for "Add server"
// or "Edit", and one button at the bottom saves both parts together.

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
            <td>${esc(s.name)}</td>
            <td class="text-break">${esc(s.host)}:${s.port}</td>
            <td class="text-break small">${s.has_api
              ? `<i class="bi bi-check-circle text-success"></i> ${esc(s.api_url)}`
              : '<span class="text-secondary">–</span>'}</td>
            <td class="text-end text-nowrap">
              <button class="btn btn-sm btn-link" data-edit="${s.id}" title="Edit"><i class="bi bi-pencil"></i></button>
              <button class="btn btn-sm btn-link text-danger" data-delete="${s.id}" title="Delete"><i class="bi bi-trash"></i></button>
            </td>
          </tr>`).join('')
      : '<tr><td colspan="4" class="text-secondary">No servers yet – click “Add server”.</td></tr>';
  }

  // ---- Form ------------------------------------------------------------------------------

  function openForm(server) {
    const s = server || {};
    $('serverForm').reset();
    $('sId').value = s.id || '';
    $('sName').value = s.name || '';
    $('sHost').value = s.host || '';
    $('sPort').value = s.port || 5027;
    $('sApiEnabled').checked = Boolean(s.has_api);
    $('sApiUrl').value = s.api_url || '';
    $('sApiUser').value = s.api_user || '';
    // The password is never sent to the browser: empty = keep the stored one.
    $('sApiPassword').value = '';
    $('sApiPassword').placeholder = s.api_password_set ? 'unchanged – type to replace' : '';
    $('serverFormTitle').textContent = server ? `Edit server “${server.name}”` : 'New server';
    $('btnServerSave').textContent = server ? 'Save changes' : 'Add server';
    showApiResult('');
    showApiFields();
    $('serverForm').classList.remove('d-none');
    $('btnServerNew').disabled = true;
    $('sName').focus();
  }

  function closeForm() {
    $('serverForm').classList.add('d-none');
    $('btnServerNew').disabled = false;
  }

  function showApiFields() {
    const on = $('sApiEnabled').checked;
    $('serverApiFields').classList.toggle('d-none', !on);
    // Traccar's web/API port is 8082 by default - suggest it for the host of the server.
    if (on && !$('sApiUrl').value && $('sHost').value.trim()) {
      $('sApiUrl').value = `http://${$('sHost').value.trim()}:8082`;
    }
    $('serverSaveHint').textContent = on
      ? 'Saves the server and its Traccar API access.'
      : ($('sId').value && servers.find((x) => x.id === Number($('sId').value))?.has_api
        ? 'Saves the server and removes its Traccar API access.'
        : 'Saves the server (without Traccar API access).');
  }

  $('serversModal').addEventListener('show.bs.modal', () => {
    closeForm();
    load().catch((err) => toast(err.message));
  });
  $('btnServerNew').addEventListener('click', () => openForm(null));
  $('btnServerCancel').addEventListener('click', closeForm);
  $('sApiEnabled').addEventListener('change', showApiFields);

  $('serverTable').addEventListener('click', async (e) => {
    const editBtn = e.target.closest('[data-edit]');
    const deleteBtn = e.target.closest('[data-delete]');

    if (editBtn) openForm(servers.find((x) => x.id === Number(editBtn.dataset.edit)));

    if (deleteBtn) {
      const s = servers.find((x) => x.id === Number(deleteBtn.dataset.delete));
      if (!confirm(`Delete server "${s.name}"?`)) return;
      try {
        await api.servers.remove(s.id);
        if (Number($('sId').value) === s.id) closeForm();
        await load();
        onChanged();
      } catch (err) {
        toast(err.message);
      }
    }
  });

  // Switched off = no API access (an existing one is removed on save).
  function apiFields() {
    if (!$('sApiEnabled').checked) return { api_url: '', api_user: '', api_password: '' };
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
      showApiResult(`✓ Connected – Traccar ${res.version || ''}. Not saved yet – click “${$('btnServerSave').textContent}”.`);
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
      const saved = id ? await api.servers.update(id, data) : await api.servers.create(data);
      closeForm();
      await load();
      onChanged();
      toast(`Server “${saved.name}” saved${saved.has_api ? ' with Traccar API access' : ''}`, 'success');
    } catch (err) {
      showApiResult('');
      toast(err.message);
    }
  });
}
