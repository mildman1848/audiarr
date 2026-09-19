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
let lastSearchMeta = { indexer: "—", total_results: null };

// localStorage key for the "only profile-fitting releases" toggle (issue #22).
// Namespaced and page-specific so it doesn't collide with other per-page
// UI prefs stored the same way.
const QUALITY_FIT_STORAGE_KEY = "audiarr.releaseSearch.onlyQualityFit";

// Statuses considered a profile fit when the toggle is on.
const QUALITY_FIT_STATUSES = new Set(["preferred", "accepted"]);

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

// quality_status (from app/quality.py, via /api/v1/releases/search) -> i18n
// label key and badge modifier class. Falls back to "unknown" for missing
// or unrecognized statuses (e.g. an older cached response).
const QUALITY_STATUS_KEYS = {
  preferred: "search_quality_preferred",
  accepted: "search_quality_accepted",
  below_cutoff: "search_quality_below_cutoff",
  rejected: "search_quality_rejected",
  unknown: "search_quality_unknown",
};

const QUALITY_STATUS_CLASSES = {
  preferred: "badge-quality-preferred",
  accepted: "badge-quality-accepted",
  below_cutoff: "badge-quality-below-cutoff",
  rejected: "badge-quality-rejected",
};

// Tooltip text: inferred container/codec/bitrate plus the English
// quality_reason string from app/quality.py, shown as-is (not translated).
function qualityTooltip(r) {
  const parts = [];
  if (r.quality_container) parts.push(String(r.quality_container).toUpperCase());
  // For mp3/flac the container IS the codec — avoid "MP3 MP3 320 kbps".
  if (r.quality_codec && r.quality_codec !== r.quality_container) {
    parts.push(String(r.quality_codec).toUpperCase());
  }
  if (r.quality_bitrate_kbps) parts.push(`${r.quality_bitrate_kbps} kbps`);
  const head = parts.join(" ");
  const reason = r.quality_reason || "";
  if (head && reason) return `${head} — ${reason}`;
  return head || reason;
}

// Compact "M4B · 128k" hint shown next to the badge when known.
function qualityCompact(r) {
  const bits = [];
  if (r.quality_container) bits.push(String(r.quality_container).toUpperCase());
  if (r.quality_bitrate_kbps) bits.push(`${r.quality_bitrate_kbps}k`);
  return bits.join(" · ");
}

// Missing/unrecognized quality_status (e.g. an older/stubbed response row)
// is always normalized to "unknown".
function qualityStatusOf(r) {
  return String(r.quality_status || "unknown");
}

// Whether a row counts as "profile-fitting" for the optional releases-page
// filter: preferred/accepted pass, below_cutoff/rejected/unknown do not.
function isQualityFit(r) {
  return QUALITY_FIT_STATUSES.has(qualityStatusOf(r));
}

function loadQualityFitOnly() {
  try {
    return localStorage.getItem(QUALITY_FIT_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function saveQualityFitOnly(value) {
  try {
    localStorage.setItem(QUALITY_FIT_STORAGE_KEY, value ? "true" : "false");
  } catch {
    // Storage unavailable (private browsing, disabled storage, etc.) — the
    // toggle still works for the current page load.
  }
}

function qualityBadge(r) {
  const status = qualityStatusOf(r);
  const cls = QUALITY_STATUS_CLASSES[status] || "";
  const label = T[QUALITY_STATUS_KEYS[status]] || T.search_quality_unknown || status;
  const tooltip = qualityTooltip(r);
  const titleAttr = tooltip ? ` title="${esc(tooltip)}"` : "";
  const compact = qualityCompact(r);
  const compactHtml = compact ? ` <span class="muted small">${esc(compact)}</span>` : "";
  const badgeClass = `badge ${cls}`.trim();
  return `<span class="${badgeClass}"${titleAttr}>${esc(label)}</span>${compactHtml}`;
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
    lastSearchMeta = { indexer: data.indexer, total_results: data.total_results };
    console.debug("release search: %d result(s)", lastReleases.length);
    renderResults();
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(T.search_results_error)} (${esc(err.message)})</p>`;
  }
}

function isQualityFitOnlyEnabled() {
  const toggle = document.getElementById("rs-quality-fit-only");
  return toggle ? toggle.checked : false;
}

// Renders lastReleases into #release-results, applying the "only
// profile-fitting releases" toggle if enabled. Re-run (without re-fetching)
// whenever the toggle changes so Grab buttons stay bound to visible rows only.
function renderResults() {
  const container = document.getElementById("release-results");
  if (!lastReleases.length) {
    container.innerHTML = `<p class="muted">${esc(T.search_results_empty)}</p>`;
    return;
  }

  const onlyFit = isQualityFitOnlyEnabled();
  const indexed = lastReleases.map((r, i) => ({ r, i }));
  const visible = onlyFit ? indexed.filter(({ r }) => isQualityFit(r)) : indexed;
  const hiddenCount = lastReleases.length - visible.length;
  const rows = visible.map(({ r, i }) => renderRow(r, i)).join("");
  const hiddenNotice = hiddenCount > 0
    ? `<p class="muted" id="rs-quality-fit-hidden-count">${esc(T.search_quality_fit_hidden_count)}: ${hiddenCount}</p>`
    : "";

  container.innerHTML = `
    <p class="muted">${esc(T.search_indexer_used)}: ${esc(lastSearchMeta.indexer || "—")}
      · ${esc(T.search_total_results)}: ${lastSearchMeta.total_results ?? "—"}</p>
    ${hiddenNotice}
    <table class="table">
      <thead>
        <tr>
          <th>${esc(T.search_col_protocol)}</th>
          <th>${esc(T.search_col_title)}</th>
          <th>${esc(T.search_col_quality)}</th>
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
      <td>${qualityBadge(r)}</td>
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

  const qualityFitToggle = document.getElementById("rs-quality-fit-only");
  if (qualityFitToggle) {
    qualityFitToggle.checked = loadQualityFitOnly();
    qualityFitToggle.addEventListener("change", () => {
      saveQualityFitOnly(qualityFitToggle.checked);
      renderResults();
    });
  }
});
