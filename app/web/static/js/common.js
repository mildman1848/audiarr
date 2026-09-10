// Shared UI helpers loaded on every page via base.html.
//
// Provides:
//   * window.AudiarrToast.{success,error,info}(message) — bottom-right toasts
//   * the top-bar EN/DE language switch, which edits the UI language through
//     the settings API (GET the full document, flip ui.language, PUT it back)
//     and reloads the page so the server re-renders in the new locale.
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

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".lang-btn[data-lang]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.classList.contains("active")) return;
        switchLanguage(btn.dataset.lang);
      });
    });
  });
})();
