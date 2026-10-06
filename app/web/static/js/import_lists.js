// Settings > Import Lists (issue #72): generic Starr-style status table over
// GET /api/v1/import-lists, with a per-row Sync action against
// POST /api/v1/import-lists/{id}/sync. Read-only page -- enabling a source
// or editing its URL/token still happens on the Connections page; this is
// purely a status view, so it does not load settings.js.
//
// Issue #79 adds reading-list imports: saved Goodreads shelf-RSS sources
// (add / enable-disable / delete), a one-page feed preview with pagination,
// a Goodreads/StoryGraph CSV upload preview, and an explicit import of only
// the candidates the user ticked. Nothing is imported implicitly; the saved
// feed URL is never shown again (the backend keeps only user id + shelf).

const T = window.AUDIARR_I18N || {};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

// Safe API error text: only fixed {code, message} details are shown, mapped
// through i18n first. Anything else (validation echoes, raw bodies) is
// replaced by a generic message so inputs such as feed URLs never render.
function apiErrorText(data, status) {
  const detail = data && data.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail) && typeof detail.code === "string") {
    return T[`reading_list_err_${detail.code}`] || (typeof detail.message === "string" ? detail.message : "") || `HTTP ${status}`;
  }
  return T.reading_list_err_generic || `HTTP ${status}`;
}

function statusBadge(status) {
  let cls = "badge";
  if (status === "ok") cls = "badge badge-success";
  else if (status === "error") cls = "badge badge-error";
  const label = T[`import_lists_status_${status}`] || status || "—";
  return `<span class="${cls}">${esc(label)}</span>`;
}

function lastResultLabel(source) {
  if (!source.last_sync_at) return "—";
  return (T.import_lists_last_result || "{created} added, {skipped} skipped")
    .replace("{created}", source.last_created)
    .replace("{skipped}", source.last_skipped);
}

async function syncSource(sourceId, button) {
  button.disabled = true;
  const originalText = button.textContent;
  button.textContent = T.import_lists_syncing || "Syncing…";
  try {
    const resp = await fetch(`/api/v1/import-lists/${encodeURIComponent(sourceId)}/sync`, { method: "POST" });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const text = `${T.import_lists_sync_error || "Sync failed"} (${apiErrorText(data, resp.status)})`;
      if (window.AudiarrToast) window.AudiarrToast.error(text);
      return;
    }
    const text = (T.import_lists_last_result || "{created} added, {skipped} skipped")
      .replace("{created}", data.created)
      .replace("{skipped}", data.skipped_existing + data.skipped_no_id);
    if (window.AudiarrToast) window.AudiarrToast.success(text);
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.import_lists_sync_error || "Sync failed"} (${err.message})`);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
    loadImportLists();
  }
}

const READING_LIST_TYPE = "goodreads_rss";

// Reading-list rows import from a previewed selection, never a bulk Sync.
function rowActions(source) {
  if (source.type !== READING_LIST_TYPE) {
    return `<button type="button" class="btn btn-secondary" data-sync-source="${esc(source.id)}">${esc(T.import_lists_sync)}</button>`;
  }
  const id = esc(source.id);
  const toggle = source.enabled ? T.reading_list_disable : T.reading_list_enable;
  return `<button type="button" class="btn btn-secondary" data-preview-source="${id}"${source.enabled ? "" : " disabled"}>${esc(T.reading_list_preview_button)}</button>
        <button type="button" class="btn btn-secondary" data-toggle-source="${id}" data-enabled="${source.enabled ? "1" : "0"}">${esc(toggle)}</button>
        <button type="button" class="btn btn-danger" data-delete-source="${id}">${esc(T.reading_list_delete)}</button>`;
}

async function loadImportLists() {
  const tbody = document.getElementById("import-lists-tbody");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="6" class="muted table-loading-row">${esc(T.import_lists_loading)}</td></tr>`;
  try {
    const resp = await fetch("/api/v1/import-lists");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const sources = await resp.json();
    if (!sources.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="muted">${esc(T.import_lists_empty || "No import-list sources configured")}</td></tr>`;
      return;
    }
    tbody.innerHTML = sources
      .map(
        (source) => `
      <tr data-source-id="${esc(source.id)}">
        <td>${esc(source.name)}</td>
        <td>${source.enabled ? esc(T.settings_enabled_label) : esc(T.status_not_configured)}</td>
        <td>${statusBadge(source.status)}</td>
        <td>${esc(source.last_sync_at) || "—"}</td>
        <td>${esc(lastResultLabel(source))}${source.last_error ? ` <span class="muted small">(${esc(source.last_error)})</span>` : ""}</td>
        <td>${rowActions(source)}</td>
      </tr>`
      )
      .join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="muted">${esc(T.import_lists_load_error)} (${esc(err.message)})</td></tr>`;
  }
}

