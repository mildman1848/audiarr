// Connections page: Audiobookshelf and m4b-convertarr integration settings.
// Vanilla JS. Save flow is GET the full settings document, merge the edited
// fields, then PUT the whole document back (the settings API replaces, it
// does not patch).
//
// API keys are never rendered back into the page: a stored key only shows as
// a masked placeholder, and an empty submit means "keep the current key".

const T = window.AUDIARR_I18N || {};
const MASK = "•••••";

async function getSettings() {
  const resp = await fetch("/api/v1/settings");
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

async function putSettings(doc) {
  const resp = await fetch("/api/v1/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(doc),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

function populate(settings) {
  const abs = settings.connections.audiobookshelf;
  document.getElementById("abs-url").value = abs.url || "";
  document.getElementById("abs-library-id").value = abs.library_id || "";
  document.getElementById("abs-enabled").checked = Boolean(abs.enabled);
  // Never place the key in .value; only hint that one is stored.
  document.getElementById("abs-api-key").placeholder = abs.api_key ? MASK : "";

  const m4b = settings.connections.m4b_convertarr;
  document.getElementById("m4b-url").value = m4b.url || "";
  document.getElementById("m4b-enabled").checked = Boolean(m4b.enabled);
  document.getElementById("m4b-api-key").placeholder = m4b.api_key ? MASK : "";
}

async function loadConnections() {
  try {
    populate(await getSettings());
  } catch (err) {
    for (const id of ["abs-msg", "m4b-msg"]) {
      document.getElementById(id).textContent =
        `${T.connections_load_error} (${err.message})`;
    }
  }
}

async function saveAudiobookshelf(event) {
  event.preventDefault();
  const msg = document.getElementById("abs-msg");
  msg.textContent = T.connections_saving;
  try {
    const doc = await getSettings();
    const abs = doc.connections.audiobookshelf;
    abs.url = document.getElementById("abs-url").value.trim();
    abs.library_id = document.getElementById("abs-library-id").value.trim();
    abs.enabled = document.getElementById("abs-enabled").checked;
    const key = document.getElementById("abs-api-key").value;
    if (key) abs.api_key = key; // empty means keep the stored key
    await putSettings(doc);
    document.getElementById("abs-api-key").value = "";
    populate(await getSettings());
    msg.textContent = "";
    if (window.AudiarrToast) window.AudiarrToast.success(T.connections_save_success);
  } catch (err) {
    const text = `${T.connections_save_error} (${err.message})`;
    msg.textContent = text;
    if (window.AudiarrToast) window.AudiarrToast.error(text);
  }
}

async function saveM4bConvertarr(event) {
  event.preventDefault();
  const msg = document.getElementById("m4b-msg");
  msg.textContent = T.connections_saving;
  try {
    const doc = await getSettings();
    const m4b = doc.connections.m4b_convertarr;
    m4b.url = document.getElementById("m4b-url").value.trim();
    m4b.enabled = document.getElementById("m4b-enabled").checked;
    const key = document.getElementById("m4b-api-key").value;
    if (key) m4b.api_key = key;
    await putSettings(doc);
    document.getElementById("m4b-api-key").value = "";
    populate(await getSettings());
    msg.textContent = "";
    if (window.AudiarrToast) window.AudiarrToast.success(T.connections_save_success);
  } catch (err) {
    const text = `${T.connections_save_error} (${err.message})`;
    msg.textContent = text;
    if (window.AudiarrToast) window.AudiarrToast.error(text);
  }
}

async function testConnection(endpoint, urlId, keyId, msgId) {
  const msg = document.getElementById(msgId);
  msg.textContent = T.connections_testing;
  try {
    const body = { url: document.getElementById(urlId).value.trim() };
    const key = document.getElementById(keyId).value;
    if (key) body.api_key = key;
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      msg.textContent = `${T.connections_test_error} (${data.detail || `HTTP ${resp.status}`})`;
      return;
    }
    msg.textContent = `${data.ok ? "✓" : "✗"} ${data.message || ""}`.trim();
  } catch (err) {
    msg.textContent = `${T.connections_test_error} (${err.message})`;
  }
}

async function scanAudiobookshelf() {
  const msg = document.getElementById("abs-msg");
  msg.textContent = T.connections_scanning;
  try {
    // The scan endpoint takes no body; it uses the persisted settings.
    const resp = await fetch("/api/v1/connections/audiobookshelf/scan", {
      method: "POST",
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      msg.textContent = `${T.connections_scan_error} (${data.detail || `HTTP ${resp.status}`})`;
      return;
    }
    msg.textContent = `${data.ok ? "✓" : "✗"} ${data.message || ""}`.trim();
  } catch (err) {
    msg.textContent = `${T.connections_scan_error} (${err.message})`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadConnections();
  document.getElementById("abs-form").addEventListener("submit", saveAudiobookshelf);
  document.getElementById("m4b-form").addEventListener("submit", saveM4bConvertarr);
  document.getElementById("abs-test-btn").addEventListener("click", () =>
    testConnection(
      "/api/v1/connections/audiobookshelf/test",
      "abs-url",
      "abs-api-key",
      "abs-msg"
    )
  );
  document.getElementById("abs-scan-btn").addEventListener("click", scanAudiobookshelf);
  document.getElementById("m4b-test-btn").addEventListener("click", () =>
    testConnection(
      "/api/v1/connections/m4b-convertarr/test",
      "m4b-url",
      "m4b-api-key",
      "m4b-msg"
    )
  );
});
