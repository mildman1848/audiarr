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

// Comma-separated list <-> array helper for the plain-text list fields
// (allowed formats, preferred quality tier IDs) in the Profiles editor.
function splitList(value) {
  return String(value || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

// ---------------------------------------------------------------- Quality
// Editable list of audiobook quality tiers (Quality page). Each row edits
// one QualityDefinition (see app/models/settings.py); state is kept as a
// plain array of objects and only re-rendered on add/remove so typing in a
// field never loses focus/cursor position.
let qualityDefinitionsState = null;

function newQualityDefinition() {
  return {
    id: "",
    name: "",
    container: "m4b",
    codec: "aac",
    lossless: false,
    min_bitrate_kbps: 32,
    preferred_bitrate_kbps: 64,
    max_bitrate_kbps: 128,
    chapters: "preferred",
  };
}

function paintQualityDefinitions() {
  const el = $("quality-definitions");
  if (!el || !qualityDefinitionsState) return;
  if (!qualityDefinitionsState.length) {
    el.innerHTML = `<p class="muted">${T.settings_quality_empty}</p>`;
    return;
  }
  el.innerHTML = qualityDefinitionsState
    .map((d, i) => {
      const chaptersOption = (value, label) =>
        `<option value="${value}" ${d.chapters === value ? "selected" : ""}>${label}</option>`;
      return `
    <div class="settings-subsection repeat-row" data-row="${i}">
      <div class="repeat-row-head">
        <h3>${escapeHtml(d.name) || T.settings_quality_untitled}</h3>
        <button type="button" class="btn btn-secondary" data-remove-quality="${i}">${T.settings_remove}</button>
      </div>
      <label>${T.settings_field_name}
        <input type="text" data-quality-field="name" data-row="${i}" value="${escapeHtml(d.name)}">
      </label>
      <label>${T.settings_quality_id_label}
        <input type="text" data-quality-field="id" data-row="${i}" value="${escapeHtml(d.id)}">
      </label>
      <p class="muted small">${T.settings_quality_id_hint}</p>
      <label>${T.settings_quality_container_label}
        <input type="text" data-quality-field="container" data-row="${i}" value="${escapeHtml(d.container)}">
      </label>
      <label>${T.settings_quality_codec_label}
        <input type="text" data-quality-field="codec" data-row="${i}" value="${escapeHtml(d.codec)}">
      </label>
      <label class="inline-check">
        <input type="checkbox" data-quality-field="lossless" data-row="${i}" ${d.lossless ? "checked" : ""}>
        ${T.settings_quality_lossless_label}
      </label>
      <label>${T.settings_quality_min_bitrate_label}
        <input type="number" min="0" data-quality-field="min_bitrate_kbps" data-row="${i}" value="${d.min_bitrate_kbps}">
      </label>
      <label>${T.settings_quality_preferred_bitrate_label}
        <input type="number" min="0" data-quality-field="preferred_bitrate_kbps" data-row="${i}" value="${d.preferred_bitrate_kbps}">
      </label>
      <label>${T.settings_quality_max_bitrate_label}
        <input type="number" min="0" data-quality-field="max_bitrate_kbps" data-row="${i}" value="${d.max_bitrate_kbps}">
      </label>
      <label>${T.settings_quality_chapters_label}
        <select data-quality-field="chapters" data-row="${i}">
          ${chaptersOption("required", T.settings_quality_chapters_required)}
          ${chaptersOption("preferred", T.settings_quality_chapters_preferred)}
          ${chaptersOption("not_required", T.settings_quality_chapters_not_required)}
        </select>
      </label>
    </div>`;
    })
    .join("");
}

function renderQualityDefinitionsEditor(definitions) {
  const el = $("quality-definitions");
  if (!el) return;
  qualityDefinitionsState = (definitions || []).map((d) => ({ ...d }));
  paintQualityDefinitions();
}

function bindQualityDefinitionsEvents() {
  const el = $("quality-definitions");
  if (!el || el.dataset.bound) return;
  el.dataset.bound = "1";

  const applyFieldChange = (target) => {
    const field = target.dataset.qualityField;
    const row = target.dataset.row;
    if (field == null || row == null || !qualityDefinitionsState) return;
    const item = qualityDefinitionsState[Number(row)];
    if (!item) return;
    if (target.type === "checkbox") {
      item[field] = target.checked;
    } else if (target.type === "number") {
      item[field] = Number(target.value) || 0;
    } else {
      item[field] = target.value;
    }
  };

  el.addEventListener("input", (e) => applyFieldChange(e.target));
  el.addEventListener("change", (e) => applyFieldChange(e.target));
  el.addEventListener("click", (e) => {
    const idx = e.target.dataset.removeQuality;
    if (idx == null || !qualityDefinitionsState) return;
    qualityDefinitionsState.splice(Number(idx), 1);
    paintQualityDefinitions();
    setDirty(true);
  });
}

// ---------------------------------------------------------------- Profiles
// Editable list of quality profiles (Profiles page). List-shaped fields
// (allowed formats, preferred quality tier IDs) are edited as plain
// comma-separated text and only split into arrays at save time.
let profilesState = null;
let profilesQualityDefs = [];

function paintProfiles() {
  const el = $("profiles-editor");
  if (!el || !profilesState) return;
  if (!profilesState.length) {
    el.innerHTML = `<p class="muted">${T.settings_profiles_empty}</p>`;
    return;
  }
  const qualityOptionsHtml = (selectedId) => {
    const opts = [
      `<option value="">${T.settings_profiles_cutoff_quality_none}</option>`,
      ...profilesQualityDefs.map(
        (d) =>
          `<option value="${escapeHtml(d.id)}" ${d.id === selectedId ? "selected" : ""}>${escapeHtml(d.name)} (${escapeHtml(d.id)})</option>`
      ),
    ];
    return opts.join("");
  };
  el.innerHTML = profilesState
    .map(
      (p, i) => `
    <div class="settings-subsection repeat-row" data-row="${i}">
      <div class="repeat-row-head">
        <h3>${escapeHtml(p.name) || T.settings_profiles_untitled}</h3>
        <button type="button" class="btn btn-secondary" data-remove-profile="${i}">${T.settings_remove}</button>
      </div>
      <label>${T.settings_field_name}
        <input type="text" data-profile-field="name" data-row="${i}" value="${escapeHtml(p.name)}">
      </label>
      <label>${T.settings_profiles_allowed_formats_label}
        <input type="text" data-profile-field="allowed_formats" data-row="${i}" value="${escapeHtml(p.allowed_formats)}">
      </label>
      <p class="muted small">${T.settings_profiles_allowed_formats_hint}</p>
      <label>${T.settings_profiles_cutoff_format_label}
        <input type="text" data-profile-field="cutoff_format" data-row="${i}" value="${escapeHtml(p.cutoff_format)}">
      </label>
      <label>${T.settings_profiles_quality_ids_label}
        <input type="text" data-profile-field="quality_ids" data-row="${i}" value="${escapeHtml(p.quality_ids)}">
      </label>
      <p class="muted small">${T.settings_profiles_quality_ids_hint}</p>
      <label>${T.settings_profiles_cutoff_quality_label}
        <select data-profile-field="cutoff_quality_id" data-row="${i}">
          ${qualityOptionsHtml(p.cutoff_quality_id)}
        </select>
      </label>
      <label class="inline-check">
        <input type="checkbox" data-profile-field="upgrade_allowed" data-row="${i}" ${p.upgrade_allowed ? "checked" : ""}>
        ${T.settings_profiles_upgrade_allowed_label}
      </label>
    </div>`
    )
    .join("");
}

function renderProfilesEditor(profiles, qualityDefs) {
  const el = $("profiles-editor");
  if (!el) return;
  profilesState = (profiles || []).map((p) => ({
    name: p.name || "",
    allowed_formats: (p.allowed_formats || []).join(", "),
    cutoff_format: p.cutoff_format || "",
    quality_ids: (p.quality_ids || []).join(", "),
    cutoff_quality_id: p.cutoff_quality_id || "",
    upgrade_allowed: p.upgrade_allowed !== false,
  }));
  profilesQualityDefs = qualityDefs || [];
  paintProfiles();
}

function bindProfilesEvents() {
  const el = $("profiles-editor");
  if (!el || el.dataset.bound) return;
  el.dataset.bound = "1";

  const applyFieldChange = (target) => {
    const field = target.dataset.profileField;
    const row = target.dataset.row;
    if (field == null || row == null || !profilesState) return;
    const item = profilesState[Number(row)];
    if (!item) return;
    item[field] = target.type === "checkbox" ? target.checked : target.value;
  };

  el.addEventListener("input", (e) => applyFieldChange(e.target));
  el.addEventListener("change", (e) => applyFieldChange(e.target));
  el.addEventListener("click", (e) => {
    const idx = e.target.dataset.removeProfile;
    if (idx == null || !profilesState) return;
    profilesState.splice(Number(idx), 1);
    paintProfiles();
    setDirty(true);
  });
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
  setValue("media-scan-interval", s.media_management.import_scan_interval_minutes ?? 0);
  setText("media-scan-last-run", s.media_management.last_scheduled_scan_at || "—");
  setChecked("media-sab-import-enabled", s.media_management.sab_auto_import_enabled);
  setValue("media-sab-import-category", s.media_management.sab_auto_import_category || "");
  setValue("media-sab-import-interval", s.media_management.sab_auto_import_interval_minutes ?? 5);
  setValue("metadata-refresh-interval", s.metadata.refresh_interval_minutes ?? 0);
  setValue("metadata-refresh-batch-size", s.metadata.refresh_batch_size ?? 10);
  setText("metadata-refresh-last-run", s.metadata.last_scheduled_refresh_at || "—");
  setText("metadata-refresh-updated", s.metadata.last_refresh_updated ?? 0);
  setText("metadata-refresh-failed", s.metadata.last_refresh_failed ?? 0);
  setText("metadata-refresh-remaining", s.metadata.last_refresh_remaining ?? 0);
  setValue("wanted-search-interval", s.wanted.search_interval_minutes ?? 0);
  setText("wanted-search-last-run", s.wanted.last_scheduled_search_at || "—");
  setText("wanted-search-grabbed", s.wanted.last_search_grabbed ?? 0);
  setText("wanted-search-no-release", s.wanted.last_search_no_release ?? 0);
  setText("wanted-search-skipped", s.wanted.last_search_skipped ?? 0);
  if ($("quality-definitions")) {
    renderQualityDefinitionsEditor(s.quality_definitions);
    bindQualityDefinitionsEvents();
  }
  if ($("profiles-editor")) {
    renderProfilesEditor(s.quality_profiles, s.quality_definitions);
    bindProfilesEvents();
  }
  if ($("connect-list") && window.AudiarrConnect) {
    window.AudiarrConnect.populate(s.connect);
  }
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

  // Maintenance / backups (issue #32): interval + retention are settings
  // fields; the backup list itself comes from the system API.
  if (s.backup) {
    setValue("backup-folder", s.backup.folder || "");
    setValue("backup-interval", s.backup.interval_hours ?? 24);
    setValue("backup-retention", s.backup.retention_copies ?? 7);
  }
}

// --- Backups (issue #32) -------------------------------------------------

function humanSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

async function loadBackupList() {
  if (!$("backup-list-wrap")) return;
  try {
    const resp = await fetch("/api/v1/system/backup");
    if (!resp.ok) return;
    const data = await resp.json();
    renderBackupList(data.backups || []);
  } catch {
    // Backup list is informational; a failed fetch just leaves it hidden.
  }
}

function renderBackupList(backups) {
  const wrap = $("backup-list-wrap");
  const list = $("backup-list");
  if (!wrap || !list) return;
  wrap.hidden = backups.length === 0;
  list.innerHTML = "";
  for (const b of backups) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = `${b.name} — ${humanSize(b.size_bytes)}`;
    li.appendChild(name);
    list.appendChild(li);
  }
}

async function backupNow() {
  const btn = $("backup-now-btn");
  const msg = $("backup-result-msg");
  if (!btn) return;
  btn.disabled = true;
  const restoreLabel = btn.textContent;
  btn.textContent = T.settings_backup_running;
  if (msg) {
    msg.hidden = false;
    msg.textContent = T.settings_backup_running;
  }
  try {
    const resp = await fetch("/api/v1/system/backup", { method: "POST" });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    if (msg) {
      msg.textContent = `${T.settings_backup_done} (${humanSize(data.size_bytes)})`;
    }
    await loadBackupList();
  } catch (err) {
    if (msg) msg.textContent = `${T.settings_backup_failed} (${err.message})`;
  } finally {
    btn.disabled = false;
    btn.textContent = restoreLabel;
  }
}

function bindBackupEvents() {
  const btn = $("backup-now-btn");
  if (btn && !btn.dataset.bound) {
    btn.dataset.bound = "1";
    btn.addEventListener("click", backupNow);
  }
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
    if ($("backup-interval")) {
      const interval = Number(getValue("backup-interval"));
      doc.backup.interval_hours =
        Number.isFinite(interval) && interval >= 0 ? Math.trunc(interval) : 24;
    }
    if ($("backup-retention")) {
      const retention = Number(getValue("backup-retention"));
      doc.backup.retention_copies =
        Number.isFinite(retention) && retention >= 0 ? Math.trunc(retention) : 7;
    }
    if ($("media-rename-files")) {
      doc.media_management.rename_files = getChecked("media-rename-files");
    }
    if ($("media-file-name-pattern")) {
      doc.media_management.file_name_pattern = getValue("media-file-name-pattern").trim();
    }
    if ($("media-delete-empty-folders")) {
      doc.media_management.delete_empty_folders = getChecked("media-delete-empty-folders");
    }
    if ($("media-scan-interval")) {
      const interval = Number(getValue("media-scan-interval"));
      doc.media_management.import_scan_interval_minutes =
        Number.isFinite(interval) && interval >= 0 ? Math.trunc(interval) : 0;
    }
    if ($("media-sab-import-enabled")) {
      doc.media_management.sab_auto_import_enabled = getChecked("media-sab-import-enabled");
    }
    if ($("media-sab-import-category")) {
      doc.media_management.sab_auto_import_category = getValue("media-sab-import-category").trim();
    }
    if ($("media-sab-import-interval")) {
      const sabInterval = Number(getValue("media-sab-import-interval"));
      doc.media_management.sab_auto_import_interval_minutes =
        Number.isFinite(sabInterval) && sabInterval >= 1 ? Math.trunc(sabInterval) : 5;
    }
    if ($("wanted-search-interval")) {
      const searchInterval = Number(getValue("wanted-search-interval"));
      doc.wanted.search_interval_minutes =
        Number.isFinite(searchInterval) && searchInterval >= 0 ? Math.trunc(searchInterval) : 0;
    }
    if ($("metadata-locale")) doc.metadata.audible_locale = getValue("metadata-locale");
    if ($("metadata-refresh-interval")) {
      const refreshInterval = Number(getValue("metadata-refresh-interval"));
      doc.metadata.refresh_interval_minutes =
        Number.isFinite(refreshInterval) && refreshInterval >= 0 ? Math.trunc(refreshInterval) : 0;
    }
    if ($("metadata-refresh-batch-size")) {
      const batchSize = Number(getValue("metadata-refresh-batch-size"));
      doc.metadata.refresh_batch_size =
        Number.isFinite(batchSize) && batchSize >= 1 ? Math.trunc(batchSize) : 10;
    }
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

    if ($("quality-definitions") && qualityDefinitionsState) {
      doc.quality_definitions = qualityDefinitionsState
        .map((d) => ({
          id: (d.id || "").trim(),
          name: (d.name || "").trim(),
          container: (d.container || "").trim() || "m4b",
          codec: (d.codec || "").trim() || "aac",
          lossless: Boolean(d.lossless),
          min_bitrate_kbps: Number(d.min_bitrate_kbps) || 0,
          preferred_bitrate_kbps: Number(d.preferred_bitrate_kbps) || 0,
          max_bitrate_kbps: Number(d.max_bitrate_kbps) || 0,
          chapters: d.chapters || "preferred",
        }))
        .filter((d) => d.id && d.name);
    }

    if ($("profiles-editor") && profilesState) {
      doc.quality_profiles = profilesState
        .map((p) => ({
          name: (p.name || "").trim(),
          allowed_formats: splitList(p.allowed_formats),
          cutoff_format: (p.cutoff_format || "").trim() || "m4b",
          quality_ids: splitList(p.quality_ids),
          cutoff_quality_id: (p.cutoff_quality_id || "").trim(),
          upgrade_allowed: Boolean(p.upgrade_allowed),
        }))
        .filter((p) => p.name);
    }

    if ($("prowlarr-url") || $("prowlarr-enabled")) {
      const prowlarr = mergeProwlarr(doc);
      prowlarr.enabled = getChecked("prowlarr-enabled");
      prowlarr.name = getValue("prowlarr-name").trim() || "Prowlarr";
      prowlarr.url = getValue("prowlarr-url").trim();
      const prowlarrKey = getValue("prowlarr-api-key");
      if (prowlarrKey) prowlarr.api_key = prowlarrKey;
    }

    if ($("connect-list") && window.AudiarrConnect) {
      doc.connect = window.AudiarrConnect.collectForSave(doc.connect);
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
  // Backups live on the general page only; both helpers no-op elsewhere.
  bindBackupEvents();
  loadBackupList();
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

  const qualityAddBtn = $("quality-add-btn");
  if (qualityAddBtn) {
    qualityAddBtn.addEventListener("click", () => {
      if (!qualityDefinitionsState) qualityDefinitionsState = [];
      qualityDefinitionsState.push(newQualityDefinition());
      paintQualityDefinitions();
      setDirty(true);
    });
  }

  const profilesAddBtn = $("profiles-add-btn");
  if (profilesAddBtn) {
    profilesAddBtn.addEventListener("click", () => {
      if (!profilesState) profilesState = [];
      profilesState.push({
        name: "",
        allowed_formats: "m4b, mp3, flac",
        cutoff_format: "m4b",
        quality_ids: "",
        cutoff_quality_id: "",
        upgrade_allowed: true,
      });
      paintProfiles();
      setDirty(true);
    });
  }

  const copyBtn = $("security-api-key-copy-btn");
  if (copyBtn) copyBtn.addEventListener("click", copyApiKey);

  const regenBtn = $("security-api-key-regen-btn");
  if (regenBtn) regenBtn.addEventListener("click", regenerateApiKey);
});
