// Settings -> Tags page: CRUD editor for the shared tag vocabulary (#27).
// Tags have their own dedicated REST API (app/api/routes_tags.py) and
// persist immediately per action, unlike the rest of settings.js which
// edits a local copy of the settings document and saves it as a whole.


// Wrapped in an IIFE: this page also loads settings.js, and both modules
// declare a top-level `const T` -- a bare top-level declaration here makes
// the whole script die with "Identifier T has already been declared".
(() => {
const T = window.AUDIARR_I18N || {};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

let tagsState = [];
let editingTagId = null;

function chipStyleAttr(color) {
  return color ? ` style="background:${esc(color)};color:#fff;border-color:transparent;"` : "";
}

function renderTags() {
  const el = document.getElementById("tags-list");
  if (!el) return;
  if (!tagsState.length) {
    el.innerHTML = `<p class="muted">${esc(T.settings_tags_empty)}</p>`;
    return;
  }
  const rowStyle = "display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;";
  el.innerHTML = `<ul class="plain-list">${tagsState
    .map((tag) => {
      if (editingTagId === tag.id) {
        return `
        <li data-tag-row="${tag.id}" style="${rowStyle}">
          <input type="text" data-tag-edit-label="${tag.id}" value="${esc(tag.label)}" maxlength="60">
          <input type="color" data-tag-edit-color="${tag.id}" value="${esc(tag.color || "#8a8a8a")}" aria-label="${esc(T.settings_tags_color_label)}">
          <button type="button" class="btn btn-primary" data-tag-save="${tag.id}">${esc(T.settings_tags_save)}</button>
          <button type="button" class="btn btn-secondary" data-tag-cancel="${tag.id}">${esc(T.settings_tags_cancel)}</button>
        </li>`;
      }
      return `
      <li data-tag-row="${tag.id}" style="${rowStyle}">
        <span class="chip"${chipStyleAttr(tag.color)}>${esc(tag.label)}</span>
        <span class="muted small">${tag.book_count} ${esc(T.settings_tags_book_count)}</span>
        <button type="button" class="btn btn-secondary" data-tag-edit="${tag.id}">${esc(T.settings_tags_edit)}</button>
        <button type="button" class="btn btn-danger" data-tag-delete="${tag.id}" data-tag-label="${esc(tag.label)}">${esc(T.settings_tags_delete)}</button>
      </li>`;
    })
    .join("")}</ul>`;
  wireRowActions();
}

function wireRowActions() {
  document.querySelectorAll("[data-tag-edit]").forEach((btn) => {
    btn.addEventListener("click", () => {
      editingTagId = Number(btn.dataset.tagEdit);
      renderTags();
    });
  });
  document.querySelectorAll("[data-tag-cancel]").forEach((btn) => {
    btn.addEventListener("click", () => {
      editingTagId = null;
      renderTags();
    });
  });
  document.querySelectorAll("[data-tag-save]").forEach((btn) => {
    btn.addEventListener("click", () => saveTag(Number(btn.dataset.tagSave)));
  });
  document.querySelectorAll("[data-tag-delete]").forEach((btn) => {
    btn.addEventListener("click", () => deleteTag(Number(btn.dataset.tagDelete), btn.dataset.tagLabel));
  });
}

async function loadTags() {
  const el = document.getElementById("tags-list");
  if (!el) return;
  try {
    const resp = await fetch("/api/v1/tags");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    tagsState = await resp.json();
    renderTags();
  } catch (err) {
    el.innerHTML = `<p class="muted">${esc(T.settings_tags_load_error)} (${esc(err.message)})</p>`;
  }
}

async function addTag() {
  const labelInput = document.getElementById("tags-new-label");
  const colorInput = document.getElementById("tags-new-color");
  const label = labelInput.value.trim();
  if (!label) return;
  try {
    const resp = await fetch("/api/v1/tags", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label, color: colorInput.value }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    labelInput.value = "";
    await loadTags();
    if (window.AudiarrToast) window.AudiarrToast.success(T.settings_tags_add_success);
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.settings_tags_add_error} (${err.message})`);
  }
}

async function saveTag(tagId) {
  const labelInput = document.querySelector(`[data-tag-edit-label="${tagId}"]`);
  const colorInput = document.querySelector(`[data-tag-edit-color="${tagId}"]`);
  const label = labelInput.value.trim();
  if (!label) return;
  try {
    const resp = await fetch(`/api/v1/tags/${tagId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label, color: colorInput.value }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    editingTagId = null;
    await loadTags();
    if (window.AudiarrToast) window.AudiarrToast.success(T.settings_tags_save_success);
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.settings_tags_save_error} (${err.message})`);
  }
}

async function deleteTag(tagId, label) {
  if (!window.confirm(`${T.settings_tags_delete_confirm}\n\n${label}`)) return;
  try {
    const resp = await fetch(`/api/v1/tags/${tagId}`, { method: "DELETE" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    await loadTags();
    if (window.AudiarrToast) window.AudiarrToast.success(T.settings_tags_delete_success);
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.settings_tags_delete_error} (${err.message})`);
  }
}

// The tags editor lives inside #settings-form (shared settings shell), so
// Enter in a text field would otherwise submit the unrelated settings-save
// form; intercept it here and drive the tag action instead.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  if (event.target.id === "tags-new-label") {
    event.preventDefault();
    addTag();
  } else if (event.target.matches("[data-tag-edit-label]")) {
    event.preventDefault();
    saveTag(Number(event.target.dataset.tagEditLabel));
  }
});

document.addEventListener("DOMContentLoaded", () => {
  if (!document.getElementById("tags-list")) return;
  loadTags();
  const addBtn = document.getElementById("tags-add-btn");
  if (addBtn) addBtn.addEventListener("click", addTag);
});

})();
