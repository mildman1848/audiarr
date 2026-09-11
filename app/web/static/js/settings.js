// Settings pages: Sonarr-style dedicated full pages backed by the single
// settings document. Every /settings/<section> page loads this same
// script; each page only has the DOM ids for its own fields, so every
// lookup below is guarded and simply no-ops when an id is absent on the
// current page. Save flow is GET the full document, merge the edited
// fields, then PUT the whole document back (the settings API replaces,
// it does not patch).
//
// Secrets (webhook / SABnzbd / Prowlarr API keys, the auth password) are
// never rendered back into the page: a stored value only shows as a masked
// placeholder (or, for the password, stays blank), and an empty submit
// keeps the current value. The auth API key is the one exception: the
// settings API only excludes password_hash, so GET returns it in plain
// text and it is shown read-only for copying.

const T = window.AUDIARR_I18N || {};
const MASK = "•••••";
const ADVANCED_STORAGE_KEY = "audiarr:settings:advancedVisible";

function $(id) {
  return document.getElementById(id);
}

function setValue(id, value) {
  const el = $(id);
  if (el) el.value = value;
}

function setChecked(id, checked) {
  const el = $(id);
  if (el) el.checked = Boolean(checked);
}

function setText(id, text) {
  const el = $(id);
  if (el) el.textContent = text;
}

function setPlaceholder(id, text) {
  const el = $(id);
  if (el) el.placeholder = text;
}

function getValue(id, fallback) {
  const el = $(id);
  return el ? el.value : fallback ?? "";
}

function getChecked(id) {
  const el = $(id);
  return el ? el.checked : false;
}

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

async function loginAfterAuthChange(username, password) {
  if (!username || !password) return;
  const resp = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!resp.ok) throw new Error(`login HTTP ${resp.status}`);
}

// Return the first SABnzbd download client, or a fresh default (not yet
// attached to the document).
function readSab(s) {
  return (
    (s.download_clients || []).find((c) => c.type === "sabnzbd") || {
      name: "SABnzbd",
      type: "sabnzbd",
      url: "",
      api_key: "",
      category: "audiobooks",
      enabled: false,
    }
  );
}

// Return the first Prowlarr indexer, or a fresh default.
function readProwlarr(s) {
  return (
    (s.indexers || []).find((i) => i.type === "prowlarr") || {
      name: "Prowlarr",
      type: "prowlarr",
      url: "",
      api_key: "",
      enabled: false,
    }
  );
}

// Find-or-create the SABnzbd entry inside the document being saved.
function mergeSab(doc) {
  doc.download_clients = doc.download_clients || [];
  let sab = doc.download_clients.find((c) => c.type === "sabnzbd");
  if (!sab) {
    sab = { name: "SABnzbd", type: "sabnzbd", category: "audiobooks", enabled: false };
    doc.download_clients.push(sab);
  }
  return sab;
}