// -- reading lists (issue #79) ---------------------------------------------

const rl = {
  // {kind: "source", id, name} | {kind: "csv", file, format}
  origin: null,
  page: 1,
  offset: 0,
  preview: null,
  selected: new Map(), // entry_key -> {provider_name, provider_uid, locale}
};
const MAX_SELECTIONS = 50;

function rlEl(id) {
  return document.getElementById(id);
}

function setMsg(id, text) {
  const el = rlEl(id);
  if (el) el.textContent = text || "";
}

function toast(kind, text) {
  if (window.AudiarrToast && window.AudiarrToast[kind]) window.AudiarrToast[kind](text);
}

async function readJson(resp) {
  return resp.json().catch(() => ({}));
}

async function addSource(event) {
  event.preventDefault();
  const urlInput = rlEl("rl-feed-url");
  const body = {
    feed_url: urlInput.value,
    name: rlEl("rl-name").value,
    enabled: rlEl("rl-enabled").checked,
  };
  setMsg("rl-add-msg", "");
  try {
    const resp = await fetch("/api/v1/import-lists/goodreads/sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await readJson(resp);
    if (!resp.ok) {
      setMsg("rl-add-msg", apiErrorText(data, resp.status));
      return;
    }
    // The raw feed URL is dropped as soon as the backend accepted it.
    urlInput.value = "";
    rlEl("rl-name").value = "";
    setMsg("rl-add-msg", T.reading_list_added);
    loadImportLists();
  } catch (err) {
    setMsg("rl-add-msg", T.reading_list_err_generic);
  }
}

async function toggleSource(sourceId, enabled) {
  try {
    const resp = await fetch(`/api/v1/import-lists/goodreads/sources/${encodeURIComponent(sourceId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if (!resp.ok) toast("error", apiErrorText(await readJson(resp), resp.status));
  } catch (err) {
    toast("error", T.reading_list_err_generic);
  }
  if (rl.origin && rl.origin.kind === "source" && rl.origin.id === sourceId && !enabled) closePreview();
  loadImportLists();
}

async function deleteSource(sourceId) {
  if (!window.confirm(T.reading_list_delete_confirm)) return;
  try {
    const resp = await fetch(`/api/v1/import-lists/goodreads/sources/${encodeURIComponent(sourceId)}`, {
      method: "DELETE",
    });
    if (!resp.ok) toast("error", apiErrorText(await readJson(resp), resp.status));
  } catch (err) {
    toast("error", T.reading_list_err_generic);
  }
  if (rl.origin && rl.origin.kind === "source" && rl.origin.id === sourceId) closePreview();
  loadImportLists();
}

function closePreview() {
  rl.origin = null;
  rl.preview = null;
  rl.selected.clear();
  rlEl("reading-list-preview").hidden = true;
}

// Fetch one preview window. Read-only on the server: nothing is created.
async function runPreview() {
  const origin = rl.origin;
  if (!origin) return;
  const locale = rlEl("rl-locale").value;
  const shelf = rlEl("rl-shelf").value.trim();
  setMsg("rl-preview-msg", T.reading_list_previewing);
  rlEl("rl-import").disabled = true;
  let resp;
  try {
    if (origin.kind === "source") {
      resp = await fetch(`/api/v1/import-lists/goodreads/sources/${encodeURIComponent(origin.id)}/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ page: rl.page, offset: rl.offset, shelf, locale }),
      });
    } else {
      const form = new FormData();
      form.append("file", origin.file);
      form.append("format", origin.format);
      form.append("shelf", shelf);
      form.append("locale", locale);
      form.append("offset", String(rl.offset));
      resp = await fetch("/api/v1/import-lists/csv/preview", { method: "POST", body: form });
    }
    const data = await readJson(resp);
    if (!resp.ok) {
      setMsg("rl-preview-msg", apiErrorText(data, resp.status));
      return;
    }
    rl.preview = data;
    rl.selected.clear();
    setMsg("rl-preview-msg", "");
    renderPreview();
  } catch (err) {
    setMsg("rl-preview-msg", T.reading_list_err_generic);
  }
}

