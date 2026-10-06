// Follows page (#80): author/series follows with a manual refresh and a
// candidate review panel. Future releases become monitored books on the
// server at refresh time; back-catalog candidates are only added when the
// user selects them here. Nothing on this page triggers a download.

const T = window.AUDIARR_I18N || {};

let follows = [];
let selectedFollow = null; // follow object whose candidates are shown
let candidates = [];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function tpl(key, values) {
  let text = T[key] || key;
  for (const [name, value] of Object.entries(values || {})) {
    text = text.replace(`{${name}}`, value);
  }
  return text;
}

function toast(kind, message) {
  if (window.AudiarrToast) window.AudiarrToast[kind](message);
}

async function errorDetail(resp) {
  const body = await resp.json().catch(() => ({}));
  return typeof body.detail === "string" ? body.detail : `HTTP ${resp.status}`;
}

function kindLabel(kind) {
  return kind === "series" ? T.follows_kind_series : T.follows_kind_author;
}

// ---- follows table ----------------------------------------------------

function renderFollowRow(f) {
  const counts = f.candidate_counts || {};
  return `
    <tr>
      <td>${esc(f.name)}</td>
      <td><span class="badge">${esc(kindLabel(f.kind))}</span></td>
      <td>${esc(f.last_refreshed_at || T.follows_never_refreshed)}</td>
      <td>${esc(tpl("follows_counts_summary", counts))}</td>
      <td>
        <button type="button" class="btn btn-primary" data-refresh="${f.id}">${esc(T.follows_refresh)}</button>
        <button type="button" class="btn btn-secondary" data-review="${f.id}">${esc(T.follows_review)}</button>
        <button type="button" class="btn btn-secondary" data-delete="${f.id}">${esc(T.follows_delete)}</button>
      </td>
    </tr>`;
}

function renderFollows() {
  const container = document.getElementById("follows-list");
  if (!follows.length) {
    container.innerHTML = window.AudiarrUI.emptyState({
      icon: "♥",
      title: T.follows_empty,
      hint: T.follows_empty_hint,
    });
    return;
  }
  container.innerHTML = `
    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>${esc(T.follows_col_name)}</th>
            <th>${esc(T.follows_col_kind)}</th>
            <th>${esc(T.follows_col_last_refreshed)}</th>
            <th>${esc(T.follows_col_counts)}</th>
            <th>${esc(T.follows_col_actions)}</th>
          </tr>
        </thead>
        <tbody>${follows.map(renderFollowRow).join("")}</tbody>
      </table>
    </div>`;
  container.querySelectorAll("button[data-refresh]").forEach((btn) => {
    btn.addEventListener("click", () => refreshFollow(Number(btn.dataset.refresh), btn));
  });
  container.querySelectorAll("button[data-review]").forEach((btn) => {
    btn.addEventListener("click", () => reviewFollow(Number(btn.dataset.review)));
  });
  container.querySelectorAll("button[data-delete]").forEach((btn) => {
    btn.addEventListener("click", () => deleteFollow(Number(btn.dataset.delete)));
  });
}

async function loadFollows() {
  const container = document.getElementById("follows-list");
  try {
    const resp = await fetch("/api/v1/follows");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    follows = await resp.json();
    renderFollows();
  } catch (err) {
    follows = [];
    container.innerHTML = window.AudiarrUI.emptyState({
      icon: "⚠",
      title: `${T.follows_load_error} (${err.message})`,
      actionHtml: `<button type="button" class="btn btn-secondary" id="follows-retry-btn">${esc(T.follows_retry)}</button>`,
    });
    document.getElementById("follows-retry-btn").addEventListener("click", loadFollows);
  }
}

async function addFollow() {
  const nameInput = document.getElementById("follows-name");
  const name = nameInput.value.trim();
  if (!name) return;
  const kind = document.getElementById("follows-kind").value;
  try {
    const resp = await fetch("/api/v1/follows", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, name }),
    });
    if (resp.status === 409) {
      toast("error", T.follows_duplicate);
      return;
    }
    if (!resp.ok) throw new Error(await errorDetail(resp));
    nameInput.value = "";
    toast("success", T.follows_added);
    await loadFollows();
  } catch (err) {
    toast("error", `${T.follows_add_error} (${err.message})`);
  }
}

