// Library page: Arr-style overview — stat cards, a searchable grid/table of
// books, root-folder management, and a dry-run/real import panel.
// Vanilla JS, follows the fetch + innerHTML render pattern used in app.js.

const T = window.AUDIARR_I18N || {};

// Cache of the last fetched books/root-folders so client-side search and the
// grid/table toggle can re-render without refetching.
let allBooks = [];
let rootFolders = [];
let currentView = "grid";
let currentSort = "title";

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

function seriesLabel(b) {
  if (!b.series) return "";
  return b.series_position ? `${b.series} #${b.series_position}` : b.series;
}

function coverHtml(b) {
  if (b.cover_url) {
    return `<img src="${esc(b.cover_url)}" alt="${esc(T.library_cover_alt)}">`;
  }
  return `<div class="library-cover-placeholder">${esc(T.library_grid_cover_placeholder)}</div>`;
}

// Badges shown on both the grid card and could be reused for the table view;
// series is only included when present.
function badgeRowHtml(b) {
  const badges = [];
  if (b.series) badges.push(`<span class="badge">${esc(seriesLabel(b))}</span>`);
  if (b.language) badges.push(`<span class="badge">${esc(b.language)}</span>`);
  badges.push(
    `<span class="badge" title="${esc(T.library_badge_files)}">${b.file_count} ${esc(T.library_badge_files)}</span>`
  );
  badges.push(
    `<span class="badge" title="${esc(T.library_badge_duration)}">${esc(formatDuration(b.duration_seconds))}</span>`
  );
  badges.push(
    `<span class="badge" title="${esc(T.library_badge_size)}">${esc(humanSize(b.size_bytes))}</span>`
  );
  return `<div class="library-badge-row">${badges.join("")}</div>`;
}

// -- stats ----------------------------------------------------------------------

async function loadStats() {
  try {
    const resp = await fetch("/api/v1/library/stats");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const s = await resp.json();
    document.getElementById("stat-books").textContent = s.book_count;
    document.getElementById("stat-files").textContent = s.file_count;
    document.getElementById("stat-size").textContent = humanSize(s.total_size_bytes);
    document.getElementById("stat-root-folders").textContent = s.root_folder_count;
  } catch (err) {
    console.debug("library stats: failed to load (%s)", err.message);
  }
}

// -- books ------------------------------------------------------------------------

function filteredBooks() {
  const query = (document.getElementById("library-filter").value || "").trim().toLowerCase();
  if (!query) return allBooks;
  return allBooks.filter((b) => {
    const haystack = [b.title, b.subtitle, ...(b.authors || []), ...(b.narrators || []), b.series]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
}

// Client-side only: sorts a copy of the already-filtered list, leaving
// allBooks untouched.
function sortedBooks(books) {
  const sorted = [...books];
  if (currentSort === "author") {
    sorted.sort((a, b) => (a.authors?.[0] || "").localeCompare(b.authors?.[0] || ""));
  } else {
    sorted.sort((a, b) => (a.title || "").localeCompare(b.title || ""));
  }
  return sorted;
}

function renderBooks() {
  const container = document.getElementById("library-books");

  if (!allBooks.length) {
    container.innerHTML = `<p class="muted">${esc(T.library_books_empty)}</p>`;
    return;
  }

  const books = sortedBooks(filteredBooks());
  if (!books.length) {
    container.innerHTML = `<p class="muted">${esc(T.library_search_no_results)}</p>`;
    return;
  }

  container.innerHTML = currentView === "table" ? renderTable(books) : renderGrid(books);
  wireBookActions(container);
}

function bookActionsHtml(b) {
  return `
    <div class="library-card-actions">
      <a href="/library/books/${b.id}" class="btn btn-secondary">${esc(T.library_action_details)}</a>
      <button type="button" class="btn btn-danger" data-delete-book="${b.id}" data-book-title="${esc(b.title)}">${esc(T.library_action_delete)}</button>
    </div>`;
}

function renderGrid(books) {
  const cards = books
    .map(
      (b) => `
      <div class="library-card">
        <div class="library-cover">${coverHtml(b)}</div>
        <div class="library-card-body">
          <h3 class="library-card-title" title="${esc(b.title)}">${esc(b.title)}</h3>
          <p class="library-card-author" title="${esc((b.authors || []).join(", "))}">${esc((b.authors || []).join(", ")) || "—"}</p>
          ${badgeRowHtml(b)}
          ${bookActionsHtml(b)}
        </div>
      </div>`
    )
    .join("");
  return `<div class="library-grid">${cards}</div>`;
}

function renderTable(books) {
  const rows = books
    .map(
      (b) => `
      <tr>
        <td>${esc(b.title)}</td>
        <td>${esc((b.authors || []).join(", ")) || "—"}</td>
        <td>${esc((b.narrators || []).join(", ")) || "—"}</td>
        <td>${b.series ? esc(seriesLabel(b)) : "—"}</td>
        <td>${esc(formatDuration(b.duration_seconds))}</td>
        <td>${b.file_count}</td>
        <td>${esc(humanSize(b.size_bytes))}</td>
        <td>${bookActionsHtml(b)}</td>
      </tr>`
    )
    .join("");
  return `
    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>${esc(T.library_col_title)}</th>
            <th>${esc(T.library_col_authors)}</th>
            <th>${esc(T.library_col_narrators)}</th>
            <th>${esc(T.library_col_series)}</th>
            <th>${esc(T.library_col_duration)}</th>
            <th>${esc(T.library_col_files)}</th>
            <th>${esc(T.library_col_size)}</th>
            <th>${esc(T.library_col_actions)}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function wireBookActions(container) {
  container.querySelectorAll("[data-delete-book]").forEach((btn) => {
    btn.addEventListener("click", () => deleteBook(btn.dataset.deleteBook, btn.dataset.bookTitle));
  });
}

async function loadBooks() {
  const container = document.getElementById("library-books");
  try {
    const resp = await fetch("/api/v1/library/books");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    allBooks = await resp.json();
    renderBooks();
  } catch (err) {
    allBooks = [];
    container.innerHTML = `<p class="muted">${esc(T.library_books_error)} (${esc(err.message)})</p>`;
  }
}

// Deletes the Audiarr DB record only; media files on disk are never touched.
async function deleteBook(bookId, title) {
  if (!window.confirm(`${T.library_delete_confirm}\n\n${title}`)) return;
  try {
    const resp = await fetch(`/api/v1/library/books/${bookId}`, { method: "DELETE" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    if (window.AudiarrToast) window.AudiarrToast.success(T.library_delete_success);
    await Promise.all([loadBooks(), loadStats()]);
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.library_delete_error} (${err.message})`);
  }
}

