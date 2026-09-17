// Calendar page: a Sonarr-style month view over /api/v1/calendar, plus an
// agenda list below it. Vanilla JS, follows the fetch + innerHTML render
// pattern used across the other pages (wanted.js, library.js).

const T = window.AUDIARR_I18N || {};

// The first-of-month currently shown; kept in sync with the ?month= URL
// param so the view is bookmarkable/shareable and survives a reload.
let currentMonth = new Date();
// Raw books fetched for the visible month's full grid range (may include a
// few days from the previous/next month that fill out the grid's weeks).
let currentBooks = [];
// ISO date of the day whose modal is open, or null; kept so a monitored
// toggle flip can re-render the modal body in place instead of closing it.
let openModalDayIso = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

function isoDate(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

function monthKeyFromDate(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}`;
}

function addMonths(date, delta) {
  return new Date(date.getFullYear(), date.getMonth() + delta, 1);
}

function currentLang() {
  return document.documentElement.lang || "en";
}

function formatLongDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  return new Intl.DateTimeFormat(currentLang(), {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(date);
}

// Mon-Sun labels, localized via Intl rather than duplicated i18n keys.
function weekdayLabels() {
  const formatter = new Intl.DateTimeFormat(currentLang(), { weekday: "short" });
  const monday = new Date(2021, 0, 4); // a known Monday
  const labels = [];
  for (let i = 0; i < 7; i += 1) {
    const d = new Date(monday);
    d.setDate(monday.getDate() + i);
    labels.push(formatter.format(d));
  }
  return labels;
}

// Full Mon-Sun weeks covering the given month (may include leading/trailing
// days from the adjacent months so the grid has no partial weeks).
function computeGridDays(monthDate) {
  const year = monthDate.getFullYear();
  const month = monthDate.getMonth();
  const firstWeekday = (new Date(year, month, 1).getDay() + 6) % 7; // Mon=0
  const gridStart = new Date(year, month, 1 - firstWeekday);
  const lastWeekday = (new Date(year, month + 1, 0).getDay() + 6) % 7;
  const gridEnd = new Date(year, month + 1, 0 + (6 - lastWeekday));

  const days = [];
  const cursor = new Date(gridStart);
  while (cursor <= gridEnd) {
    days.push(new Date(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return days;
}

function booksByDate() {
  const map = {};
  for (const book of currentBooks) {
    if (!book.release_date) continue;
    if (!map[book.release_date]) map[book.release_date] = [];
    map[book.release_date].push(book);
  }
  return map;
}

// -- rendering: month grid ------------------------------------------------

function thumbHtml(book) {
  const cls = book.monitored ? "calendar-thumb" : "calendar-thumb is-unmonitored";
  const title = esc(book.title);
  const inner = book.cover_url ? `<img src="${esc(book.cover_url)}" alt="">` : "";
  return `<div class="${cls}" title="${title}">${inner}</div>`;
}

function renderGrid(days, grouped) {
  const container = document.getElementById("calendar-grid");
  const todayIso = isoDate(new Date());

  const headerHtml = weekdayLabels()
    .map((label) => `<div class="calendar-weekday">${esc(label)}</div>`)
    .join("");

  const cellsHtml = days
    .map((day) => {
      const iso = isoDate(day);
      const dayBooks = grouped[iso] || [];
      const classes = ["calendar-day"];
      if (day.getMonth() !== currentMonth.getMonth()) classes.push("is-other-month");
      if (iso === todayIso) classes.push("is-today");

      const thumbs = dayBooks.slice(0, 4).map(thumbHtml).join("");
      const overflowCount = dayBooks.length - 4;
      const overflow =
        overflowCount > 0
          ? `<div class="calendar-day-more">+${overflowCount} ${esc(T.calendar_day_more)}</div>`
          : "";

      return `
        <button type="button" class="${classes.join(" ")}" data-day="${iso}">
          <span class="calendar-day-number">${day.getDate()}</span>
          <div class="calendar-day-thumbs">${thumbs}</div>
          ${overflow}
        </button>`;
    })
    .join("");

  container.innerHTML = headerHtml + cellsHtml;
  container.querySelectorAll("[data-day]").forEach((el) => {
    el.addEventListener("click", () => openDayModal(el.dataset.day, grouped[el.dataset.day] || []));
  });
}

// -- rendering: agenda list -------------------------------------------------

function coverHtml(book) {
  if (book.cover_url) {
    return `<img src="${esc(book.cover_url)}" alt="${esc(T.library_cover_alt)}">`;
  }
  return `<div class="library-cover-placeholder">${esc(T.library_grid_cover_placeholder)}</div>`;
}

function monitoredToggleHtml(book) {
  return `
    <label class="calendar-agenda-monitored">
      <input type="checkbox" data-monitored-toggle="${book.id}" ${book.monitored ? "checked" : ""}>
      ${esc(T.calendar_monitored_label)}
    </label>`;
}

function agendaRowHtml(book) {
  const rowClass = book.monitored ? "calendar-agenda-row" : "calendar-agenda-row is-unmonitored";
  return `
    <div class="${rowClass}">
      <div class="calendar-agenda-cover">${coverHtml(book)}</div>
      <div class="calendar-agenda-body">
        <p class="calendar-agenda-title" title="${esc(book.title)}">${esc(book.title)}</p>
        <p class="calendar-agenda-authors">${esc((book.authors || []).join(", ")) || "—"}</p>
      </div>
      ${monitoredToggleHtml(book)}
    </div>`;
}

function renderAgenda(grouped) {
  const container = document.getElementById("calendar-agenda-list");
  const dates = Object.keys(grouped).sort();

  if (!dates.length) {
    container.innerHTML = window.AudiarrUI.emptyState({ icon: "▦", title: T.calendar_agenda_empty });
    return;
  }

  container.innerHTML = dates
    .map(
      (iso) => `
      <div class="calendar-agenda-day">
        <p class="calendar-agenda-date">${esc(formatLongDate(iso))}</p>
        ${grouped[iso].map(agendaRowHtml).join("")}
      </div>`
    )
    .join("");
  wireMonitoredToggles(container);
}

// -- day modal ----------------------------------------------------------------

function modalRowHtml(book) {
  return `
    <div class="calendar-modal-row">
      <div>
        <p class="calendar-agenda-title">${esc(book.title)}</p>
        <p class="calendar-agenda-authors">${esc((book.authors || []).join(", ")) || "—"}</p>
      </div>
      ${monitoredToggleHtml(book)}
    </div>`;
}

function renderModalBody(books) {
  const body = document.getElementById("calendar-day-modal-body");
  if (!books.length) {
    body.innerHTML = window.AudiarrUI.emptyState({ icon: "▦", title: T.calendar_agenda_empty });
    return;
  }
  body.innerHTML = books.map(modalRowHtml).join("");
  wireMonitoredToggles(body);
}

function openDayModal(iso, books) {
  openModalDayIso = iso;
  document.getElementById("calendar-day-modal-title").textContent = formatLongDate(iso);
  renderModalBody(books);
  document.getElementById("calendar-day-modal").hidden = false;
}

function closeDayModal() {
  openModalDayIso = null;
  document.getElementById("calendar-day-modal").hidden = true;
}

// -- monitored toggle -------------------------------------------------------

function wireMonitoredToggles(scope) {
  scope.querySelectorAll("[data-monitored-toggle]").forEach((input) => {
    input.addEventListener("change", () => toggleMonitored(input));
  });
}

// Reuses the existing library PATCH endpoint (added with wanted/missing
// monitoring) rather than a bespoke calendar-only route.
async function toggleMonitored(input) {
  const bookId = input.dataset.monitoredToggle;
  const monitored = input.checked;
  input.disabled = true;
  try {
    const resp = await fetch(`/api/v1/library/books/${bookId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ monitored }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const book = currentBooks.find((b) => String(b.id) === String(bookId));
    if (book) book.monitored = monitored;
    if (window.AudiarrToast) window.AudiarrToast.success(T.calendar_monitored_updated);
    renderAll();
  } catch (err) {
    input.checked = !monitored;
    if (window.AudiarrToast) {
      window.AudiarrToast.error(`${T.calendar_monitored_error} (${err.message})`);
    }
  } finally {
    input.disabled = false;
  }
}

