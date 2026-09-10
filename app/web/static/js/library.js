// Library page: books table, root-folder management, and dry-run import.
// Vanilla JS, follows the fetch + innerHTML render pattern used in app.js.

const T = window.AUDIARR_I18N || {};

// Track the first root folder so the dry-run import targets it.
let firstRootFolderId = null;

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

async function loadBooks() {
  const container = document.getElementById("library-books");
  try {
    const resp = await fetch("/api/v1/library/books");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const books = await resp.json();

    if (!Array.isArray(books) || books.length === 0) {
      container.innerHTML = `<p class="muted">${esc(T.library_books_empty)}</p>`;
      return;
    }

    const rows = books
      .map(
        (b) => `
        <tr>
          <td>${esc(b.title)}</td>
          <td>${esc((b.authors || []).join(", ")) || "—"}</td>
          <td>${esc(b.language || "—")}</td>
          <td>${esc((b.narrators || []).join(", ")) || "—"}</td>
          <td>${esc(formatDuration(b.duration_seconds))}</td>
        </tr>`
      )
      .join("");

    container.innerHTML = `
      <table class="table">
        <thead>
          <tr>
            <th>${esc(T.library_col_title)}</th>
            <th>${esc(T.library_col_authors)}</th>
            <th>${esc(T.library_col_language)}</th>
            <th>${esc(T.library_col_narrators)}</th>
            <th>${esc(T.library_col_duration)}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(T.library_books_error)} (${esc(err.message)})</p>`;
  }
}

async function loadRootFolders() {
  const container = document.getElementById("root-folders-list");
  try {
    const resp = await fetch("/api/v1/library/root-folders");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const folders = await resp.json();

    firstRootFolderId = folders.length ? folders[0].id : null;

    if (!folders.length) {
      container.innerHTML = `<p class="muted">${esc(T.library_root_folders_empty)}</p>`;
      return;
    }

    container.innerHTML = `<ul class="plain-list">${folders
      .map(
        (f) =>
          `<li><code>${esc(f.path)}</code>${f.label ? ` — ${esc(f.label)}` : ""}</li>`
      )
      .join("")}</ul>`;
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
    await loadRootFolders();
  } catch (err) {
    msg.textContent = `${T.library_root_folder_add_error} (${err.message})`;
  }
}

async function runDryImport() {
  const summary = document.getElementById("import-summary");
  if (!firstRootFolderId) {
    summary.innerHTML = `<p class="muted">${esc(T.library_import_no_root_folder)}</p>`;
    return;
  }

  summary.innerHTML = `<p class="muted">${esc(T.library_import_running)}</p>`;
  try {
    const resp = await fetch("/api/v1/import/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root_folder_id: firstRootFolderId, dry_run: true }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

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
  } catch (err) {
    summary.innerHTML = `<p class="muted">${esc(T.library_import_error)} (${esc(err.message)})</p>`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadBooks();
  loadRootFolders();
  document.getElementById("root-folder-form").addEventListener("submit", addRootFolder);
  document.getElementById("dry-run-btn").addEventListener("click", runDryImport);
});
