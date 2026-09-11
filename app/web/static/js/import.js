// Import page: unmatched-folder review, manual match search, and ignore list.
// Vanilla JS, follows the fetch + innerHTML render pattern used in library.js.

const T = window.AUDIARR_I18N || {};

// Cache of the last fetched search results so "Use this" buttons can look up
// their source row by index without re-parsing the DOM.
let lastMatchResults = [];
let matchFolderPath = "";

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function coverHtml(row) {
  if (row.cover_url) {
    return `<img src="${esc(row.cover_url)}" alt="${esc(T.library_cover_alt)}" style="width:100%;height:100%;object-fit:cover;border-radius:4px;display:block;">`;
  }
  return `<div class="muted small" style="display:flex;align-items:center;justify-content:center;height:100%;text-align:center;padding:0.2rem;">${esc(T.library_no_cover)}</div>`;
}

// -- unmatched table ----------------------------------------------------------------

async function loadUnmatched() {
  const container = document.getElementById("import-unmatched");
  try {
    const resp = await fetch("/api/v1/import/unmatched");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const rows = await resp.json();
    renderUnmatched(rows);
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(T.import_unmatched_error)} (${esc(err.message)})</p>`;
    console.debug("import unmatched: failed to load (%s)", err.message);
  }
}

function renderUnmatched(rows) {
  const container = document.getElementById("import-unmatched");
  if (!rows.length) {
    container.innerHTML = `<p class="muted">${esc(T.import_empty)}</p>`;
    return;
  }

  const body = rows
    .map(
      (r) => `
      <tr>
        <td><code>${esc(r.folder_path)}</code></td>
        <td>${esc(r.guessed_title) || "—"}<br><span class="muted small">${esc(r.guessed_author) || "—"}</span></td>
        <td>${esc(r.last_tried_at) || "—"}</td>
        <td>${r.exists ? "" : `<span class="badge badge-error">${esc(T.import_missing)}</span>`}</td>
        <td>
          <div class="button-row">
            <button type="button" class="btn btn-primary" data-match-folder="${esc(r.folder_path)}">${esc(T.import_action_match)}</button>
            <button type="button" class="btn btn-danger" data-ignore-folder="${esc(r.folder_path)}">${esc(T.import_action_ignore)}</button>
          </div>
        </td>
      </tr>`
    )
    .join("");

  container.innerHTML = `
    <table class="table">
      <thead>
        <tr>
          <th>${esc(T.import_col_folder)}</th>
          <th>${esc(T.import_col_guess)}</th>
          <th>${esc(T.import_col_last_tried)}</th>
          <th></th>
          <th>${esc(T.library_col_actions)}</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>`;

  container.querySelectorAll("[data-match-folder]").forEach((btn) => {
    btn.addEventListener("click", () => openMatchModal(btn.dataset.matchFolder));
  });
  container.querySelectorAll("[data-ignore-folder]").forEach((btn) => {
    btn.addEventListener("click", () => ignoreFolder(btn.dataset.ignoreFolder));
  });
}

async function ignoreFolder(folderPath) {
  try {
    const resp = await fetch("/api/v1/import/ignore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: folderPath }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    if (window.AudiarrToast) window.AudiarrToast.success(T.import_ignore_success);
    await refreshAll();
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.import_error} (${err.message})`);
  }
}

// -- ignored list ---------------------------------------------------------------------

async function loadIgnored() {
  const container = document.getElementById("import-ignored");
  try {
    const resp = await fetch("/api/v1/import/ignores");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const rows = await resp.json();
    renderIgnored(rows);
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(T.import_unmatched_error)} (${esc(err.message)})</p>`;
    console.debug("import ignores: failed to load (%s)", err.message);
  }
}

function renderIgnored(rows) {
  const container = document.getElementById("import-ignored");
  if (!rows.length) {
    container.innerHTML = `<p class="muted">${esc(T.import_ignored_empty)}</p>`;
    return;
  }

  container.innerHTML = `<ul class="plain-list">${rows
    .map(
      (r) => `
      <li style="display:flex;align-items:center;justify-content:space-between;gap:0.5rem;">
        <span><code>${esc(r.path)}</code>${r.note ? ` — ${esc(r.note)}` : ""}</span>
        <button type="button" class="btn btn-secondary" data-unignore-folder="${esc(r.path)}">${esc(T.import_action_unignore)}</button>
      </li>`
    )
    .join("")}</ul>`;

  container.querySelectorAll("[data-unignore-folder]").forEach((btn) => {
    btn.addEventListener("click", () => unignoreFolder(btn.dataset.unignoreFolder));
  });
}

async function unignoreFolder(folderPath) {
  try {
    const resp = await fetch("/api/v1/import/unignore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: folderPath }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    if (window.AudiarrToast) window.AudiarrToast.success(T.import_unignore_success);
    await refreshAll();
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.import_error} (${err.message})`);
  }
}

