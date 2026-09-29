// Book detail page: fetches a single book plus its files and renders a
// Readarr/Lidarr-style detail view. Vanilla JS, standalone (no shared module
// import), follows the fetch + innerHTML render pattern used in library.js.

const T = window.AUDIARR_I18N || {};

// Set once loadBook() resolves; the toolbar delete button (static markup in
// book_detail.html) reads this instead of waiting for the render pass.
let currentBook = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function formatDuration(seconds) {
  if (!seconds) return "—";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

// Precise clock-style timestamp for chapter start/end times, distinct from
// formatDuration's rounded "Xh Ym" badge style.
function formatTimestamp(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function tpl(key, values) {
  let text = T[key] || key;
  for (const [name, value] of Object.entries(values || {})) {
    text = text.replace(`{${name}}`, value);
  }
  return text;
}

// Audio-probe status badge (#71): "ok" needs no badge -- the data speaks for
// itself -- but pending/unavailable/error must stay visible per the
// non-fatal-but-visible extraction-failure requirement.
function probeStatusBadge(file) {
  if (file.probe_status === "ok") return "";
  if (file.probe_status === "pending") {
    return `<span class="badge" title="${esc(T.book_detail_files_probe_pending)}">${esc(T.book_detail_files_probe_pending)}</span>`;
  }
  if (file.probe_status === "unavailable") {
    return `<span class="badge badge-quality-below-cutoff" title="${esc(T.book_detail_files_probe_unavailable)}">${esc(T.book_detail_files_probe_unavailable)}</span>`;
  }
  const detail = file.probe_error ? `${T.book_detail_files_probe_error}: ${file.probe_error}` : T.book_detail_files_probe_error;
  return `<span class="badge badge-error" title="${esc(detail)}">${esc(T.book_detail_files_probe_error)}</span>`;
}

function chaptersCellHtml(file) {
  if (file.probe_status !== "ok") return probeStatusBadge(file);
  if (!file.chapter_count) return esc(T.book_detail_files_chapters_none);
  return `<span class="badge chapters-toggle" data-file-id="${file.id}" role="button" tabindex="0" style="cursor:pointer">${esc(tpl("book_detail_files_chapters_toggle", { count: file.chapter_count }))}</span>`;
}

function chapterRowsHtml(file) {
  const rows = (file.chapters || [])
    .map(
      (c) => `
      <tr>
        <td>${c.index + 1}</td>
        <td>${esc(c.title || T.book_detail_chapters_untitled)}</td>
        <td>${esc(formatTimestamp(c.start_seconds))}</td>
        <td>${esc(formatTimestamp(c.end_seconds))}</td>
      </tr>`
    )
    .join("");
  return `
    <tr class="chapters-row" data-file-id="${file.id}" hidden>
      <td colspan="8">
        <div class="table-scroll"><table class="table">
          <thead>
            <tr>
              <th>${esc(T.book_detail_chapters_col_index)}</th>
              <th>${esc(T.book_detail_chapters_col_title)}</th>
              <th>${esc(T.book_detail_chapters_col_start)}</th>
              <th>${esc(T.book_detail_chapters_col_end)}</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table></div>
      </td>
    </tr>`;
}

// Edition header row: format/abridgement/locale badges plus the book's
// narrators (narrators are book-level in the current schema -- shared by
// every edition/file of this book) so the file table itself communicates
// "which edition, read by whom" without scrolling back to the hero card.
function editionHeaderHtml(editionFiles, book) {
  const edition = editionFiles[0];
  const abridgedLabel = edition.edition_abridged
    ? T.book_detail_files_edition_abridged
    : T.book_detail_files_edition_unabridged;
  const narrators = (book.narrators || []).join(", ") || T.book_detail_files_narrator_unknown;
  return `
    <tr class="edition-row">
      <td colspan="8">
        <span class="badge">${esc((edition.edition_format || edition.format || "").toUpperCase())}</span>
        <span class="badge">${esc(abridgedLabel)}</span>
        ${edition.edition_locale ? `<span class="badge">${esc(edition.edition_locale)}</span>` : ""}
        <span class="badge">${esc(tpl("book_detail_files_file_count", { count: editionFiles.length }))}</span>
        <span class="muted">${esc(T.book_detail_files_narrated_by)}: ${esc(narrators)}</span>
      </td>
    </tr>`;
}

// Human-readable byte size, e.g. 336000000 -> "320.5 MB".
function humanSize(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let value = n;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded = value >= 100 || unit === 0 ? Math.round(value) : value.toFixed(1);
  return `${rounded} ${units[unit]}`;
}

function coverHtml(book) {
  if (book.cover_url) {
    return `<img src="${esc(book.cover_url)}" alt="${esc(T.book_detail_cover_alt)}">`;
  }
  return `<div class="book-hero-cover-placeholder">${esc(T.library_grid_cover_placeholder)}</div>`;
}

function qualityProfileOptionsHtml(selected, profiles) {
  const options = [
    `<option value="" ${selected ? "" : "selected"}>${esc(T.book_detail_quality_profile_default)}</option>`,
  ];
  for (const p of profiles) {
    options.push(
      `<option value="${esc(p.name)}" ${selected === p.name ? "selected" : ""}>${esc(p.name)}</option>`
    );
  }
  return options.join("");
}

function rootFolderOptionsHtml(selectedId, rootFolders) {
  const options = [
    `<option value="" ${selectedId ? "" : "selected"}>${esc(T.book_detail_root_folder_none)}</option>`,
  ];
  for (const f of rootFolders) {
    const label = f.label ? `${f.label} — ${f.path}` : f.path;
    options.push(
      `<option value="${f.id}" ${selectedId === f.id ? "selected" : ""}>${esc(label)}</option>`
    );
  }
  return options.join("");
}

// Initials for a monogram avatar fallback (Audiarr has no author/narrator
// photos at all, so this is always the fallback), same helper as library.js.
function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function monogramHtml(name) {
  if (!name) return "";
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  const hue = hash % 360;
  return `<span class="avatar-monogram" style="background:hsl(${hue},45%,32%);" title="${esc(name)}">${esc(initials(name))}</span>`;
}

function chipStyleAttr(color) {
  return color ? ` style="background:${esc(color)};color:#fff;border-color:transparent;"` : "";
}

function tagsEditorHtml(tags) {
  const chips = (tags || [])
    .map(
      (t) => `
      <span class="chip"${chipStyleAttr(t.color)}>
        ${esc(t.label)}
        <span class="chip-remove" data-remove-tag="${t.id}" title="${esc(T.book_detail_tags_remove_label)}" role="button">×</span>
      </span>`
    )
    .join("") || `<span class="muted">${esc(T.book_detail_tags_empty)}</span>`;
  return `
    <p class="book-hero-line">
      <span class="book-hero-line-label">${esc(T.book_detail_tags_label)}</span>
    </p>
    <div id="book-detail-tags" class="library-badge-row">${chips}</div>
    <div class="inline-form toolbar">
      <input type="text" id="book-detail-tag-input" placeholder="${esc(T.book_detail_tags_add_placeholder)}" maxlength="60">
      <button type="button" id="book-detail-tag-add-btn" class="btn btn-secondary">${esc(T.book_detail_tags_add)}</button>
    </div>`;
}

function renderBook(book, files, profiles, rootFolders) {
  const container = document.getElementById("book-detail");

  const authorsChips =
    (book.authors || [])
      .map((a) => `<span class="badge">${monogramHtml(a)}${esc(a)}</span>`)
      .join(" ") || "—";
  const narratorsChips =
    (book.narrators || [])
      .map((n) => `<span class="badge">${monogramHtml(n)}${esc(n)}</span>`)
      .join(" ") || "—";
  const seriesBadge = book.series
    ? `<span class="badge">${esc(book.series_position ? `${book.series} #${book.series_position}` : book.series)}</span>`
    : "";

  const providerRows = (book.provider_ids || [])
    .map(
      (p) => `
      <tr>
        <td>${esc(p.provider)}</td>
        <td>${esc(p.provider_id)}</td>
        <td>${esc(p.locale || "—")}</td>
      </tr>`
    )
    .join("");

  // Group files by edition (#71: edition/multi-file/narrator clarity) --
  // files arrive pre-sorted by edition_id/path (see get_book_files), so a
  // single pass is enough to detect each edition's boundaries and part count.
  const editionGroups = [];
  for (const f of files) {
    const last = editionGroups[editionGroups.length - 1];
    if (last && last[0].edition_id === f.edition_id) {
      last.push(f);
    } else {
      editionGroups.push([f]);
    }
  }

  const fileRows = editionGroups
    .map((group) => {
      const header = editionHeaderHtml(group, book);
      const rows = group
        .map(
          (f, i) => `
          <tr>
            <td>${esc(tpl("book_detail_files_part_label", { index: i + 1, total: group.length }))}</td>
            <td><code>${esc(f.path)}</code></td>
            <td>${f.probe_status === "ok" ? esc(formatDuration(f.duration_seconds)) : probeStatusBadge(f)}</td>
            <td>${f.probe_status === "ok" ? esc(f.bitrate_kbps ? `${f.bitrate_kbps} kbps` : "—") : "—"}</td>
            <td>${f.probe_status === "ok" ? esc(f.codec || "—") : "—"}</td>
            <td>${chaptersCellHtml(f)}</td>
            <td>${esc(humanSize(f.size_bytes))}</td>
            <td>${esc(f.added_at || "—")}</td>
          </tr>${f.chapter_count ? chapterRowsHtml(f) : ""}`
        )
        .join("");
      return header + rows;
    })
    .join("");

  container.innerHTML = `
    <div class="card book-hero">
      <div class="book-hero-cover">${coverHtml(book)}</div>
      <div class="book-hero-body">
        <h2 class="book-hero-title">${esc(book.title)}</h2>
        ${book.subtitle ? `<p class="book-hero-subtitle">${esc(book.subtitle)}</p>` : ""}
        ${seriesBadge ? `<div class="book-hero-badges">${seriesBadge}</div>` : ""}
        <p class="book-hero-line"><span class="book-hero-line-label">${esc(T.book_detail_authors_label)}</span>${authorsChips}</p>
        <p class="book-hero-line"><span class="book-hero-line-label">${esc(T.book_detail_narrators_label)}</span>${narratorsChips}</p>
        <p class="book-hero-line">
          <span class="book-hero-line-label">${esc(T.book_detail_quality_profile_label)}</span>
          <select id="book-detail-quality-profile-select">${qualityProfileOptionsHtml(book.quality_profile, profiles)}</select>
        </p>
        <p class="book-hero-line">
          <span class="book-hero-line-label">${esc(T.book_detail_root_folder_label)}</span>
          <select id="book-detail-root-folder-select">${rootFolderOptionsHtml(book.root_folder_id, rootFolders)}</select>
        </p>
        <p class="book-hero-meta-heading">${esc(T.book_detail_meta_heading)}</p>
        <div class="book-hero-stats">
          <span class="badge" title="${esc(T.book_detail_duration_label)}">${esc(formatDuration(book.duration_seconds))}</span>
          <span class="badge" title="${esc(T.book_detail_language_label)}">${esc(book.language || "—")}</span>
          <span class="badge" title="${esc(T.book_detail_publisher_label)}">${esc(book.publisher || "—")}</span>
          <span class="badge" title="${esc(T.book_detail_release_date_label)}">${esc(book.release_date || "—")}</span>
        </div>
        ${tagsEditorHtml(book.tags)}
      </div>
    </div>

    <section class="panel">
      <div class="section-head">
        <span class="eyebrow">${esc(T.book_detail_eyebrow)}</span>
        <h2>${esc(T.book_detail_provider_ids_heading)}</h2>
      </div>
      <div class="card">
        ${
          providerRows
            ? `<div class="table-scroll"><table class="table">
                 <thead>
                   <tr>
                     <th>${esc(T.book_detail_provider_col_provider)}</th>
                     <th>${esc(T.book_detail_provider_col_id)}</th>
                     <th>${esc(T.book_detail_provider_col_locale)}</th>
                   </tr>
                 </thead>
                 <tbody>${providerRows}</tbody>
               </table></div>`
            : `<p class="muted">${esc(T.book_detail_provider_ids_empty)}</p>`
        }
      </div>
    </section>

    <section class="panel">
      <div class="section-head">
        <span class="eyebrow">${esc(T.book_detail_eyebrow)}</span>
        <h2>${esc(T.book_detail_files_heading)}</h2>
      </div>
      <div class="card">
        ${
          fileRows
            ? `<div class="table-scroll"><table class="table">
                 <thead>
                   <tr>
                     <th>${esc(T.book_detail_files_col_part)}</th>
                     <th>${esc(T.book_detail_files_col_path)}</th>
                     <th>${esc(T.book_detail_files_col_duration)}</th>
                     <th>${esc(T.book_detail_files_col_bitrate)}</th>
                     <th>${esc(T.book_detail_files_col_codec)}</th>
                     <th>${esc(T.book_detail_files_col_chapters)}</th>
                     <th>${esc(T.book_detail_files_col_size)}</th>
                     <th>${esc(T.book_detail_files_col_added_at)}</th>
                   </tr>
                 </thead>
                 <tbody>${fileRows}</tbody>
               </table></div>`
            : `<p class="muted">${esc(T.book_detail_files_empty)}</p>`
        }
      </div>
    </section>`;
}

// Deletes the Audiarr DB record only; media files on disk are never touched.
async function deleteBook(bookId, title) {
  if (!window.confirm(`${T.book_detail_delete_confirm}\n\n${title}`)) return;
  try {
    const resp = await fetch(`/api/v1/library/books/${bookId}`, { method: "DELETE" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    if (window.AudiarrToast) window.AudiarrToast.success(T.book_detail_delete_success);
    window.location.href = "/library";
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.book_detail_delete_error} (${err.message})`);
  }
}

// Saves the book's quality profile via the existing book PATCH endpoint;
// "" selects the default (first configured) profile.
async function updateQualityProfile(bookId, value, select) {
  select.disabled = true;
  try {
    const resp = await fetch(`/api/v1/library/books/${bookId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quality_profile: value }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    currentBook = await resp.json();
    if (window.AudiarrToast) window.AudiarrToast.success(T.book_detail_quality_profile_updated);
  } catch (err) {
    if (window.AudiarrToast) {
      window.AudiarrToast.error(`${T.book_detail_quality_profile_error} (${err.message})`);
    }
  } finally {
    select.disabled = false;
  }
}

function wireQualityProfileSelect(bookId) {
  const select = document.getElementById("book-detail-quality-profile-select");
  if (!select) return;
  select.addEventListener("change", () => updateQualityProfile(bookId, select.value, select));
}

// Saves the book's root-folder preference (set at Add time or here, #50)
// via the existing book PATCH endpoint; selecting "No preference" clears it
// back to NULL server-side.
async function updateRootFolder(bookId, value, select) {
  select.disabled = true;
  try {
    const resp = await fetch(`/api/v1/library/books/${bookId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root_folder_id: value ? Number(value) : null }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    currentBook = await resp.json();
    if (window.AudiarrToast) window.AudiarrToast.success(T.book_detail_root_folder_updated);
  } catch (err) {
    if (window.AudiarrToast) {
      window.AudiarrToast.error(`${T.book_detail_root_folder_error} (${err.message})`);
    }
  } finally {
    select.disabled = false;
  }
}

function wireRootFolderSelect(bookId) {
  const select = document.getElementById("book-detail-root-folder-select");
  if (!select) return;
  select.addEventListener("change", () => updateRootFolder(bookId, select.value, select));
}

// Saves the book's full tag list via the PATCH endpoint (create-on-the-fly
// by label), then re-renders the hero card in place from the server's
// response so the chip list always reflects what actually got saved.
async function saveTags(bookId, labels) {
  const resp = await fetch(`/api/v1/library/books/${bookId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tags: labels }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

async function addTagToBook(bookId) {
  const input = document.getElementById("book-detail-tag-input");
  const label = input.value.trim();
  if (!label) return;
  const existingLabels = (currentBook.tags || []).map((t) => t.label);
  if (existingLabels.some((l) => l.toLowerCase() === label.toLowerCase())) {
    input.value = "";
    return;
  }
  try {
    currentBook = await saveTags(bookId, [...existingLabels, label]);
    refreshBookView();
    if (window.AudiarrToast) window.AudiarrToast.success(T.book_detail_tags_updated);
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.book_detail_tags_error} (${err.message})`);
  }
}

async function removeTagFromBook(bookId, tagId) {
  const remaining = (currentBook.tags || []).filter((t) => t.id !== tagId).map((t) => t.label);
  try {
    currentBook = await saveTags(bookId, remaining);
    refreshBookView();
    if (window.AudiarrToast) window.AudiarrToast.success(T.book_detail_tags_updated);
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.book_detail_tags_error} (${err.message})`);
  }
}

function wireTagsEditor(bookId) {
  const addBtn = document.getElementById("book-detail-tag-add-btn");
  if (addBtn) addBtn.addEventListener("click", () => addTagToBook(bookId));
  const input = document.getElementById("book-detail-tag-input");
  if (input) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addTagToBook(bookId);
      }
    });
  }
  document.querySelectorAll("[data-remove-tag]").forEach((el) => {
    el.addEventListener("click", () => removeTagFromBook(bookId, Number(el.dataset.removeTag)));
  });
}

