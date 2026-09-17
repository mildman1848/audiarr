// Wanted/Missing page: monitored books that have zero files in the library
// yet, Arr-style. Read-only in this slice — no indexer search/grab actions.

const T = window.AUDIARR_I18N || {};

// Cache of the last fetched list so the filter field can re-render without
// refetching, same pattern as library.js.
let allMissing = [];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function reasonLabel(reason) {
  if (reason === "missingFiles") return T.wanted_reason_missing_files;
  return reason;
}

function coverHtml(b) {
  if (b.cover_url) {
    return `<img src="${esc(b.cover_url)}" alt="${esc(T.library_cover_alt)}">`;
  }
  return `<div class="library-cover-placeholder">${esc(T.library_grid_cover_placeholder)}</div>`;
}

function filteredMissing() {
  const query = (document.getElementById("wanted-filter").value || "").trim().toLowerCase();
  if (!query) return allMissing;
  return allMissing.filter((b) => {
    const haystack = [b.title, ...(b.authors || []), b.series].join(" ").toLowerCase();
    return haystack.includes(query);
  });
}

function renderRow(b) {
  return `
    <tr>
      <td><div class="library-cover small">${coverHtml(b)}</div></td>
      <td>${esc(b.title)}</td>
      <td>${esc((b.authors || []).join(", ")) || "—"}</td>
      <td>${esc(b.release_date) || "—"}</td>
      <td>${esc(b.language) || "—"}</td>
      <td><span class="badge">${esc(reasonLabel(b.reason))}</span></td>
      <td><a href="/library/books/${b.id}" class="btn btn-secondary">${esc(T.wanted_action_open_book)}</a></td>
    </tr>`;
}

function renderMissing() {
  const container = document.getElementById("wanted-missing-list");

  if (!allMissing.length) {
    container.innerHTML = window.AudiarrUI.emptyState({ icon: "☆", title: T.wanted_empty });
    return;
  }

  const books = filteredMissing();
  if (!books.length) {
    container.innerHTML = window.AudiarrUI.emptyState({ icon: "⌕", title: T.toolbar_search_no_results });
    return;
  }

  container.innerHTML = `
    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th></th>
            <th>${esc(T.wanted_col_title)}</th>
            <th>${esc(T.wanted_col_authors)}</th>
            <th>${esc(T.wanted_col_release_date)}</th>
            <th>${esc(T.wanted_col_language)}</th>
            <th>${esc(T.wanted_col_reason)}</th>
            <th>${esc(T.wanted_col_actions)}</th>
          </tr>
        </thead>
        <tbody>${books.map(renderRow).join("")}</tbody>
      </table>
    </div>`;
}

async function loadMissing() {
  const container = document.getElementById("wanted-missing-list");
  try {
    const resp = await fetch("/api/v1/wanted/missing");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    allMissing = await resp.json();
    renderMissing();
  } catch (err) {
    allMissing = [];
    container.innerHTML = `<p class="muted">${esc(T.wanted_load_error)} (${esc(err.message)})</p>`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadMissing();
  document.getElementById("wanted-refresh-top").addEventListener("click", loadMissing);
  document.getElementById("wanted-filter").addEventListener("input", renderMissing);
});
