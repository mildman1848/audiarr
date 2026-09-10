// Dashboard interactivity: live conversion job list.
// Fetches /api/v1/conversion/jobs every 10s and renders a compact table
// (roadmap #6: conversion state visible in the UI).

function t(key, fallback) {
  return (window.AUDIARR_I18N && window.AUDIARR_I18N[key]) || fallback;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function loadConversionJobs() {
  const container = document.getElementById("conversion-jobs");
  if (!container) return;

  try {
    const resp = await fetch("/api/v1/conversion/jobs");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const jobs = await resp.json();

    if (!Array.isArray(jobs) || jobs.length === 0) {
      container.innerHTML = `<p class="muted" data-i18n="conversion_empty">${escapeHtml(
        t("conversion_empty", "No conversion jobs yet.")
      )}</p>`;
      return;
    }

    const rows = jobs
      .slice(0, 10)
      .map((j) => {
        const sourceName = String(j.source_path || "").split("/").pop();
        return `
        <tr>
          <td>#${escapeHtml(j.id)}</td>
          <td>${escapeHtml(j.book_id)}</td>
          <td title="${escapeHtml(j.source_path)}">${escapeHtml(sourceName)}</td>
          <td><span class="badge badge-${escapeHtml(j.status)}">${escapeHtml(j.status)}</span></td>
          <td>${escapeHtml(j.attempts)}</td>
          <td class="muted">${escapeHtml(j.error ? j.error.slice(0, 60) : "")}</td>
        </tr>`;
      })
      .join("");

    container.innerHTML = `
      <table class="table">
        <thead>
          <tr>
            <th>${escapeHtml(t("conversion_table_id", "ID"))}</th>
            <th>${escapeHtml(t("conversion_table_book", "Book"))}</th>
            <th>${escapeHtml(t("conversion_table_source", "Source"))}</th>
            <th>${escapeHtml(t("conversion_table_status", "Status"))}</th>
            <th>${escapeHtml(t("conversion_table_tries", "Tries"))}</th>
            <th>${escapeHtml(t("conversion_table_error", "Error"))}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (err) {
    const message = `${t("conversion_error", "Conversion status unavailable.")} (${err.message})`;
    container.innerHTML = `<p class="muted" data-i18n="conversion_error">${escapeHtml(message)}</p>`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  console.info("Audiarr UI loaded");
  loadConversionJobs();
  setInterval(loadConversionJobs, 10000);
});