let currentFiles = [];
let currentProfiles = [];
let currentRootFolders = [];

// Re-renders the hero/detail card from currentBook (e.g. after a tag edit)
// and re-wires the event listeners the fresh markup needs.
function refreshBookView() {
  renderBook(currentBook, currentFiles, currentProfiles, currentRootFolders);
  wireQualityProfileSelect(currentBook.id);
  wireRootFolderSelect(currentBook.id);
  wireTagsEditor(currentBook.id);
  wireChapterToggles();
}

// Shows/hides a file's chapter sub-table (#71) -- chapters are already
// embedded in the files response, so this is a pure DOM toggle, no fetch.
function wireChapterToggles() {
  document.querySelectorAll(".chapters-toggle").forEach((el) => {
    el.addEventListener("click", () => {
      const row = document.querySelector(`.chapters-row[data-file-id="${el.dataset.fileId}"]`);
      if (row) row.hidden = !row.hidden;
    });
  });
}

// Re-fetches this book's metadata from its linked provider (toolbar
// "Refresh" action, #50) and re-renders in place.
async function refreshBookMetadata(bookId, btn) {
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = T.book_detail_refreshing;
  try {
    const resp = await fetch(`/api/v1/library/books/${bookId}/refresh`, { method: "POST" });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const detail = typeof body.detail === "string" ? body.detail : `HTTP ${resp.status}`;
      throw new Error(detail);
    }
    currentBook = body;
    refreshBookView();
    if (window.AudiarrToast) window.AudiarrToast.success(T.book_detail_refresh_success);
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.book_detail_refresh_error} (${err.message})`);
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

// Toolbar "Search" action (#50): jumps to the interactive release Search
// page, prefilled with this book's title + authors, and triggers a search
// there -- reuses the existing /search page/endpoint rather than adding a
// new backend search trigger.
function searchForBook(book) {
  const query = [book.title, ...(book.authors || [])].filter(Boolean).join(" ");
  window.location.href = `/search?q=${encodeURIComponent(query)}`;
}

// Toolbar "Rescan" action (#68): triggers the existing Audiobookshelf
// library-scan connection endpoint. There is no per-book filesystem rescan
// route, so this asks the configured Audiobookshelf server to rescan its
// whole library (see app/api/routes_connections.py); the toolbar hint/title
// makes that scope explicit rather than implying a book-only rescan.
async function rescanLibrary(btn) {
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = T.book_detail_rescanning;
  try {
    const resp = await fetch("/api/v1/connections/audiobookshelf/scan", { method: "POST" });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const detail = typeof body.detail === "string" ? body.detail : `HTTP ${resp.status}`;
      throw new Error(detail);
    }
    if (window.AudiarrToast) window.AudiarrToast.success(body.message || T.book_detail_rescan_success);
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.book_detail_rescan_error} (${err.message})`);
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