function highlightViewButtons() {
  const gridBtn = document.getElementById("view-grid-btn");
  const tableBtn = document.getElementById("view-table-btn");
  gridBtn.classList.toggle("active", currentView === "grid");
  tableBtn.classList.toggle("active", currentView === "table");
}

function setView(view) {
  currentView = view;
  highlightViewButtons();
  renderBooks();
}

// -- root folders -----------------------------------------------------------------

async function loadRootFolders() {
  const container = document.getElementById("root-folders-list");
  const importSelect = document.getElementById("import-root-folder");
  try {
    const resp = await fetch("/api/v1/library/root-folders");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    rootFolders = await resp.json();

    importSelect.innerHTML = rootFolders
      .map((f) => `<option value="${f.id}">${esc(f.label ? `${f.label} — ${f.path}` : f.path)}</option>`)
      .join("");

    if (!rootFolders.length) {
      container.innerHTML = `<p class="muted">${esc(T.library_root_folders_empty)}</p>`;
      return;
    }

    container.innerHTML = `<ul class="plain-list">${rootFolders
      .map(
        (f) => `
        <li style="display:flex;align-items:center;justify-content:space-between;gap:0.5rem;">
          <span><code>${esc(f.path)}</code>${f.label ? ` — ${esc(f.label)}` : ""}</span>
          <button type="button" class="btn btn-danger" data-delete-folder="${f.id}">${esc(T.library_root_folder_delete)}</button>
        </li>`
      )
      .join("")}</ul>`;

    container.querySelectorAll("[data-delete-folder]").forEach((btn) => {
      btn.addEventListener("click", () => deleteRootFolder(btn.dataset.deleteFolder));
    });
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(T.library_root_folders_error)} (${esc(err.message)})</p>`;
  }
}

async function addRootFolder(event) {
  event.preventDefault();
  const msg = document.getElementById("root-folder-msg");
  const path = document.getElementById("rf-path").value.trim();
  const label = document.getElementById("rf-label").value.trim();
  if (!path) return;

  try {
    const resp = await fetch("/api/v1/library/root-folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, label }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    msg.textContent = "";
    document.getElementById("root-folder-form").reset();
    await Promise.all([loadRootFolders(), loadStats()]);
    if (window.AudiarrToast) window.AudiarrToast.success(T.library_root_folder_added);
  } catch (err) {
    const text = `${T.library_root_folder_add_error} (${err.message})`;
    msg.textContent = text;
    if (window.AudiarrToast) window.AudiarrToast.error(text);
  }
}

// Deletes the root-folder record only; nothing on disk is touched.
async function deleteRootFolder(folderId) {
  if (!window.confirm(T.library_root_folder_delete_confirm)) return;
  try {
    const resp = await fetch(`/api/v1/library/root-folders/${folderId}`, { method: "DELETE" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    if (window.AudiarrToast) window.AudiarrToast.success(T.library_root_folder_deleted);
    await Promise.all([loadRootFolders(), loadStats()]);
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.library_root_folder_delete_error} (${err.message})`);
  }
}