function openPreview(origin, label) {
  rl.origin = origin;
  rl.page = 1;
  rl.offset = 0;
  rlEl("rl-preview-label").textContent = label;
  rlEl("rl-import-results").innerHTML = "";
  rlEl("rl-preview-tbody").innerHTML = "";
  rlEl("reading-list-preview").hidden = false;
  runPreview();
}

function entryStatusBadge(status) {
  const cls = status === "matched" ? "badge badge-success" : status === "lookup_failed" ? "badge badge-error" : "badge";
  return `<span class="${cls}">${esc(T[`reading_list_entry_${status}`] || status)}</span>`;
}

function candidateLabel(cand) {
  const reasons = (cand.match_reasons || []).map((r) => T[`reading_list_reason_${r}`] || r).join(", ");
  const people = (cand.authors || []).join(", ");
  const series = cand.series ? ` · ${cand.series}${cand.series_position ? ` #${cand.series_position}` : ""}` : "";
  const existing = cand.existing_book_id ? ` <span class="badge badge-success">${esc(T.reading_list_in_library)}</span>` : "";
  return `${esc(cand.title)}${cand.subtitle ? ` — ${esc(cand.subtitle)}` : ""}
    <span class="muted small">${esc(people)}${esc(series)} · ${esc(cand.provider_name)}/${esc(cand.locale)}
    · ${esc(T.reading_list_confidence)} ${Math.round((cand.score || 0) * 100)}%${reasons ? ` · ${esc(reasons)}` : ""}</span>${existing}`;
}

function renderPreview() {
  const data = rl.preview;
  const tbody = rlEl("rl-preview-tbody");
  if (!data.entries.length) {
    tbody.innerHTML = `<tr><td colspan="3" class="muted">${esc(T.reading_list_no_entries)}</td></tr>`;
  } else {
    tbody.innerHTML = data.entries
      .map((item, idx) => {
        const entry = item.entry;
        const name = `rl-pick-${idx}`;
        // "Skip" is the default: no candidate is ever pre-selected.
        const options = [
          `<label class="rl-candidate"><input type="radio" name="${name}" data-entry-key="${esc(entry.entry_key)}" value="" checked>${esc(T.reading_list_skip)}</label>`,
          ...item.candidates.map((cand, ci) => {
            const disabled = cand.existing_book_id ? " disabled" : "";
            return `<label class="rl-candidate"><input type="radio" name="${name}" data-entry-key="${esc(entry.entry_key)}" value="${ci}"${disabled}>${candidateLabel(cand)}</label>`;
          }),
        ].join("");
        return `<tr data-entry-index="${idx}">
          <td>${esc(entry.title)}<div class="muted small">${esc((entry.authors || []).join(", "))}</div></td>
          <td>${entryStatusBadge(item.status)}${item.existing_book_id ? ` <span class="badge badge-success">${esc(T.reading_list_in_library)}</span>` : ""}</td>
          <td>${options}</td>
        </tr>`;
      })
      .join("");
  }
  const notes = (data.notes || []).map((n) => T[`reading_list_note_${n}`]).filter(Boolean);
  if (data.skipped_rows) notes.push((T.reading_list_skipped_rows || "{n}").replace("{n}", data.skipped_rows));
  if (data.duplicate_rows) notes.push((T.reading_list_duplicate_rows || "{n}").replace("{n}", data.duplicate_rows));
  rlEl("rl-preview-notes").innerHTML = notes.map((n) => `<li>${esc(n)}</li>`).join("");
  rlEl("rl-next-window").hidden = data.next_offset == null;
  rlEl("rl-next-page").hidden = data.next_page == null || rl.origin.kind !== "source";
  updateSelectedCount();
}

function updateSelectedCount() {
  const n = rl.selected.size;
  rlEl("rl-selected-count").textContent = (T.reading_list_selected_count || "{n}").replace("{n}", n);
  rlEl("rl-import").disabled = n === 0;
}

function onPick(input) {
  const key = input.dataset.entryKey;
  const item = rl.preview.entries.find((e) => e.entry.entry_key === key);
  if (input.value === "" || !item) {
    rl.selected.delete(key);
  } else {
    const cand = item.candidates[Number(input.value)];
    if (rl.selected.size >= MAX_SELECTIONS && !rl.selected.has(key)) {
      toast("error", (T.reading_list_err_too_many_selections));
      const skip = input.closest("td").querySelector('input[value=""]');
      if (skip) skip.checked = true;
      return;
    }
    rl.selected.set(key, {
      entry_key: key,
      provider_name: cand.provider_name,
      provider_uid: cand.provider_uid,
      locale: cand.locale,
    });
  }
  updateSelectedCount();
}

