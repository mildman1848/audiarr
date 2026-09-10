// Metadata Search page: query the provider chain and add results to the library.
// Vanilla JS, follows the fetch + innerHTML render pattern used in app.js.

const T = window.AUDIARR_I18N || {};

// Cache the last result set so per-row "Add to library" buttons can look up
// their source row by index without re-parsing the DOM.
let lastResults = [];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function seriesLabel(row) {
  if (!row.series) return "—";
  return row.series_position ? `${row.series} #${row.series_position}` : row.series;
}

async function runSearch(event) {
  event.preventDefault();
  const container = document.getElementById("metadata-results");
  const query = document.getElementById("ms-query").value.trim();
  const locale = document.getElementById("ms-locale").value;
  const limit = document.getElementById("ms-limit").value || "10";
  if (!query) return;

  container.innerHTML = `<p class="muted">${esc(T.metadata_searching)}</p>`;
  try {
    const params = new URLSearchParams({ query, locale, limit });
    const resp = await fetch(`/api/v1/metadata/search?${params.toString()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    lastResults = data.results || [];
    if (!lastResults.length) {
      container.innerHTML = `<p class="muted">${esc(T.metadata_results_empty)}</p>`;
      return;
    }

    const rows = lastResults
      .map(
        (r, i) => `
        <tr>
          <td>${esc(r.title)}${
            r.subtitle ? `<br><span class="muted">${esc(r.subtitle)}</span>` : ""
          }</td>
          <td>${esc((r.authors || []).join(", ")) || "—"}</td>
          <td>${esc(seriesLabel(r))}</td>
          <td>${esc(r.asin || "—")}</td>
          <td>${esc(r.locale || "—")}</td>
          <td><span class="badge">${esc(r.provider_name)}</span></td>
          <td><button type="button" data-index="${i}">${esc(T.metadata_add_button)}</button></td>
        </tr>`
      )
      .join("");

    container.innerHTML = `
      <p class="muted">${esc(T.metadata_provider_used)}: ${esc(data.provider_used || "—")}
        · ${esc(T.metadata_total_results)}: ${data.total_results ?? "—"}</p>
      <table class="table">
        <thead>
          <tr>
            <th>${esc(T.metadata_col_title)}</th>
            <th>${esc(T.metadata_col_authors)}</th>
            <th>${esc(T.metadata_col_series)}</th>
            <th>${esc(T.metadata_col_asin)}</th>
            <th>${esc(T.metadata_col_locale)}</th>
            <th>${esc(T.metadata_col_provider)}</th>
            <th>${esc(T.metadata_col_actions)}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;

    container.querySelectorAll("button[data-index]").forEach((btn) => {
      btn.addEventListener("click", () => addToLibrary(Number(btn.dataset.index), btn));
    });
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(T.metadata_results_error)} (${esc(err.message)})</p>`;
  }
}

async function addToLibrary(index, btn) {
  const row = lastResults[index];
  if (!row) return;
  btn.disabled = true;

  // Map the normalized search row onto the BookIn contract (routes_library.py).
  const payload = {
    title: row.title,
    subtitle: row.subtitle || "",
    authors: row.authors || [],
    narrators: row.narrators || [],
    series: row.series || "",
    series_position: row.series_position || null,
    cover_url: row.cover_url || null,
    provider: row.provider_name || "",
    provider_id: row.provider_uid || "",
    locale: row.locale || document.getElementById("ms-locale").value,
  };

  try {
    const resp = await fetch("/api/v1/library/books", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (resp.status === 409) {
      btn.textContent = T.metadata_add_exists;
      if (window.AudiarrToast) window.AudiarrToast.info(`${T.metadata_add_exists}: ${row.title}`);
      return;
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    btn.textContent = T.metadata_add_success;
    if (window.AudiarrToast) window.AudiarrToast.success(`${T.metadata_add_success}: ${row.title}`);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = `${T.metadata_add_error} (${err.message})`;
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.metadata_add_error} (${err.message})`);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("metadata-search-form").addEventListener("submit", runSearch);
});