// -- loading / error states ----------------------------------------------------

function showLoading() {
  const loadingHtml = `<p class="muted">${esc(T.calendar_loading)}</p>`;
  document.getElementById("calendar-grid").innerHTML = loadingHtml;
  document.getElementById("calendar-agenda-list").innerHTML = loadingHtml;
}

function renderError(err) {
  const retry = `<button type="button" class="btn btn-secondary" id="calendar-retry-btn">${esc(
    T.calendar_retry
  )}</button>`;
  document.getElementById("calendar-grid").innerHTML = window.AudiarrUI.emptyState({
    icon: "▦",
    title: `${T.calendar_load_error} (${err.message})`,
    actionHtml: retry,
  });
  document.getElementById("calendar-agenda-list").innerHTML = "";
  document.getElementById("calendar-retry-btn").addEventListener("click", loadMonth);
}

// -- render orchestration + data loading ---------------------------------------

function renderAll() {
  const days = computeGridDays(currentMonth);
  const grouped = booksByDate();
  renderGrid(days, grouped);
  renderAgenda(grouped);
  if (openModalDayIso) renderModalBody(grouped[openModalDayIso] || []);
}

async function loadMonth() {
  showLoading();
  try {
    const days = computeGridDays(currentMonth);
    const params = new URLSearchParams({
      start: isoDate(days[0]),
      end: isoDate(days[days.length - 1]),
    });
    const resp = await fetch(`/api/v1/calendar?${params.toString()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    currentBooks = await resp.json();
    renderAll();
  } catch (err) {
    currentBooks = [];
    renderError(err);
  }
}

// -- month navigation + URL state -----------------------------------------------

function updateMonthLabel() {
  const label = new Intl.DateTimeFormat(currentLang(), { month: "long", year: "numeric" }).format(
    currentMonth
  );
  document.getElementById("calendar-month-label").textContent = label;
}

function goToMonth(date, { push = true } = {}) {
  currentMonth = new Date(date.getFullYear(), date.getMonth(), 1);
  updateMonthLabel();

  const url = new URL(window.location.href);
  url.searchParams.set("month", monthKeyFromDate(currentMonth));
  if (push) {
    window.history.pushState({}, "", url);
  } else {
    window.history.replaceState({}, "", url);
  }
  loadMonth();
}

function initialMonth() {
  const raw = new URLSearchParams(window.location.search).get("month");
  if (raw && /^\d{4}-\d{2}$/.test(raw)) {
    const [year, month] = raw.split("-").map(Number);
    if (month >= 1 && month <= 12) return new Date(year, month - 1, 1);
  }
  const today = new Date();
  return new Date(today.getFullYear(), today.getMonth(), 1);
}

document.addEventListener("DOMContentLoaded", () => {
  // Normalizes the URL (adds ?month= on first visit) without an extra
  // history entry, then triggers the first fetch.
  goToMonth(initialMonth(), { push: false });

  document.getElementById("calendar-prev-btn").addEventListener("click", () => {
    goToMonth(addMonths(currentMonth, -1));
  });
  document.getElementById("calendar-next-btn").addEventListener("click", () => {
    goToMonth(addMonths(currentMonth, 1));
  });
  document.getElementById("calendar-today-btn").addEventListener("click", () => {
    const today = new Date();
    goToMonth(new Date(today.getFullYear(), today.getMonth(), 1));
  });

  document.getElementById("calendar-day-modal-close").addEventListener("click", closeDayModal);
  document.getElementById("calendar-day-modal").addEventListener("click", (event) => {
    if (event.target.id === "calendar-day-modal") closeDayModal();
  });

  window.addEventListener("popstate", () => goToMonth(initialMonth(), { push: false }));
});
