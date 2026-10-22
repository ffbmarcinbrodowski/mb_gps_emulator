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
            <td>${esc(s.name)}</td>
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

  $('serverForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = { name: $('sName').value.trim(), host: $('sHost').value.trim(), port: Number($('sPort').value) };
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