function mergeProwlarr(doc) {
  doc.indexers = doc.indexers || [];
  let idx = doc.indexers.find((i) => i.type === "prowlarr");
  if (!idx) {
    idx = { name: "Prowlarr", type: "prowlarr", enabled: false };
    doc.indexers.push(idx);
  }
  return idx;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Read-only summary table of the modeled quality profiles (Profiles
// page). Editing is a later slice; this just shows what is stored.
function renderProfileSummary(profiles) {
  const el = $("profile-summary");
  if (!el) return;
  if (!profiles || !profiles.length) {
    el.innerHTML = `<p class="muted">${T.settings_profiles_empty}</p>`;
    return;
  }
  const rows = profiles
    .map(
      (p) => `<tr>
        <td>${escapeHtml(p.name)}</td>
        <td>${escapeHtml((p.allowed_formats || []).join(", "))}</td>
        <td>${escapeHtml(p.cutoff_format || "")}</td>
      </tr>`
    )
    .join("");
  el.innerHTML = `<table class="table summary-table">
    <thead><tr>
      <th>${T.settings_profiles_col_name}</th>
      <th>${T.settings_profiles_col_formats}</th>
      <th>${T.settings_profiles_col_cutoff}</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

// Populate whichever of these fields exist on the current settings page.
function populate(s) {
  setText("host-port", s.host.port ?? "—");
  setValue("security-method", s.auth.method || "none");
  setValue("security-username", s.auth.username || "");
  setValue("security-api-key", s.auth.api_key || "");
  setValue("ui-language", s.ui.language || "en");
  setValue("metadata-locale", s.metadata.audible_locale || "us");
  setText("provider-order", (s.metadata.provider_order || []).join(" → ") || "—");
  setText("root-folder-count", (s.root_folders || []).length);
  setChecked("media-rename-files", s.media_management.rename_files);
  setValue("media-file-name-pattern", s.media_management.file_name_pattern || "");
  setChecked("media-delete-empty-folders", s.media_management.delete_empty_folders);
  renderProfileSummary(s.quality_profiles);
  setText("connect-summary", (s.connect || []).length);
  setText("ui-theme-summary", s.ui.theme || "—");
  setText("ui-date-format-summary", s.ui.date_format || "—");
  setValue("conversion-backend", s.conversion.backend || "disabled");
  setChecked("conversion-delete-originals", s.conversion.delete_originals);
  setValue("conversion-job-timeout", s.conversion.job_timeout_hours ?? 6);
  setPlaceholder("conversion-webhook-key", s.conversion.webhook_api_key ? MASK : "");

  const sab = readSab(s);
  setChecked("sab-enabled", sab.enabled);
  setValue("sab-name", sab.name || "SABnzbd");
  setValue("sab-url", sab.url || "");
  setValue("sab-category", sab.category || "audiobooks");
  setPlaceholder("sab-api-key", sab.api_key ? MASK : "");

  const prowlarr = readProwlarr(s);
  setChecked("prowlarr-enabled", prowlarr.enabled);
  setValue("prowlarr-name", prowlarr.name || "Prowlarr");
  setValue("prowlarr-url", prowlarr.url || "");
  setPlaceholder("prowlarr-api-key", prowlarr.api_key ? MASK : "");
}

async function loadSettings() {
  try {
    populate(await getSettings());
  } catch (err) {
    setText("settings-msg", `${T.settings_load_error} (${err.message})`);
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const msg = $("settings-msg");
  if (msg) msg.textContent = T.settings_saving;
  try {
    const doc = await getSettings();
    // doc.auth.api_key is already present from the GET above; pass it
    // through unchanged unless regenerateApiKey() rewrote it in place.
    let password = "";
    if ($("security-method")) doc.auth.method = getValue("security-method");
    if ($("security-username")) doc.auth.username = getValue("security-username").trim();
    if ($("security-password")) {
      password = getValue("security-password");
      doc.auth.password = password; // empty means keep the stored password
    }
    if ($("ui-language")) doc.ui.language = getValue("ui-language");
    if ($("media-rename-files")) {
      doc.media_management.rename_files = getChecked("media-rename-files");
    }
    if ($("media-file-name-pattern")) {
      doc.media_management.file_name_pattern = getValue("media-file-name-pattern").trim();
    }
    if ($("media-delete-empty-folders")) {
      doc.media_management.delete_empty_folders = getChecked("media-delete-empty-folders");
    }
    if ($("metadata-locale")) doc.metadata.audible_locale = getValue("metadata-locale");
    if ($("conversion-backend")) doc.conversion.backend = getValue("conversion-backend");
    if ($("conversion-delete-originals")) {
      doc.conversion.delete_originals = getChecked("conversion-delete-originals");
    }
    if ($("conversion-job-timeout")) {
      const timeout = Number(getValue("conversion-job-timeout"));
      if (Number.isFinite(timeout) && timeout > 0) {
        doc.conversion.job_timeout_hours = timeout;
      }
    }
    if ($("conversion-webhook-key")) {
      const key = getValue("conversion-webhook-key");
      if (key) doc.conversion.webhook_api_key = key; // empty means keep stored value
    }

    if ($("sab-url") || $("sab-enabled")) {
      const sab = mergeSab(doc);
      sab.enabled = getChecked("sab-enabled");
      sab.name = getValue("sab-name").trim() || "SABnzbd";
      sab.url = getValue("sab-url").trim();
      sab.category = getValue("sab-category").trim() || "audiobooks";
      const sabKey = getValue("sab-api-key");
      if (sabKey) sab.api_key = sabKey; // empty means keep stored key
    }

    if ($("prowlarr-url") || $("prowlarr-enabled")) {
      const prowlarr = mergeProwlarr(doc);
      prowlarr.enabled = getChecked("prowlarr-enabled");
      prowlarr.name = getValue("prowlarr-name").trim() || "Prowlarr";
      prowlarr.url = getValue("prowlarr-url").trim();
      const prowlarrKey = getValue("prowlarr-api-key");
      if (prowlarrKey) prowlarr.api_key = prowlarrKey;
    }

    await putSettings(doc);
    if (doc.auth.method === "forms" && password) {
      await loginAfterAuthChange(doc.auth.username, password);
    }
    for (const id of [
      "conversion-webhook-key",
      "sab-api-key",
      "prowlarr-api-key",
      "security-password",
    ]) {
      const el = $(id);
      if (el) el.value = "";
    }
    populate(await getSettings());
    setDirty(false);
    if (msg) msg.textContent = "";
    if (window.AudiarrToast) window.AudiarrToast.success(T.settings_save_success);
  } catch (err) {
    const text = `${T.settings_save_error} (${err.message})`;
    if (msg) msg.textContent = text;
    if (window.AudiarrToast) window.AudiarrToast.error(text);
  }
}

// Fire a connection test against one of the /api/v1/connections/.../test
// endpoints using the current (unsaved) form values.
async function testConnection(endpoint, urlId, keyId, msgId) {
  const msg = $(msgId);
  if (msg) msg.textContent = T.settings_testing;
  try {
    const body = { url: getValue(urlId).trim() };
    const key = getValue(keyId);
    if (key) body.api_key = key;
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      if (msg) msg.textContent = `${T.settings_test_error} (${data.detail || `HTTP ${resp.status}`})`;
      return;
    }
    if (msg) msg.textContent = `${data.ok ? "✓" : "✗"} ${data.message || ""}`.trim();
  } catch (err) {
    if (msg) msg.textContent = `${T.settings_test_error} (${err.message})`;
  }
}

// Copy the current API key to the clipboard.
async function copyApiKey() {
  const key = getValue("security-api-key");
  try {
    await navigator.clipboard.writeText(key);
    if (window.AudiarrToast) window.AudiarrToast.success(T.settings_security_api_key_copy_success);
  } catch (err) {
    if (window.AudiarrToast)
      window.AudiarrToast.error(`${T.settings_save_error} (${err.message})`);
  }
}

// Generate a fresh API key (invalidates sessions + API clients), persist it
// immediately via the normal GET/merge/PUT save path. There is no dedicated
// regenerate endpoint; the settings PUT path persists api_key fine.
async function regenerateApiKey() {
  if (!window.confirm(T.settings_security_api_key_regenerate_confirm)) return;
  try {
    const doc = await getSettings();
    const newKey = crypto.randomUUID().replace(/-/g, "");
    doc.auth.api_key = newKey;
    await putSettings(doc);
    setValue("security-api-key", newKey);
    if (window.AudiarrToast)
      window.AudiarrToast.success(T.settings_security_api_key_regenerated);
  } catch (err) {
    const text = `${T.settings_save_error} (${err.message})`;
    setText("settings-msg", text);
    if (window.AudiarrToast) window.AudiarrToast.error(text);
  }
}

// Arr-style save-state bar: "No changes" until a field is edited, then
// "Unsaved changes" until the next successful save. Populating fields from
// a GET does not fire input/change events, so the initial state stays
// clean until an actual user edit happens.
function setDirty(isDirty) {
  const bar = $("settings-save-bar");
  const status = $("settings-save-status");
  if (!bar || !status) return;
  bar.classList.toggle("is-dirty", isDirty);
  status.textContent = isDirty ? T.settings_unsaved_changes : T.settings_no_changes;
}

function setupDirtyTracking() {
  const form = $("settings-form");
  if (!form) return;
  const markDirty = () => setDirty(true);
  form.addEventListener("input", markDirty);
  form.addEventListener("change", markDirty);
}

// "Show advanced" toggle: state is localStorage-only (no server round
// trip) and simply reveals/hides .settings-advanced-row placeholder rows
// via a class on <body>, applied on load and on every settings page.
function setupAdvancedToggle() {
  const toggle = $("settings-advanced-toggle");
  if (!toggle) return;

  function apply(visible) {
    document.body.classList.toggle("settings-show-advanced", visible);
    toggle.classList.toggle("active", visible);
    toggle.setAttribute("aria-pressed", String(visible));
  }

  apply(window.localStorage.getItem(ADVANCED_STORAGE_KEY) === "1");
  toggle.addEventListener("click", () => {
    const next = !document.body.classList.contains("settings-show-advanced");
    window.localStorage.setItem(ADVANCED_STORAGE_KEY, next ? "1" : "0");
    apply(next);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  setupDirtyTracking();
  setupAdvancedToggle();

  const form = $("settings-form");
  if (form) form.addEventListener("submit", saveSettings);

  const sabTestBtn = $("sab-test-btn");
  if (sabTestBtn) {
    sabTestBtn.addEventListener("click", () =>
      testConnection("/api/v1/connections/sabnzbd/test", "sab-url", "sab-api-key", "sab-msg")
    );
  }

  const prowlarrTestBtn = $("prowlarr-test-btn");
  if (prowlarrTestBtn) {
    prowlarrTestBtn.addEventListener("click", () =>
      testConnection(
        "/api/v1/connections/prowlarr/test",
        "prowlarr-url",
        "prowlarr-api-key",
        "prowlarr-msg"
      )
    );
  }

  const copyBtn = $("security-api-key-copy-btn");
  if (copyBtn) copyBtn.addEventListener("click", copyApiKey);

  const regenBtn = $("security-api-key-regen-btn");
  if (regenBtn) regenBtn.addEventListener("click", regenerateApiKey);
});
