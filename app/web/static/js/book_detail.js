// Book detail page: fetches a single book plus its files and renders a
// Readarr/Lidarr-style detail view. Vanilla JS, standalone (no shared module
// import), follows the fetch + innerHTML render pattern used in library.js.

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

function formatDuration(seconds) {
  if (!seconds) return "—";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
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

function renderBook(book, files) {
  const container = document.getElementById("book-detail");

  const coverBlock = book.cover_url
    ? `<img src="${esc(book.cover_url)}" alt="${esc(T.book_detail_cover_alt)}" style="width:100%;height:100%;object-fit:cover;border-radius:4px;display:block;">`
    : `<div class="muted small" style="display:flex;align-items:center;justify-content:center;height:100%;text-align:center;padding:0.5rem;">${esc(T.library_no_cover)}</div>`;

  const authorsChips = (book.authors || []).map((a) => `<span class="badge">${esc(a)}</span>`).join(" ") || "—";
  const narratorsChips =
    (book.narrators || []).map((n) => `<span class="badge">${esc(n)}</span>`).join(" ") || "—";
  const seriesBadge = book.series
    ? `<span class="badge">${esc(book.series_position ? `${book.series} #${book.series_position}` : book.series)}</span>`
    : "—";

  const providerRows = (book.provider_ids || [])
    .map(
      (p) => `
      <tr>
        <td>${esc(p.provider)}</td>
        <td>${esc(p.provider_id)}</td>
        <td>${esc(p.locale || "—")}</td>
      </tr>`
    )
    .join("");

  const fileRows = files
    .map(
      (f) => `
      <tr>
        <td><code>${esc(f.path)}</code></td>
        <td>${esc(f.format || "—")}</td>
        <td>${esc(humanSize(f.size_bytes))}</td>
        <td>${esc(f.added_at || "—")}</td>
      </tr>`
    )
    .join("");

  container.innerHTML = `
    <div class="card">
      <div style="display:flex;gap:1.25rem;flex-wrap:wrap;">
        <div style="flex:0 0 180px;height:180px;background:var(--bg-panel-alt);border-radius:4px;overflow:hidden;">${coverBlock}</div>
        <div style="flex:1;min-width:260px;">
          <h2 style="margin:0 0 0.2rem;">${esc(book.title)}</h2>
          ${book.subtitle ? `<p class="muted" style="margin:0 0 0.6rem;">${esc(book.subtitle)}</p>` : ""}
          <p>${esc(T.book_detail_series_label)}: ${seriesBadge}</p>
          <p>${esc(T.book_detail_authors_label)}: ${authorsChips}</p>
          <p>${esc(T.book_detail_narrators_label)}: ${narratorsChips}</p>
          <ul class="plain-list">
            <li>${esc(T.book_detail_duration_label)}: ${esc(formatDuration(book.duration_seconds))}</li>
            <li>${esc(T.book_detail_language_label)}: ${esc(book.language || "—")}</li>
            <li>${esc(T.book_detail_publisher_label)}: ${esc(book.publisher || "—")}</li>
            <li>${esc(T.book_detail_release_date_label)}: ${esc(book.release_date || "—")}</li>
          </ul>
          <div class="button-row">
            <a href="/library" class="btn btn-secondary">${esc(T.book_detail_back)}</a>
            <button type="button" id="book-delete-btn" class="btn btn-danger">${esc(T.book_detail_delete)}</button>
          </div>
        </div>
      </div>
    </div>

    <section class="panel">
      <div class="section-head">
        <span class="eyebrow">${esc(T.book_detail_eyebrow)}</span>
        <h2>${esc(T.book_detail_provider_ids_heading)}</h2>
      </div>
      <div class="card">
        ${
          providerRows
            ? `<table class="table">
                 <thead>
                   <tr>
                     <th>${esc(T.book_detail_provider_col_provider)}</th>
                     <th>${esc(T.book_detail_provider_col_id)}</th>
                     <th>${esc(T.book_detail_provider_col_locale)}</th>
                   </tr>
                 </thead>
                 <tbody>${providerRows}</tbody>
               </table>`
            : `<p class="muted">${esc(T.book_detail_provider_ids_empty)}</p>`
        }
      </div>
    </section>

    <section class="panel">
      <div class="section-head">
        <span class="eyebrow">${esc(T.book_detail_eyebrow)}</span>
        <h2>${esc(T.book_detail_files_heading)}</h2>
      </div>
      <div class="card">
        ${
          fileRows
            ? `<table class="table">
                 <thead>
                   <tr>
                     <th>${esc(T.book_detail_files_col_path)}</th>
                     <th>${esc(T.book_detail_files_col_format)}</th>
                     <th>${esc(T.book_detail_files_col_size)}</th>
                     <th>${esc(T.book_detail_files_col_added_at)}</th>
                   </tr>
                 </thead>
                 <tbody>${fileRows}</tbody>
               </table>`
            : `<p class="muted">${esc(T.book_detail_files_empty)}</p>`
        }
      </div>
    </section>`;

  document.getElementById("book-delete-btn").addEventListener("click", () => deleteBook(book.id, book.title));
}

// Deletes the Audiarr DB record only; media files on disk are never touched.
async function deleteBook(bookId, title) {
  if (!window.confirm(`${T.book_detail_delete_confirm}\n\n${title}`)) return;
  try {
    const resp = await fetch(`/api/v1/library/books/${bookId}`, { method: "DELETE" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    if (window.AudiarrToast) window.AudiarrToast.success(T.book_detail_delete_success);
    window.location.href = "/library";
  } catch (err) {
    if (window.AudiarrToast) window.AudiarrToast.error(`${T.book_detail_delete_error} (${err.message})`);
  }
}

async function loadBook() {
  const container = document.getElementById("book-detail");
  const bookId = container.dataset.bookId;
  try {
    const [bookResp, filesResp] = await Promise.all([
      fetch(`/api/v1/library/books/${bookId}`),
      fetch(`/api/v1/library/books/${bookId}/files`),
    ]);
    if (bookResp.status === 404) {
      container.innerHTML = `<p class="muted">${esc(T.book_detail_not_found)}</p>`;
      return;
    }
    if (!bookResp.ok) throw new Error(`HTTP ${bookResp.status}`);
    const book = await bookResp.json();
    const files = filesResp.ok ? await filesResp.json() : [];
    renderBook(book, files);
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(T.book_detail_error)} (${esc(err.message)})</p>`;
  }
}

document.addEventListener("DOMContentLoaded", loadBook);