async function refreshFollow(id, btn) {
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = T.follows_refreshing;
  try {
    const resp = await fetch(`/api/v1/follows/${id}/refresh`, { method: "POST" });
    if (!resp.ok) throw new Error(await errorDetail(resp));
    const data = await resp.json();
    toast("success", tpl("follows_refresh_success", {
      found: data.found,
      new: data.new,
      created: data.future_created,
    }));
    await loadFollows();
    if (selectedFollow && selectedFollow.id === id) await loadCandidates();
  } catch (err) {
    toast("error", `${T.follows_refresh_error} (${err.message})`);
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

async function deleteFollow(id) {
  const follow = follows.find((f) => f.id === id);
  if (!follow || !window.confirm(tpl("follows_delete_confirm", { name: follow.name }))) return;
  try {
    const resp = await fetch(`/api/v1/follows/${id}`, { method: "DELETE" });
    if (!resp.ok) throw new Error(await errorDetail(resp));
    if (selectedFollow && selectedFollow.id === id) clearCandidates();
    await loadFollows();
  } catch (err) {
    toast("error", `${T.follows_delete_error} (${err.message})`);
  }
}

// ---- candidate review panel -------------------------------------------

function clearCandidates() {
  selectedFollow = null;
  candidates = [];
  document.getElementById("follows-candidates-actions").hidden = true;
  document.getElementById("follows-candidates-heading").textContent = T.follows_candidates_heading;
  document.getElementById("follows-candidates").innerHTML =
    `<p class="muted">${esc(T.follows_candidates_select_follow)}</p>`;
}

// Status badge: "added" wins over "owned" (we created that book ourselves),
// excluded wins over everything else the user hid.
function statusBadges(c) {
  const badges = [];
  if (c.status === "excluded") {
    badges.push(`<span class="badge badge-failed">${esc(T.follows_status_excluded)}</span>`);
  } else if (c.status === "added") {
    badges.push(`<span class="badge badge-success">${esc(T.follows_status_added)}</span>`);
  } else if (c.owned) {
    badges.push(`<span class="badge badge-success">${esc(T.follows_status_owned)}</span>`);
  } else if (c.status === "future") {
    badges.push(`<span class="badge badge-pending">${esc(T.follows_status_future)}</span>`);
  } else {
    badges.push(`<span class="badge">${esc(T.follows_status_backlog)}</span>`);
  }
  return badges.join(" ");
}

function renderCandidateRow(c) {
  const series = c.series ? (c.series_position ? `${c.series} #${c.series_position}` : c.series) : "—";
  const selectable = c.status === "backlog" || c.status === "future" || c.status === "excluded";
  const bookLink = c.book_id
    ? ` <a href="/library/books/${c.book_id}">↗</a>`
    : "";
  return `
    <tr>
      <td>${selectable ? `<input type="checkbox" data-cand="${c.id}">` : ""}</td>
      <td>${esc(c.title)}${bookLink}</td>
      <td>${esc((c.authors || []).join(", ")) || "—"}</td>
      <td>${esc(series)}</td>
      <td>${esc(c.release_date) || "—"}</td>
      <td>${statusBadges(c)}</td>
    </tr>`;
}

function renderCandidates() {
  const container = document.getElementById("follows-candidates");
  document.getElementById("follows-candidates-actions").hidden = !candidates.length;
  if (!candidates.length) {
    container.innerHTML = `<p class="muted">${esc(T.follows_candidates_empty)}</p>`;
    return;
  }
  container.innerHTML = `
    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th><input type="checkbox" id="follows-select-all" aria-label="${esc(T.follows_select_all)}"></th>
            <th>${esc(T.follows_col_title)}</th>
            <th>${esc(T.follows_col_authors)}</th>
            <th>${esc(T.follows_col_series)}</th>
            <th>${esc(T.follows_col_release_date)}</th>
            <th>${esc(T.follows_col_status)}</th>
          </tr>
        </thead>
        <tbody>${candidates.map(renderCandidateRow).join("")}</tbody>
      </table>
    </div>`;
  document.getElementById("follows-select-all").addEventListener("change", (event) => {
    container.querySelectorAll("input[data-cand]").forEach((box) => {
      box.checked = event.target.checked;
    });
  });
}

async function loadCandidates() {
  const container = document.getElementById("follows-candidates");
  try {
    const resp = await fetch(`/api/v1/follows/${selectedFollow.id}/candidates`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    candidates = await resp.json();
    renderCandidates();
  } catch (err) {
    candidates = [];
    document.getElementById("follows-candidates-actions").hidden = true;
    container.innerHTML = `<p class="muted">${esc(T.follows_candidates_load_error)} (${esc(err.message)})</p>`;
  }
}

async function reviewFollow(id) {
  selectedFollow = follows.find((f) => f.id === id) || null;
  if (!selectedFollow) return;
  document.getElementById("follows-candidates-heading").textContent =
    `${T.follows_candidates_heading}: ${selectedFollow.name}`;
  await loadCandidates();
}

function selectedIds() {
  return [...document.querySelectorAll("#follows-candidates input[data-cand]:checked")].map((box) =>
    Number(box.dataset.cand)
  );
}

// action: "add" | "exclude" | "restore" -- server skips ineligible rows
// (e.g. owned or already-added candidates on "add") and reports them.
async function candidateAction(action) {
  const ids = selectedIds();
  if (!ids.length) {
    toast("info", T.follows_none_selected);
    return;
  }
  try {
    const resp = await fetch(`/api/v1/follows/${selectedFollow.id}/candidates/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    if (!resp.ok) throw new Error(await errorDetail(resp));
    const data = await resp.json();
    toast("success", tpl("follows_action_done", {
      changed: data.changed.length,
      skipped: data.skipped.length,
    }));
    await loadFollows();
    await loadCandidates();
  } catch (err) {
    toast("error", `${T.follows_action_error} (${err.message})`);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadFollows();
  document.getElementById("follows-add-btn").addEventListener("click", addFollow);
  document.getElementById("follows-name").addEventListener("keydown", (event) => {
    if (event.key === "Enter") addFollow();
  });
  document.getElementById("follows-add-selected-btn").addEventListener("click", () => candidateAction("add"));
  document.getElementById("follows-exclude-selected-btn").addEventListener("click", () => candidateAction("exclude"));
  document.getElementById("follows-restore-selected-btn").addEventListener("click", () => candidateAction("restore"));
});