// Explicit import: only the picked candidates are submitted.
async function importSelected() {
  if (!rl.preview || !rl.origin || !rl.selected.size) return;
  const selections = [...rl.selected.values()];
  const button = rlEl("rl-import");
  button.disabled = true;
  let url;
  const body = { selections };
  if (rl.origin.kind === "source") {
    url = `/api/v1/import-lists/goodreads/sources/${encodeURIComponent(rl.origin.id)}/import`;
  } else {
    url = "/api/v1/import-lists/csv/import";
    body.source_type = rl.preview.source_type;
  }
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await readJson(resp);
    if (!resp.ok) {
      setMsg("rl-preview-msg", apiErrorText(data, resp.status));
      return;
    }
    renderImportResult(data);
    toast(data.errors ? "error" : "success", (T.reading_list_import_summary || "")
      .replace("{created}", data.created)
      .replace("{skipped}", data.skipped_existing + data.skipped)
      .replace("{errors}", data.errors));
    loadImportLists();
  } catch (err) {
    setMsg("rl-preview-msg", T.reading_list_err_generic);
  } finally {
    updateSelectedCount();
  }
}

function renderImportResult(data) {
  const titles = new Map(rl.preview.entries.map((e) => [e.entry.entry_key, e.entry.title]));
  rlEl("rl-import-results").innerHTML = data.items
    .map((it) => {
      const label = T[`reading_list_item_${it.status}`] || it.status;
      const reason = T[`reading_list_reason_item_${it.reason}`] || it.message || "";
      return `<li>${esc(titles.get(it.entry_key) || "")}: ${esc(label)}${reason ? ` (${esc(reason)})` : ""}</li>`;
    })
    .join("");
  // Imported / existing rows can no longer be picked again.
  for (const it of data.items) {
    if (it.status !== "created" && it.status !== "skipped_existing") continue;
    rl.selected.delete(it.entry_key);
    document.querySelectorAll("#rl-preview-tbody input[data-entry-key]").forEach((input) => {
      if (input.dataset.entryKey === it.entry_key) input.disabled = true;
    });
  }
}

function previewCsv(event) {
  event.preventDefault();
  const file = rlEl("rl-csv-file").files[0];
  if (!file) return;
  rlEl("rl-shelf").value = rlEl("rl-csv-shelf").value;
  openPreview({ kind: "csv", file, format: rlEl("rl-csv-format").value }, file.name);
}

document.addEventListener("DOMContentLoaded", () => {
  loadImportLists();
  const tbody = document.getElementById("import-lists-tbody");
  if (tbody) {
    tbody.addEventListener("click", (e) => {
      const ds = e.target.dataset;
      if (ds.syncSource) syncSource(ds.syncSource, e.target);
      else if (ds.previewSource) {
        const name = e.target.closest("tr").firstElementChild.textContent;
        rlEl("rl-shelf").value = "";
        openPreview({ kind: "source", id: ds.previewSource }, name);
      } else if (ds.toggleSource) toggleSource(ds.toggleSource, ds.enabled !== "1");
      else if (ds.deleteSource) deleteSource(ds.deleteSource);
    });
  }
  const addForm = rlEl("reading-list-add-form");
  if (addForm) addForm.addEventListener("submit", addSource);
  const csvForm = rlEl("reading-list-csv-form");
  if (csvForm) csvForm.addEventListener("submit", previewCsv);
  const previewBody = rlEl("rl-preview-tbody");
  if (previewBody) previewBody.addEventListener("change", (e) => {
    if (e.target.dataset.entryKey !== undefined) onPick(e.target);
  });
  const refresh = rlEl("rl-refresh");
  if (refresh) refresh.addEventListener("click", () => { rl.offset = 0; runPreview(); });
  rlEl("rl-next-window")?.addEventListener("click", () => {
    rl.offset = rl.preview.next_offset;
    runPreview();
  });
  rlEl("rl-next-page")?.addEventListener("click", () => {
    rl.page = rl.preview.next_page;
    rl.offset = 0;
    runPreview();
  });
  rlEl("rl-import")?.addEventListener("click", importSelected);
});