// -- match modal ------------------------------------------------------------------------

function openMatchModal(folderPath) {
  matchFolderPath = folderPath;
  lastMatchResults = [];
  document.getElementById("import-match-folder").textContent = folderPath;
  document.getElementById("import-match-query").value = "";
  document.getElementById("import-match-results").innerHTML = `<p class="muted">${esc(T.metadata_results_hint)}</p>`;
  document.getElementById("import-match-overlay").hidden = false;
}

function closeMatchModal() {
  document.getElementById("import-match-overlay").hidden = true;
  matchFolderPath = "";
  lastMatchResults = [];
}

async function runMatchSearch(event) {
  event.preventDefault();
  const container = document.getElementById("import-match-results");
  const query = document.getElementById("import-match-query").value.trim();
  const locale = document.getElementById("import-match-locale").value;
  if (!query) return;

  container.innerHTML = `<p class="muted">${esc(T.metadata_searching)}</p>`;
  try {
    const params = new URLSearchParams({ query, locale, limit: "10" });
    const resp = await fetch(`/api/v1/metadata/search?${params.toString()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    lastMatchResults = data.results || [];
    if (!lastMatchResults.length) {
      container.innerHTML = `<p class="muted">${esc(T.metadata_results_empty)}</p>`;
      return;
    }

    container.innerHTML = lastMatchResults
      .map(
        (r, i) => `
        <div class="card" style="display:flex;gap:0.75rem;">
          <div style="flex:0 0 64px;height:64px;background:var(--bg-panel-alt);border-radius:4px;overflow:hidden;">${coverHtml(r)}</div>
          <div style="flex:1;min-width:0;">
            <h3 style="margin:0 0 0.2rem;font-size:0.95rem;">${esc(r.title)}</h3>
            <p class="muted small" style="margin:0 0 0.2rem;">${esc((r.authors || []).join(", ")) || "—"}</p>
            <p class="muted small" style="margin:0 0 0.3rem;">${esc((r.narrators || []).join(", ")) || "—"}</p>
            <p class="muted small" style="margin:0;">${esc(T.metadata_col_asin)}: ${esc(r.asin || "—")}</p>
          </div>
          <div class="button-row" style="align-items:flex-start;">
            <button type="button" class="btn btn-primary" data-use-index="${i}" ${r.asin ? "" : "disabled"}>${esc(T.import_match_use)}</button>
          </div>
        </div>`
      )
      .join("");

    container.querySelectorAll("[data-use-index]").forEach((btn) => {
      btn.addEventListener("click", () => useMatch(Number(btn.dataset.useIndex), btn));
    });
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(T.metadata_results_error)} (${esc(err.message)})</p>`;
  }
}

async function useMatch(index, btn) {
  const row = lastMatchResults[index];
  if (!row || !row.asin || !matchFolderPath) return;
  btn.disabled = true;
  btn.textContent = T.import_match_running;

  const locale = document.getElementById("import-match-locale").value;
  try {
    const resp = await fetch("/api/v1/import/match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_path: matchFolderPath, asin: row.asin, locale }),
    });
    if (resp.status === 404) {
      if (window.AudiarrToast) window.AudiarrToast.error(T.import_match_folder_gone);
      closeMatchModal();
      await refreshAll();
      return;
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (window.AudiarrToast) window.AudiarrToast.success(`${T.import_match_success}: ${data.title}`);
    closeMatchModal();
    await refreshAll();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = T.import_match_use;
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.import_match_error} (${err.message})`);
  }
}

// -- init -----------------------------------------------------------------------------

async function refreshAll() {
  await Promise.all([loadUnmatched(), loadIgnored()]);
}

document.addEventListener("DOMContentLoaded", () => {
  refreshAll();

  document
    .querySelectorAll("#import-refresh-top")
    .forEach((btn) => btn.addEventListener("click", refreshAll));
  document.getElementById("import-match-form").addEventListener("submit", runMatchSearch);
  document.getElementById("import-match-close").addEventListener("click", closeMatchModal);
  document.getElementById("import-match-overlay").addEventListener("click", (event) => {
    if (event.target.id === "import-match-overlay") closeMatchModal();
  });
});