// Toolbar "Organize" action (#68): scrolls to and focuses the existing
// Organize Files panel instead of duplicating its preview/apply logic.
function focusOrganizePanel() {
  const panel = document.getElementById("book-detail-organize-panel");
  if (!panel) return;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
  const previewBtn = document.getElementById("book-detail-organize-preview-btn");
  if (previewBtn) previewBtn.focus();
}

// -- organize (issue #29: preview/apply pattern-driven file moves) ----------

let organizePreviewSafe = false;

const ORGANIZE_STATUS_LABELS = {
  ready: "book_detail_organize_status_ready",
  unchanged: "book_detail_organize_status_unchanged",
  missing: "book_detail_organize_status_missing",
  conflict: "book_detail_organize_status_conflict",
  outside_root: "book_detail_organize_status_outside_root",
  error: "book_detail_organize_status_error",
  moved: "book_detail_organize_status_moved",
};

function organizeStatusLabel(status) {
  return T[ORGANIZE_STATUS_LABELS[status]] || status;
}

function renderOrganizeItems(heading, items) {
  const container = document.getElementById("book-detail-organize-result");
  if (!container) return;
  const rows = (items || [])
    .map(
      (i) => `
      <tr>
        <td><code>${esc(i.source_path)}</code></td>
        <td><code>${esc(i.target_path)}</code></td>
        <td>${esc(organizeStatusLabel(i.status))}</td>
        <td>${esc(i.reason || "—")}</td>
      </tr>`
    )
    .join("");
  container.innerHTML = `
    <p class="muted">${esc(heading)}</p>
    <div class="table-scroll"><table class="table">
      <thead>
        <tr>
          <th>${esc(T.book_detail_organize_col_source)}</th>
          <th>${esc(T.book_detail_organize_col_target)}</th>
          <th>${esc(T.book_detail_organize_col_status)}</th>
          <th>${esc(T.book_detail_organize_col_reason)}</th>
        </tr>
      </thead>
      <tbody>${rows || `<tr><td colspan="4" class="muted">${esc(T.book_detail_organize_empty)}</td></tr>`}</tbody>
    </table></div>`;
}

