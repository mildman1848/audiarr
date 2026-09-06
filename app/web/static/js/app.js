// Dashboard interactivity: live conversion job list.
// Fetches /api/v1/conversion/jobs every 10s and renders a compact table
// (roadmap #6: conversion state visible in the UI).

async function loadConversionJobs() {
  const container = document.getElementById("conversion-jobs");
  if (!container) return;

  try {
    const resp = await fetch("/api/v1/conversion/jobs");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const jobs = await resp.json();

    if (!Array.isArray(jobs) || jobs.length === 0) {
      container.innerHTML = "<p class=\"muted\" data-i18n=\"conversion_empty\">No conversion jobs yet.</p>";
      return;
    }

    const rows = jobs
      .slice(0, 10)
      .map(
        (j) => `
        <tr>
          <td>#${j.id}</td>
          <td>${j.book_id}</td>
          <td title="${j.source_path}">${j.source_path.split("/").pop()}</td>
          <td><span class="badge badge-${j.status}">${j.status}</span></td>
          <td>${j.attempts}</td>
          <td class="muted">${j.error ? j.error.slice(0, 60) : ""}</td>
        </tr>`
      )
      .join("");

    container.innerHTML = `
      <table class="table">
        <thead>
          <tr><th>ID</th><th>Book</th><th>Source</th><th>Status</th><th>Tries</th><th>Error</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (err) {
    container.innerHTML = `<p class="muted" data-i18n="conversion_error">Conversion status unavailable (${err.message}).</p>`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  console.info("Audiarr UI loaded");
  loadConversionJobs();
  setInterval(loadConversionJobs, 10000);
});
