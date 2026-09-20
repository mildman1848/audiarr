// Settings -> Connect page: editable list of outbound webhooks (issue #28).
//
// Unlike Tags, Connect has no dedicated REST CRUD -- it is part of the
// single settings document (Settings.connect) and saves through the same
// GET-merge-PUT flow as the rest of settings.js (see saveSettings()).
// This file only owns rendering/editing of the list and the per-row Test
// button; settings.js calls into populate()/collectForSave() at the right
// points in its own load/save flow.
//
// Secrets: header_value is never populated from the settings GET response
// into an input field -- a stored value only ever shows as a masked
// placeholder, and a blank field on save means "keep the stored value"
// (collectForSave falls back to the value already in the freshly-fetched
// document passed in by settings.js).

// Wrapped in an IIFE: this page also loads settings.js, and both modules
// declare a top-level `const T` -- a bare top-level declaration here makes
// the whole script die with "Identifier T has already been declared" (see
// the same note in tags.js).
(() => {
const T = window.AUDIARR_I18N || {};
const MASK = "•••••";

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
  return {
    id: crypto.randomUUID(),
    name: "",
    enabled: false,
    url: "",
    on_grab: true,
    on_import: true,
    on_health_issue: false,
    header_name: "",
    header_value: "",
    _hasHeaderValue: false,
    last_event: "",
    last_status: "",
    last_status_code: null,
    last_error: "",
    last_delivered_at: "",
  };
}

function deliverySummary(row) {
  if (!row.last_delivered_at) return T.settings_connect_never;
  const parts = [row.last_event, row.last_status];
  if (row.last_status_code != null) parts.push(`(${row.last_status_code})`);
  let text = `${parts.filter(Boolean).join(" ")} @ ${row.last_delivered_at}`;
  if (row.last_error) text += ` — ${row.last_error}`;
  return text;
}

function paint() {
  const el = document.getElementById("connect-list");
  if (!el) return;
  if (!state.length) {
    el.innerHTML = `<p class="muted">${esc(T.settings_connect_empty)}</p>`;
    return;
  }
  el.innerHTML = state
    .map(
      (row) => `
    <div class="settings-subsection repeat-row" data-connect-row="${row.id}">
      <div class="repeat-row-head">
        <h3>${esc(row.name) || esc(T.settings_connect_untitled)}</h3>
        <button type="button" class="btn btn-secondary" data-connect-remove="${row.id}">${esc(T.settings_remove)}</button>
      </div>
      <label class="inline-check">
        <input type="checkbox" data-connect-field="enabled" data-connect-row="${row.id}" ${row.enabled ? "checked" : ""}>
        ${esc(T.settings_enabled_label)}
      </label>
      <label>${esc(T.settings_field_name)}
        <input type="text" data-connect-field="name" data-connect-row="${row.id}" value="${esc(row.name)}">
      </label>
      <label>${esc(T.settings_connect_url_label)}
        <input type="text" data-connect-field="url" data-connect-row="${row.id}" value="${esc(row.url)}" placeholder="https://example.com/webhook">
      </label>
      <p class="muted small">${esc(T.settings_connect_events_label)}</p>
      <label class="inline-check">
        <input type="checkbox" data-connect-field="on_grab" data-connect-row="${row.id}" ${row.on_grab ? "checked" : ""}>
        ${esc(T.settings_connect_on_grab)}
      </label>
      <label class="inline-check">
        <input type="checkbox" data-connect-field="on_import" data-connect-row="${row.id}" ${row.on_import ? "checked" : ""}>
        ${esc(T.settings_connect_on_import)}
      </label>
      <label class="inline-check">
        <input type="checkbox" data-connect-field="on_health_issue" data-connect-row="${row.id}" ${row.on_health_issue ? "checked" : ""}>
        ${esc(T.settings_connect_on_health_issue)}
      </label>
      <label>${esc(T.settings_connect_header_name_label)}
        <input type="text" data-connect-field="header_name" data-connect-row="${row.id}" value="${esc(row.header_name)}" placeholder="X-Api-Key">
      </label>
      <label>${esc(T.settings_connect_header_value_label)}
        <input type="password" data-connect-field="header_value" data-connect-row="${row.id}" placeholder="${row._hasHeaderValue ? MASK : ""}" autocomplete="new-password">
      </label>
      <p class="muted small">${esc(T.settings_connect_header_hint)}</p>
      <div class="button-row">
        <button type="button" class="btn btn-secondary" data-connect-test="${row.id}">${esc(T.settings_test)}</button>
        <span class="muted" role="status" data-connect-msg="${row.id}"></span>
      </div>
      <p class="muted small">${esc(T.settings_connect_last_delivery_label)}: ${esc(deliverySummary(row))}</p>
    </div>`
    )
    .join("");
  bindRowEvents();
}

function findRow(id) {
  return state.find((r) => r.id === id);
}

function bindRowEvents() {
  const el = document.getElementById("connect-list");
  if (!el) return;

  el.querySelectorAll("[data-connect-field]").forEach((input) => {
    const apply = () => {
      const row = findRow(input.dataset.connectRow);
      if (!row) return;
      const field = input.dataset.connectField;
      row[field] = input.type === "checkbox" ? input.checked : input.value;
    };
    input.addEventListener("input", apply);
    input.addEventListener("change", apply);
  });

  el.querySelectorAll("[data-connect-remove]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state = state.filter((r) => r.id !== btn.dataset.connectRemove);
      paint();
      if (window.setDirty) window.setDirty(true);
    });
  });

  el.querySelectorAll("[data-connect-test]").forEach((btn) => {
    btn.addEventListener("click", () => testRow(btn.dataset.connectTest));
  });
}

