// Add New page (metadata search -> Add wizard): query the provider chain,
// then add a result to the library through a Starr-style multi-step Add
// modal (root folder + quality profile + monitor mode + author/narrator
// confirmation) instead of a single one-click POST.
// Vanilla JS, follows the fetch + innerHTML render pattern used in app.js.

const T = window.AUDIARR_I18N || {};

// Cache the last result set so per-row "Add" buttons can look up their
// source row by index without re-parsing the DOM.
let lastResults = [];

// State for the currently open Add modal: the search-result row being
// added, plus the (user-editable) author/narrator chip lists.
let addModalState = null;
let rootFoldersCache = [];
let qualityProfilesCache = [];

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

// Initials for a monogram avatar fallback (Audiarr has no author/narrator
// photos), same helper as library.js/book_detail.js.
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

function personChipsHtml(names) {
  return (
    (names || []).map((n) => `<span class="badge">${monogramHtml(n)}${esc(n)}</span>`).join(" ") ||
    "—"
  );
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
          <td>${personChipsHtml(r.authors)}</td>
          <td>${personChipsHtml(r.narrators)}</td>
          <td>${esc(seriesLabel(r))}</td>
          <td>${esc(r.asin || "—")}</td>
          <td>${esc(r.locale || "—")}</td>
          <td><span class="badge">${esc(r.provider_name)}</span></td>
          <td><button type="button" class="btn btn-primary" data-index="${i}">${esc(T.metadata_add_button)}</button></td>
        </tr>`
      )
      .join("");

    container.innerHTML = `
      <p class="muted">${esc(T.metadata_provider_used)}: ${esc(data.provider_used || "—")}
        · ${esc(T.metadata_total_results)}: ${data.total_results ?? "—"}</p>
      <div class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>${esc(T.metadata_col_title)}</th>
              <th>${esc(T.library_col_authors)}</th>
              <th>${esc(T.library_col_narrators)}</th>
              <th>${esc(T.metadata_col_series)}</th>
              <th>${esc(T.metadata_col_asin)}</th>
              <th>${esc(T.metadata_col_locale)}</th>
              <th>${esc(T.metadata_col_provider)}</th>
              <th>${esc(T.metadata_col_actions)}</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;

    container.querySelectorAll("button[data-index]").forEach((btn) => {
      btn.addEventListener("click", () => openAddModal(Number(btn.dataset.index)));
    });
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(T.metadata_results_error)} (${esc(err.message)})</p>`;
  }
}

// -- Add wizard modal (issue #50: Starr-style multi-step add flow) --------

function chipEditorHtml(containerId, names) {
  return (names || [])
    .map(
      (name, i) => `
      <span class="chip">
        ${esc(name)}
        <span class="chip-remove" data-remove-person="${i}" data-chip-list="${containerId}" role="button">×</span>
      </span>`
    )
    .join("") || `<span class="muted small">${esc(T.add_book_none)}</span>`;
}

function renderPersonChips() {
  document.getElementById("add-book-authors-chips").innerHTML = chipEditorHtml(
    "authors",
    addModalState.authors
  );
  document.getElementById("add-book-narrators-chips").innerHTML = chipEditorHtml(
    "narrators",
    addModalState.narrators
  );
  wirePersonChipRemoval();
}

function wirePersonChipRemoval() {
  document.querySelectorAll("[data-remove-person]").forEach((el) => {
    el.addEventListener("click", () => {
      const list = el.dataset.chipList === "authors" ? addModalState.authors : addModalState.narrators;
      list.splice(Number(el.dataset.removePerson), 1);
      renderPersonChips();
    });
  });
}

function addPerson(list, input) {
  const value = input.value.trim();
  input.value = "";
  if (!value || list.includes(value)) return;
  list.push(value);
  renderPersonChips();
}

function rootFolderSelectOptionsHtml() {
  if (!rootFoldersCache.length) {
    return `<option value="">${esc(T.add_book_root_folder_none_configured)}</option>`;
  }
  return rootFoldersCache
    .map((f) => `<option value="${f.id}">${esc(f.label ? `${f.label} — ${f.path}` : f.path)}</option>`)
    .join("");
}

function qualityProfileSelectOptionsHtml() {
  const options = [`<option value="">${esc(T.book_detail_quality_profile_default)}</option>`];
  for (const p of qualityProfilesCache) {
    options.push(`<option value="${esc(p.name)}">${esc(p.name)}</option>`);
  }
  return options.join("");
}

function openAddModal(index) {
  const row = lastResults[index];
  if (!row) return;
  addModalState = {
    row,
    authors: [...(row.authors || [])],
    narrators: [...(row.narrators || [])],
  };

  document.getElementById("add-book-summary").innerHTML = `
    <p class="book-hero-title" style="font-size:1.05rem;margin:0 0 0.2rem;">${esc(row.title)}</p>
    ${row.subtitle ? `<p class="muted small" style="margin:0 0 0.4rem;">${esc(row.subtitle)}</p>` : ""}
    <p class="muted small" style="margin:0;">${esc(T.metadata_col_provider)}: ${esc(row.provider_name)} · ${esc(T.metadata_col_asin)}: ${esc(row.asin || "—")}</p>`;

  renderPersonChips();

  const rootFolderSelect = document.getElementById("add-book-root-folder");
  rootFolderSelect.innerHTML = rootFolderSelectOptionsHtml();
  rootFolderSelect.disabled = !rootFoldersCache.length;

  document.getElementById("add-book-quality-profile").innerHTML = qualityProfileSelectOptionsHtml();
  document.getElementById("add-book-monitored").checked = true;

  const msg = document.getElementById("add-book-msg");
  msg.textContent = rootFoldersCache.length ? "" : T.add_book_root_folder_none_configured;

  const confirmBtn = document.getElementById("add-book-confirm");
  confirmBtn.disabled = !rootFoldersCache.length;
  confirmBtn.textContent = T.add_book_confirm;

  document.getElementById("add-book-overlay").hidden = false;
}

function closeAddModal() {
  document.getElementById("add-book-overlay").hidden = true;
  addModalState = null;
}

async function confirmAddBook() {
  if (!addModalState) return;
  const row = addModalState.row;
  const rootFolderId = Number(document.getElementById("add-book-root-folder").value) || null;
  const qualityProfile = document.getElementById("add-book-quality-profile").value;
  const monitored = document.getElementById("add-book-monitored").checked;
  if (!rootFolderId) return;

  const confirmBtn = document.getElementById("add-book-confirm");
  const msg = document.getElementById("add-book-msg");
  confirmBtn.disabled = true;
  confirmBtn.textContent = T.add_book_adding;
  msg.textContent = "";

  const createPayload = {
    title: row.title,
    subtitle: row.subtitle || "",
    authors: addModalState.authors,
    narrators: addModalState.narrators,
    series: row.series || "",
    series_position: row.series_position || null,
    cover_url: row.cover_url || null,
    provider: row.provider_name || "",
    provider_id: row.provider_uid || "",
    locale: row.locale || document.getElementById("ms-locale").value,
    monitored,
    root_folder_id: rootFolderId,
    quality_profile: qualityProfile,
  };

  try {
    const createResp = await fetch("/api/v1/library/books", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(createPayload),
    });
    if (createResp.status === 409) {
      msg.textContent = `${T.metadata_add_exists}: ${row.title}`;
      if (window.AudiarrToast) window.AudiarrToast.info(`${T.metadata_add_exists}: ${row.title}`);
      confirmBtn.disabled = false;
      confirmBtn.textContent = T.add_book_confirm;
      return;
    }
    if (!createResp.ok) {
      const body = await createResp.json().catch(() => ({}));
      const detail = typeof body.detail === "string" ? body.detail : `HTTP ${createResp.status}`;
      throw new Error(detail);
    }

    if (window.AudiarrToast) window.AudiarrToast.success(`${T.metadata_add_success}: ${row.title}`);
    closeAddModal();
  } catch (err) {
    msg.textContent = `${T.metadata_add_error} (${err.message})`;
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.metadata_add_error} (${err.message})`);
    confirmBtn.disabled = false;
    confirmBtn.textContent = T.add_book_confirm;
  }
}

async function loadAddModalOptions() {
  try {
    const [rootFoldersResp, settingsResp] = await Promise.all([
      fetch("/api/v1/library/root-folders"),
      fetch("/api/v1/settings"),
    ]);
    rootFoldersCache = rootFoldersResp.ok ? await rootFoldersResp.json() : [];
    const settings = settingsResp.ok ? await settingsResp.json() : {};
    qualityProfilesCache = settings.quality_profiles || [];
  } catch (err) {
    console.debug("add-book modal: failed to preload root folders/quality profiles (%s)", err.message);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("metadata-search-form").addEventListener("submit", runSearch);
  loadAddModalOptions();

  document.getElementById("add-book-author-input").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addPerson(addModalState.authors, event.target);
  });
  document.getElementById("add-book-narrator-input").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addPerson(addModalState.narrators, event.target);
  });
  document.getElementById("add-book-confirm").addEventListener("click", confirmAddBook);
  document.getElementById("add-book-cancel").addEventListener("click", closeAddModal);
  document.getElementById("add-book-overlay").addEventListener("click", (event) => {
    if (event.target.id === "add-book-overlay") closeAddModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !document.getElementById("add-book-overlay").hidden) {
      closeAddModal();
    }
  });
});