// Always a read-only, no-filesystem-write call: safe to run as often as the
// user likes before ever touching apply.
async function previewOrganize(bookId) {
  const applyBtn = document.getElementById("book-detail-organize-apply-btn");
  if (applyBtn) applyBtn.disabled = true;
  organizePreviewSafe = false;
  try {
    const resp = await fetch(`/api/v1/library/books/${bookId}/organize/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const preview = await resp.json();
    organizePreviewSafe = preview.safe_to_apply;
    renderOrganizeItems(
      preview.safe_to_apply
        ? T.book_detail_organize_preview_safe
        : T.book_detail_organize_preview_unsafe,
      preview.items
    );
    if (applyBtn) applyBtn.disabled = !organizePreviewSafe;
    if (window.AudiarrToast) window.AudiarrToast.success(T.book_detail_organize_preview_done);
  } catch (err) {
    if (window.AudiarrToast) {
      window.AudiarrToast.error(`${T.book_detail_organize_preview_error} (${err.message})`);
    }
  }
}

// Moves files on disk -- only reachable once a fresh preview reported
// safe_to_apply, and only after the user confirms; never deletes originals.
async function applyOrganize(bookId) {
  if (!organizePreviewSafe) return;
  if (!window.confirm(T.book_detail_organize_apply_confirm)) return;
  const applyBtn = document.getElementById("book-detail-organize-apply-btn");
  if (applyBtn) applyBtn.disabled = true;
  try {
    const resp = await fetch(`/api/v1/library/books/${bookId}/organize/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const detail =
        typeof body.detail === "string"
          ? body.detail
          : (body.detail && body.detail.message) || `HTTP ${resp.status}`;
      throw new Error(detail);
    }
    organizePreviewSafe = false;
    renderOrganizeItems(T.book_detail_organize_apply_done, body.items);
    if (window.AudiarrToast) window.AudiarrToast.success(T.book_detail_organize_apply_success);
    await loadBook();
  } catch (err) {
    if (window.AudiarrToast) {
      window.AudiarrToast.error(`${T.book_detail_organize_apply_error} (${err.message})`);
    }
  } finally {
    if (applyBtn) applyBtn.disabled = !organizePreviewSafe;
  }
}