async function testRow(id) {
  const row = findRow(id);
  if (!row) return;
  const msgEl = document.querySelector(`[data-connect-msg="${id}"]`);
  if (msgEl) msgEl.textContent = T.settings_testing;
  try {
    const resp = await fetch(`/api/v1/connect/test/${encodeURIComponent(id)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: row.url.trim(),
        header_name: row.header_name.trim(),
        header_value: row.header_value,
      }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    const text = `${data.ok ? "✓" : "✗"} ${data.message || ""}`.trim();
    if (msgEl) msgEl.textContent = text;
    if (window.AudiarrToast) {
      (data.ok ? window.AudiarrToast.success : window.AudiarrToast.error)(text);
    }
  } catch (err) {
    const text = `${T.settings_test_error} (${err.message})`;
    if (msgEl) msgEl.textContent = text;
    if (window.AudiarrToast) window.AudiarrToast.error(text);
  }
}

function populate(connectList) {
  state = (connectList || []).map((c) => ({
    id: c.id || crypto.randomUUID(),
    name: c.name || "",
    enabled: Boolean(c.enabled),
    url: c.url || "",
    on_grab: c.on_grab !== false,
    on_import: c.on_import !== false,
    on_health_issue: Boolean(c.on_health_issue),
    header_name: c.header_name || "",
    header_value: "",
    _hasHeaderValue: Boolean(c.header_value),
    last_event: c.last_event || "",
    last_status: c.last_status || "",
    last_status_code: c.last_status_code ?? null,
    last_error: c.last_error || "",
    last_delivered_at: c.last_delivered_at || "",
  }));
  paint();
}

// existingConnect is doc.connect from the fresh GET settings.js already did
// at the top of saveSettings() -- it still carries the real, unmasked
// header_value for every already-saved entry, which is how a blank
// password field on a row here means "keep the stored secret".
function collectForSave(existingConnect) {
  const byId = new Map((existingConnect || []).map((c) => [c.id, c]));
  return state
    .filter((row) => row.name.trim() && row.url.trim())
    .map((row) => {
      const stored = byId.get(row.id);
      return {
        id: row.id,
        name: row.name.trim(),
        type: "webhook",
        url: row.url.trim(),
        enabled: Boolean(row.enabled),
        on_grab: Boolean(row.on_grab),
        on_import: Boolean(row.on_import),
        on_health_issue: Boolean(row.on_health_issue),
        header_name: row.header_name.trim(),
        header_value: row.header_value ? row.header_value : stored ? stored.header_value : "",
        last_event: stored ? stored.last_event : "",
        last_status: stored ? stored.last_status : "",
        last_status_code: stored ? stored.last_status_code : null,
        last_error: stored ? stored.last_error : "",
        last_delivered_at: stored ? stored.last_delivered_at : "",
      };
    });
}

window.AudiarrConnect = {
  populate,
  collectForSave,
  addRow() {
    state.push(newRow());
    paint();
  },
};

document.addEventListener("DOMContentLoaded", () => {
  const addBtn = document.getElementById("connect-add-btn");
  if (!addBtn) return;
  addBtn.addEventListener("click", () => {
    window.AudiarrConnect.addRow();
    if (window.setDirty) window.setDirty(true);
  });
});

})();
