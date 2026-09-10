// Release search page: query Prowlarr for releases and grab usenet releases
// into SABnzbd. Vanilla JS, follows the fetch + innerHTML render pattern used
// in metadata.js / settings.js.
//
// Grab requests run independently: each row keeps its own in-flight state so
// several grabs can be triggered concurrently without blocking the table.

const T = window.AUDIARR_I18N || {};

// Cache the last result set so per-row Grab buttons can look up their source
// row by index without re-parsing the DOM.
let lastReleases = [];

// Follows settings.js escapeHtml: escape every value interpolated into innerHTML.
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
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

// Prowlarr reports release age in whole days.
function humanAge(age) {
  const n = Number(age);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n === 0) return T.search_age_today || "today";
  return `${Math.round(n)} ${T.search_age_days || "d"}`;
}

function protocolBadge(protocol) {
  const proto = String(protocol || "").toLowerCase();
  if (proto === "usenet") {
    return `<span class="badge badge-queued">${esc(T.search_protocol_usenet)}</span>`;
  }
  if (proto === "torrent") {
    return `<span class="badge">${esc(T.search_protocol_torrent)}</span>`;
  }
  return `<span class="badge">${esc(protocol || "—")}</span>`;
}

// Inline 503 warning: config missing, point the user at /settings.
function renderConfigWarning(container) {
  container.innerHTML = `
    <div class="card danger-card">
      <p>${esc(T.search_config_missing)}</p>
      <p><a href="/settings">${esc(T.search_config_link)}</a></p>
    </div>`;
}

async function runSearch(event) {
  event.preventDefault();
  const container = document.getElementById("release-results");
  const query = document.getElementById("rs-query").value.trim();
  const limit = document.getElementById("rs-limit").value || "50";
  if (!query) return;

  console.debug("release search: query=%s limit=%s", query, limit);
  container.innerHTML = `<p class="muted">${esc(T.search_searching)}</p>`;
  try {
    const params = new URLSearchParams({ query, limit });
    const resp = await fetch(`/api/v1/releases/search?${params.toString()}`);
    if (resp.status === 503) {
      renderConfigWarning(container);
      return;
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    lastReleases = data.releases || [];
    console.debug("release search: %d result(s)", lastReleases.length);
    if (!lastReleases.length) {
      container.innerHTML = `<p class="muted">${esc(T.search_results_empty)}</p>`;
      return;
    }

    const rows = lastReleases.map((r, i) => renderRow(r, i)).join("");
    container.innerHTML = `
      <p class="muted">${esc(T.search_indexer_used)}: ${esc(data.indexer || "—")}
        · ${esc(T.search_total_results)}: ${data.total_results ?? "—"}</p>
      <table class="table">
        <thead>
          <tr>
            <th>${esc(T.search_col_protocol)}</th>
            <th>${esc(T.search_col_title)}</th>
            <th>${esc(T.search_col_indexer)}</th>
            <th>${esc(T.search_col_size)}</th>
            <th>${esc(T.search_col_age)}</th>
            <th>${esc(T.search_col_seeders)}</th>
            <th>${esc(T.search_col_actions)}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;

    container.querySelectorAll("button[data-index]").forEach((btn) => {
      btn.addEventListener("click", () => grabRelease(Number(btn.dataset.index), btn));
    });
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(T.search_results_error)} (${esc(err.message)})</p>`;
  }
}

function renderRow(r, i) {
  const proto = String(r.protocol || "").toLowerCase();
  const isUsenet = proto === "usenet";
  const seeders = proto === "torrent" ? esc(r.seeders ?? "—") : "—";
  const action = isUsenet
    ? `<button type="button" class="btn btn-primary" data-index="${i}">${esc(T.search_grab)}</button>`
    : `<button type="button" class="btn btn-secondary" disabled title="${esc(T.search_grab_torrent_unsupported)}">${esc(T.search_grab)}</button>`;
  return `
    <tr>
      <td>${protocolBadge(r.protocol)}</td>
      <td>${esc(r.title)}</td>
      <td>${esc(r.indexer || "—")}</td>
      <td>${esc(humanSize(r.size))}</td>
      <td>${esc(humanAge(r.age))}</td>
      <td>${seeders}</td>
      <td>${action}</td>
    </tr>`;
}

async function grabRelease(index, btn) {
  const row = lastReleases[index];
  if (!row) return;

  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = T.search_grabbing;
  console.debug("grab: %s (indexer_id=%s)", row.title, row.indexer_id);

  try {
    const resp = await fetch("/api/v1/releases/grab", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        indexer_id: row.indexer_id,
        guid: row.guid,
        download_url: row.download_url,
        title: row.title,
      }),
    });
    if (resp.status === 503) {
      btn.disabled = false;
      btn.textContent = originalLabel;
      if (window.AudiarrToast) window.AudiarrToast.error(T.search_config_missing);
      return;
    }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);

    if (data.ok) {
      btn.textContent = T.search_grabbed;
      const suffix = data.nzo_id ? ` (${data.nzo_id})` : "";
      if (window.AudiarrToast) {
        window.AudiarrToast.success(`${T.search_grab_success}: ${row.title}${suffix}`);
      }
    } else {
      btn.disabled = false;
      btn.textContent = originalLabel;
      if (window.AudiarrToast) {
        window.AudiarrToast.error(`${T.search_grab_error}: ${data.message || ""}`.trim());
      }
    }
  } catch (err) {
    btn.disabled = false;
    btn.textContent = originalLabel;
    if (window.AudiarrToast) {
      window.AudiarrToast.error(`${T.search_grab_error} (${err.message})`);
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("release-search-form").addEventListener("submit", runSearch);
});
