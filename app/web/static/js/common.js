// Shared UI helpers loaded on every page via base.html.
//
// Provides:
//   * window.AudiarrToast.{success,error,info}(message) — bottom-right toasts
//   * the top-bar EN/DE language switch, which edits the UI language through
//     the settings API (GET the full document, flip ui.language, PUT it back)
//     and reloads the page so the server re-renders in the new locale.
//   * the mobile off-canvas sidebar (hamburger toggle, backdrop, Escape key,
//     nav-link and viewport-resize auto-close).
//
// Vanilla JS, no dependencies. Wrapped in an IIFE to avoid leaking globals
// into the per-page scripts that share this document scope.
(function () {
  "use strict";

  const T = window.AUDIARR_I18N || {};
  const TOAST_TIMEOUT_MS = 5000;

  function toast(message, kind) {
    const container = document.getElementById("toast-container");
    if (!container || !message) return;

    const el = document.createElement("div");
    el.className = `toast toast-${kind || "info"}`;
    el.setAttribute("role", "status");
    el.textContent = String(message);
    container.appendChild(el);

    // Trigger the enter transition on the next frame.
    requestAnimationFrame(() => el.classList.add("toast-visible"));

    let removed = false;
    const dismiss = () => {
      if (removed) return;
      removed = true;
      el.classList.remove("toast-visible");
      setTimeout(() => el.remove(), 300);
    };

    el.addEventListener("click", dismiss);
    setTimeout(dismiss, TOAST_TIMEOUT_MS);
  }

  window.AudiarrToast = {
    success: (message) => toast(message, "success"),
    error: (message) => toast(message, "error"),
    info: (message) => toast(message, "info"),
  };

  async function switchLanguage(language) {
    try {
      const getResp = await fetch("/api/v1/settings");
      if (!getResp.ok) throw new Error(`HTTP ${getResp.status}`);
      const doc = await getResp.json();

      if ((doc.ui && doc.ui.language) === language) return;
      doc.ui.language = language;

      const putResp = await fetch("/api/v1/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(doc),
      });
      if (!putResp.ok) throw new Error(`HTTP ${putResp.status}`);

      window.location.reload();
    } catch (err) {
      window.AudiarrToast.error(
        `${T.language_switch_error || "Could not switch language"} (${err.message})`
      );
    }
  }

  // Mobile off-canvas sidebar: hamburger toggle + backdrop + drawer.
  //
  // The sidebar itself never leaves the DOM; on narrow viewports CSS slides
  // it off-canvas and this code toggles `body.sidebar-open` (which drives
  // the slide-in transform and the backdrop) plus the a11y attributes.
  const MOBILE_MEDIA_QUERY = "(max-width: 800px)";

  function setupSidebar() {
    const toggle = document.querySelector("[data-sidebar-toggle]");
    const sidebar = document.getElementById("app-sidebar");
    const backdrop = document.querySelector(".sidebar-backdrop");
    if (!toggle || !sidebar || !backdrop) return;

    const mobileQuery = window.matchMedia(MOBILE_MEDIA_QUERY);

    function isSidebarOpen() {
      return document.body.classList.contains("sidebar-open");
    }

    function openSidebar() {
      document.body.classList.add("sidebar-open");
      sidebar.style.setProperty("translate", "280px 0px", "important");
      toggle.setAttribute("aria-expanded", "true");
      sidebar.setAttribute("aria-hidden", "false");
      backdrop.hidden = false;
      backdrop.setAttribute("aria-hidden", "false");
    }

    function closeSidebar() {
      document.body.classList.remove("sidebar-open");
      sidebar.style.left = mobileQuery.matches ? "-280px" : "";
      sidebar.style.translate = "";
      toggle.setAttribute("aria-expanded", "false");
      sidebar.setAttribute("aria-hidden", mobileQuery.matches ? "true" : "false");
      backdrop.hidden = true;
      backdrop.setAttribute("aria-hidden", "true");
    }

    toggle.addEventListener("click", () => {
      if (isSidebarOpen()) {
        closeSidebar();
      } else {
        openSidebar();
      }
    });

    // Backdrop click and any element flagged data-sidebar-close (currently
    // just the backdrop, but kept generic for future close affordances).
    document.querySelectorAll("[data-sidebar-close]").forEach((el) => {
      el.addEventListener("click", closeSidebar);
    });

    // Clicking a nav link closes the drawer (no-op on desktop, where it's
    // already closed).
    sidebar.querySelectorAll("[data-nav-link]").forEach((link) => {
      link.addEventListener("click", closeSidebar);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && isSidebarOpen()) {
        closeSidebar();
        toggle.focus();
      }
    });

    // Growing past the mobile breakpoint (e.g. rotating a tablet, resizing
    // a desktop window) should always leave the drawer closed.
    mobileQuery.addEventListener("change", (event) => {
      if (!event.matches) closeSidebar();
    });

    closeSidebar();
  }

  // Global search overlay: the topbar "Suchen" button opens a modal that
  // queries the existing metadata-search endpoint as you type (debounced),
  // capped at 10 results. Results are informational only — the endpoint
  // does not return book ids, so rows are shown without links rather than
  // faking a detail-page URL.
  const GLOBAL_SEARCH_DEBOUNCE_MS = 300;
  const GLOBAL_SEARCH_RESULT_LIMIT = 10;

  // Small local escaper so common.js has no import on settings.js — keeps
  // this file dependency-free for every page that loads it.
  function escapeHtmlLocal(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c]));
  }

  function setupGlobalSearch() {
    const trigger = document.querySelector(".global-search-btn");
    const overlay = document.getElementById("global-search-overlay");
    const input = document.getElementById("global-search-input");
    const results = document.getElementById("global-search-results");
    if (!trigger || !overlay || !input || !results) return;

    let debounceTimer = null;

    function openOverlay() {
      overlay.hidden = false;
      input.value = "";
      results.innerHTML = "";
      requestAnimationFrame(() => input.focus());
    }

    function closeOverlay() {
      overlay.hidden = true;
      if (debounceTimer) clearTimeout(debounceTimer);
    }

    function renderResults(rows) {
      if (!rows.length) {
        results.innerHTML = `<p class="search-overlay-empty">${escapeHtmlLocal(
          T.toolbar_search_no_results || "No results"
        )}</p>`;
        return;
      }
      results.innerHTML = rows
        .slice(0, GLOBAL_SEARCH_RESULT_LIMIT)
        .map(
          (r) => `
          <div class="search-overlay-result">
            <div class="search-overlay-result-title">${escapeHtmlLocal(r.title)}</div>
            <div class="search-overlay-result-meta">${escapeHtmlLocal((r.authors || []).join(", ")) || "—"}</div>
          </div>`
        )
        .join("");
    }

    async function runSearch(query) {
      if (!query) {
        results.innerHTML = "";
        return;
      }
      try {
        const params = new URLSearchParams({ query, limit: String(GLOBAL_SEARCH_RESULT_LIMIT) });
        const resp = await fetch(`/api/v1/metadata/search?${params.toString()}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        renderResults(data.results || []);
      } catch (err) {
        if (window.AudiarrToast) {
          window.AudiarrToast.error(`${T.toolbar_search || "Search"} (${err.message})`);
        }
      }
    }

    trigger.addEventListener("click", openOverlay);

    // Backdrop click (anywhere outside the panel) closes the overlay.
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeOverlay();
    });

    // Debounce so we don't fire a request per keystroke.
    input.addEventListener("input", () => {
      if (debounceTimer) clearTimeout(debounceTimer);
      const query = input.value.trim();
      debounceTimer = setTimeout(() => runSearch(query), GLOBAL_SEARCH_DEBOUNCE_MS);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !overlay.hidden) closeOverlay();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".lang-btn[data-lang]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.classList.contains("active")) return;
        switchLanguage(btn.dataset.lang);
      });
    });

    setupSidebar();
    setupGlobalSearch();
  });
})();
