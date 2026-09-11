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

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".lang-btn[data-lang]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.classList.contains("active")) return;
        switchLanguage(btn.dataset.lang);
      });
    });

    setupSidebar();
  });
})();