// -- import -------------------------------------------------------------------------

function renderImportResult(data) {
  const summary = document.getElementById("import-summary");
  const results = document.getElementById("import-results");

  summary.innerHTML = `
    <p><code>${esc(data.root_path)}</code></p>
    <ul class="plain-list">
      <li>${esc(T.library_import_candidates)}: ${data.total_candidates}</li>
      <li>${esc(T.library_import_matched)}: ${data.matched}</li>
      <li>${esc(T.library_import_unmatched)}: ${data.unmatched}</li>
      <li>${esc(T.library_import_errors)}: ${data.errors}</li>
      <li>${esc(T.library_import_files)}: ${data.file_count}</li>
    </ul>
    <p class="muted">${esc(data.message)}</p>`;

  const rows = (data.results || [])
    .map(
      (r) => `
      <tr>
        <td><span class="badge">${esc(r.status)}</span></td>
        <td><code>${esc(r.folder_path)}</code></td>
        <td>${esc(r.matched_asin || (r.matched_book_id != null ? `#${r.matched_book_id}` : "—"))}</td>
        <td>${r.score != null ? Number(r.score).toFixed(2) : "—"}</td>
        <td>${esc(r.method || "—")}</td>
        <td>${esc(r.detail || "—")}</td>
      </tr>`
    )
    .join("");

  results.innerHTML = !rows
    ? `<p class="muted">${esc(T.library_import_results_empty)}</p>`
    : `
      <table class="table">
        <thead>
          <tr>
            <th>${esc(T.library_import_col_status)}</th>
            <th>${esc(T.library_import_col_folder)}</th>
            <th>${esc(T.library_import_col_matched)}</th>
            <th>${esc(T.library_import_col_score)}</th>
            <th>${esc(T.library_import_col_method)}</th>
            <th>${esc(T.library_import_col_detail)}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
}

async function runImport(dryRun) {
  const summary = document.getElementById("import-summary");
  const results = document.getElementById("import-results");
  const rootFolderId = Number(document.getElementById("import-root-folder").value);
  const locale = document.getElementById("import-locale").value;

  if (!rootFolderId) {
    summary.innerHTML = `<p class="muted">${esc(T.library_import_no_root_folder)}</p>`;
    results.innerHTML = "";
    return;
  }
  // Real imports write matched books/files to the Audiarr DB; confirm first.
  // Media files are never moved, copied, or deleted by this action.
  if (!dryRun && !window.confirm(T.library_import_run_confirm)) return;

  summary.innerHTML = `<p class="muted">${esc(T.library_import_running)}</p>`;
  results.innerHTML = "";
  try {
    const resp = await fetch("/api/v1/import/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root_folder_id: rootFolderId, dry_run: dryRun, locale }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderImportResult(data);
    if (window.AudiarrToast) window.AudiarrToast.success(T.library_import_success);
    if (!dryRun) {
      await Promise.all([loadBooks(), loadStats()]);
    }
  } catch (err) {
    summary.innerHTML = `<p class="muted">${esc(T.library_import_error)} (${esc(err.message)})</p>`;
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.library_import_error} (${err.message})`);
  }
}

// -- init -----------------------------------------------------------------------------

async function refreshAll() {
  await Promise.all([loadBooks(), loadRootFolders(), loadStats()]);
}

document.addEventListener("DOMContentLoaded", () => {
  highlightViewButtons();
  refreshAll();

  document.getElementById("root-folder-form").addEventListener("submit", addRootFolder);
  document.getElementById("library-filter").addEventListener("input", renderBooks);
  document.getElementById("library-sort").addEventListener("change", (event) => {
    currentSort = event.target.value;
    renderBooks();
  });
  document.getElementById("view-grid-btn").addEventListener("click", () => setView("grid"));
  document.getElementById("view-table-btn").addEventListener("click", () => setView("table"));
  document.getElementById("library-refresh-top").addEventListener("click", refreshAll);
  document.getElementById("import-dry-run-btn").addEventListener("click", () => runImport(true));
  document.getElementById("import-run-btn").addEventListener("click", () => runImport(false));
});
