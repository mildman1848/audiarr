// Wanted/Missing page: monitored books that have zero files in the library
// yet (Missing section, read-only), plus monitored books that already have
// files but fall below their quality profile's cutoff (Cutoff unmet
// section), where a "Search for upgrade" button triggers a server-side
// search + grab against the book's own profile.

const T = window.AUDIARR_I18N || {};

// Cache of the last fetched list so the filter field can re-render without
// refetching, same pattern as library.js.
let allMissing = [];

// Cache of the last fetched cutoff-unmet list.
let allCutoff = [];

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

// Compact "Tier name · M4B · 128k" hint for a cutoff candidate's current quality.
function cutoffQualitySummary(b) {
  const parts = [];
  if (b.current_quality_name) parts.push(b.current_quality_name);
  if (b.current_container) parts.push(String(b.current_container).toUpperCase());
  if (b.current_bitrate_kbps) parts.push(`${b.current_bitrate_kbps}k`);
  return parts.join(" · ") || "—";
}

function renderCutoffRow(b) {
  return `
    <tr>
      <td>${esc(b.title)}</td>
      <td>${esc((b.authors || []).join(", ")) || "—"}</td>
      <td>${esc(cutoffQualitySummary(b))}</td>
      <td>${esc(b.profile_name)} / ${esc(b.cutoff_name)}</td>
      <td><button type="button" class="btn btn-primary" data-book-id="${b.id}">${esc(T.wanted_cutoff_search)}</button></td>
    </tr>`;
}

function renderCutoff() {
  const container = document.getElementById("wanted-cutoff-list");

  if (!allCutoff.length) {
    container.innerHTML = window.AudiarrUI.emptyState({ icon: "⇪", title: T.wanted_cutoff_empty });
    return;
  }

  container.innerHTML = `
    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>${esc(T.wanted_col_title)}</th>
            <th>${esc(T.wanted_col_authors)}</th>
            <th>${esc(T.wanted_cutoff_col_current_quality)}</th>
            <th>${esc(T.wanted_cutoff_col_profile)}</th>
            <th>${esc(T.wanted_col_actions)}</th>
          </tr>
        </thead>
        <tbody>${allCutoff.map(renderCutoffRow).join("")}</tbody>
      </table>
    </div>`;

  container.querySelectorAll("button[data-book-id]").forEach((btn) => {
    btn.addEventListener("click", () => searchCutoffUpgrade(Number(btn.dataset.bookId), btn));
  });
}

async function loadCutoff() {
  const container = document.getElementById("wanted-cutoff-list");
  try {
    const resp = await fetch("/api/v1/wanted/cutoff");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    allCutoff = await resp.json();
    renderCutoff();
  } catch (err) {
    allCutoff = [];
    container.innerHTML = `<p class="muted">${esc(T.wanted_cutoff_load_error)} (${esc(err.message)})</p>`;
  }
}

async function searchCutoffUpgrade(bookId, btn) {
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = T.wanted_cutoff_searching;
  console.debug("cutoff upgrade search: book_id=%s", bookId);

  try {
    const resp = await fetch(`/api/v1/wanted/cutoff/${bookId}/search`, { method: "POST" });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok && resp.status !== 202) throw new Error(data.detail || `HTTP ${resp.status}`);

    if (data.ok) {
      if (window.AudiarrToast) {
        window.AudiarrToast.success(`${T.wanted_cutoff_search_success}: ${data.release_title || ""}`.trim());
      }
      await loadCutoff();
    } else {
      btn.disabled = false;
      btn.textContent = originalLabel;
      if (window.AudiarrToast) {
        window.AudiarrToast.error(
          `${T.wanted_cutoff_search_no_release}: ${data.reason || data.message || ""}`.trim()
        );
      }
    }
  } catch (err) {
    btn.disabled = false;
    btn.textContent = originalLabel;
    if (window.AudiarrToast) {
      window.AudiarrToast.error(`${T.wanted_cutoff_search_error} (${err.message})`);
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadMissing();
  loadCutoff();
  document.getElementById("wanted-refresh-top").addEventListener("click", () => {
    loadMissing();
    loadCutoff();
  });
  document.getElementById("wanted-filter").addEventListener("input", renderMissing);
});