async function loadBook() {
  const container = document.getElementById("book-detail");
  const bookId = container.dataset.bookId;
  try {
    const [bookResp, filesResp, settingsResp, rootFoldersResp] = await Promise.all([
      fetch(`/api/v1/library/books/${bookId}`),
      fetch(`/api/v1/library/books/${bookId}/files`),
      fetch("/api/v1/settings"),
      fetch("/api/v1/library/root-folders"),
    ]);
    if (bookResp.status === 404) {
      container.innerHTML = `<p class="muted">${esc(T.book_detail_not_found)}</p>`;
      return;
    }
    if (!bookResp.ok) throw new Error(`HTTP ${bookResp.status}`);
    currentBook = await bookResp.json();
    currentFiles = filesResp.ok ? await filesResp.json() : [];
    const settings = settingsResp.ok ? await settingsResp.json() : {};
    currentProfiles = settings.quality_profiles || [];
    currentRootFolders = rootFoldersResp.ok ? await rootFoldersResp.json() : [];
    refreshBookView();
    const deleteBtn = document.getElementById("book-detail-delete-btn");
    if (deleteBtn) deleteBtn.disabled = false;
    const refreshBtn = document.getElementById("book-detail-refresh-btn");
    if (refreshBtn) refreshBtn.disabled = false;
    const searchBtn = document.getElementById("book-detail-search-btn");
    if (searchBtn) searchBtn.disabled = false;
    const rescanBtn = document.getElementById("book-detail-rescan-btn");
    if (rescanBtn) rescanBtn.disabled = false;
    const organizeToolbarBtn = document.getElementById("book-detail-organize-toolbar-btn");
    if (organizeToolbarBtn) organizeToolbarBtn.disabled = false;
    const organizePreviewBtn = document.getElementById("book-detail-organize-preview-btn");
    if (organizePreviewBtn) organizePreviewBtn.disabled = false;
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(T.book_detail_error)} (${esc(err.message)})</p>`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadBook();
  const deleteBtn = document.getElementById("book-detail-delete-btn");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", () => {
      if (currentBook) deleteBook(currentBook.id, currentBook.title);
    });
  }
  const refreshBtn = document.getElementById("book-detail-refresh-btn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      if (currentBook) refreshBookMetadata(currentBook.id, refreshBtn);
    });
  }
  const searchBtn = document.getElementById("book-detail-search-btn");
  if (searchBtn) {
    searchBtn.addEventListener("click", () => {
      if (currentBook) searchForBook(currentBook);
    });
  }
  const rescanBtn = document.getElementById("book-detail-rescan-btn");
  if (rescanBtn) {
    rescanBtn.addEventListener("click", () => rescanLibrary(rescanBtn));
  }
  const organizeToolbarBtn = document.getElementById("book-detail-organize-toolbar-btn");
  if (organizeToolbarBtn) {
    organizeToolbarBtn.addEventListener("click", focusOrganizePanel);
  }
  const organizePreviewBtn = document.getElementById("book-detail-organize-preview-btn");
  if (organizePreviewBtn) {
    organizePreviewBtn.addEventListener("click", () => {
      if (currentBook) previewOrganize(currentBook.id);
    });
  }
  const organizeApplyBtn = document.getElementById("book-detail-organize-apply-btn");
  if (organizeApplyBtn) {
    organizeApplyBtn.addEventListener("click", () => {
      if (currentBook) applyOrganize(currentBook.id);
    });
  }
});
