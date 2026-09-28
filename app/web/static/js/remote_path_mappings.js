// Settings -> Download Clients page: editable list of remote path mappings
// (issue #69).
//
// Like Connect, this has no dedicated REST CRUD -- it is part of the single
// settings document (Settings.remote_path_mappings) and saves through the
// same GET-merge-PUT flow as the rest of settings.js (see saveSettings()).
// This file only owns rendering/editing of the list; settings.js calls into
// populate()/collectForSave() at the right points in its own load/save flow.
// No secrets here, so unlike connect.js there is no masked-field handling.

// Wrapped in an IIFE: this page also loads settings.js and connect.js, both
// of which declare a top-level `const T` -- see the same note in tags.js.
(() => {
const T = window.AUDIARR_I18N || {};

let state = [];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function newRow() {
  return { id: crypto.randomUUID(), host: "", remote_path: "", local_path: "", enabled: true };
}

function paint() {
  const el = document.getElementById("rpm-list");
  if (!el) return;
  if (!state.length) {
    el.innerHTML = `<p class="muted">${esc(T.settings_rpm_empty)}</p>`;
    return;
  }
  el.innerHTML = state
    .map(
      (row) => `
    <div class="settings-subsection repeat-row" data-rpm-row="${row.id}">
      <div class="repeat-row-head">
        <h3>${esc(row.host) || esc(T.settings_rpm_untitled)}</h3>
        <button type="button" class="btn btn-secondary" data-rpm-remove="${row.id}">${esc(T.settings_remove)}</button>
      </div>
      <label class="inline-check">
        <input type="checkbox" data-rpm-field="enabled" data-rpm-row="${row.id}" ${row.enabled ? "checked" : ""}>
        ${esc(T.settings_enabled_label)}
      </label>
      <label>${esc(T.settings_rpm_host_label)}
        <input type="text" data-rpm-field="host" data-rpm-row="${row.id}" value="${esc(row.host)}" placeholder="SABnzbd">
      </label>
      <label>${esc(T.settings_rpm_remote_path_label)}
        <input type="text" data-rpm-field="remote_path" data-rpm-row="${row.id}" value="${esc(row.remote_path)}" placeholder="/downloads/complete">
      </label>
      <label>${esc(T.settings_rpm_local_path_label)}
        <input type="text" data-rpm-field="local_path" data-rpm-row="${row.id}" value="${esc(row.local_path)}" placeholder="/data/usenet/complete">
      </label>
    </div>`
    )
    .join("");
  bindRowEvents();
}

function findRow(id) {
  return state.find((r) => r.id === id);
}

function bindRowEvents() {
  const el = document.getElementById("rpm-list");
  if (!el) return;

  el.querySelectorAll("[data-rpm-field]").forEach((input) => {
    const apply = () => {
      const row = findRow(input.dataset.rpmRow);
      if (!row) return;
      const field = input.dataset.rpmField;
      row[field] = input.type === "checkbox" ? input.checked : input.value;
    };
    input.addEventListener("input", apply);
    input.addEventListener("change", apply);
  });

  el.querySelectorAll("[data-rpm-remove]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state = state.filter((r) => r.id !== btn.dataset.rpmRemove);
      paint();
      if (window.setDirty) window.setDirty(true);
    });
  });
}

function populate(mappings) {
  state = (mappings || []).map((m) => ({
    id: m.id || crypto.randomUUID(),
    host: m.host || "",
    remote_path: m.remote_path || "",
    local_path: m.local_path || "",
    enabled: m.enabled !== false,
  }));
  paint();
}

function collectForSave() {
  return state
    .filter((row) => row.remote_path.trim() && row.local_path.trim())
    .map((row) => ({
      id: row.id,
      host: row.host.trim(),
      remote_path: row.remote_path.trim(),
      local_path: row.local_path.trim(),
      enabled: Boolean(row.enabled),
    }));
}

window.AudiarrRemotePathMappings = {
  populate,
  collectForSave,
  addRow() {
    state.push(newRow());
    paint();
  },
};

document.addEventListener("DOMContentLoaded", () => {
  const addBtn = document.getElementById("rpm-add-btn");
  if (!addBtn) return;
  addBtn.addEventListener("click", () => {
    window.AudiarrRemotePathMappings.addRow();
    if (window.setDirty) window.setDirty(true);
  });
});

})();
