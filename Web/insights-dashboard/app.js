(() => {
  "use strict";

  const CONFIG = window.APP_CONFIG || {};
  const PAGE_SIZE = 1000;
  const MIN_ACTIVITY_DATE = CONFIG.minimumActivityDate || "2026-11-06";
  const TODAY = localDateString(new Date());
  const state = {
    currentView: "daily",
    requestNumber: 0,
  };

  const elements = {
    tabs: [...document.querySelectorAll(".view-tab")],
    dailyView: document.querySelector("#daily-view"),
    weeklyView: document.querySelector("#weekly-view"),
    dailyHeading: document.querySelector("#daily-heading"),
    weeklyHeading: document.querySelector("#weekly-heading"),
    dayPicker: document.querySelector("#day-picker"),
    weekPicker: document.querySelector("#week-picker"),
    dailyContent: document.querySelector("#daily-content"),
    weeklyContent: document.querySelector("#weekly-content"),
    toast: document.querySelector("#toast"),
  };

  function localDateString(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function mondayOf(date) {
    const monday = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const offset = (monday.getDay() + 6) % 7;
    monday.setDate(monday.getDate() - offset);
    return monday;
  }

  function dateFromInput(value) {
    const [year, month, day] = value.split("-").map(Number);
    return new Date(year, month - 1, day);
  }

  function addDays(value, days) {
    const date = dateFromInput(value);
    date.setDate(date.getDate() + days);
    return localDateString(date);
  }

  function clampDate(value, minimum = MIN_ACTIVITY_DATE, maximum = TODAY) {
    if (!value || value < minimum) return minimum;
    return value > maximum ? maximum : value;
  }

  function weekStartFor(value) {
    const monday = localDateString(mondayOf(dateFromInput(value)));
    return monday < MIN_ACTIVITY_DATE ? MIN_ACTIVITY_DATE : monday;
  }

  function weekRangeFor(value) {
    const monday = localDateString(mondayOf(dateFromInput(value)));
    return {
      start: monday < MIN_ACTIVITY_DATE ? MIN_ACTIVITY_DATE : monday,
      end: clampDate(addDays(monday, 6)),
    };
  }

  function formatDate(value, options = { weekday: "long", month: "long", day: "numeric" }) {
    if (!value) return "Unknown date";
    return new Intl.DateTimeFormat("en-GB", options).format(dateFromInput(value));
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[character]);
  }

  function displayName(value) {
    return String(value ?? "not recorded")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function readableDuration(seconds) {
    const minutes = Math.round(Math.max(0, seconds) / 60);
    const hours = Math.floor(minutes / 60);
    const remainingMinutes = minutes % 60;
    if (hours === 0) return `${minutes} min`;
    if (remainingMinutes === 0) return `${hours} hr`;
    return `${hours} hr ${remainingMinutes} min`;
  }

  function formatRate(rate) {
    return rate === null ? "—" : `${(rate * 100).toFixed(1).replace(/\.0$/, "")}%`;
  }

  function getDuration(session) {
    const stored = Number(session.duration_seconds);
    if (Number.isFinite(stored) && stored > 0) return stored;
    const start = Date.parse(session.start_time);
    const end = Date.parse(session.end_time);
    return Number.isFinite(start) && Number.isFinite(end) && end > start
      ? (end - start) / 1000
      : 0;
  }

  function summarize(sessions, responses) {
    const validResponses = responses.filter(
      (response) => typeof response.matched_intention === "boolean",
    );
    const mismatches = validResponses.filter(
      (response) => response.matched_intention === false,
    );
    const matched = validResponses.filter(
      (response) => response.matched_intention === true,
    ).length;
    const contexts = countBy(mismatches, "context");
    const intentions = countBy(mismatches, "intended_activity");
    const actualActivities = countBy(mismatches, "actual_activity");
    const appSeconds = new Map();
    let totalSeconds = 0;

    for (const session of sessions) {
      const duration = getDuration(session);
      totalSeconds += duration;
      const name = session.app_name || session.package_name || "Unknown app";
      appSeconds.set(name, (appSeconds.get(name) || 0) + duration);
    }

    return {
      totalSeconds,
      sessionCount: sessions.length,
      responseCount: validResponses.length,
      mismatchCount: mismatches.length,
      matchedCount: matched,
      matchRate: validResponses.length ? matched / validResponses.length : null,
      contexts: sortedCounts(contexts),
      intentions: sortedCounts(intentions),
      actualActivities: sortedCounts(actualActivities),
      apps: [...appSeconds.entries()]
        .map(([name, seconds]) => ({ name, seconds }))
        .sort((a, b) => b.seconds - a.seconds || a.name.localeCompare(b.name)),
    };
  }

  function countBy(rows, key) {
    const result = new Map();
    for (const row of rows) {
      const value = row[key];
      if (typeof value !== "string" || !value.trim()) continue;
      const normalized = value.trim();
      result.set(normalized, (result.get(normalized) || 0) + 1);
    }
    return result;
  }

  function sortedCounts(counts) {
    return [...counts.entries()]
      .map(([value, count]) => ({ value, count }))
      .sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
  }

  function chunks(values, size) {
    const result = [];
    for (let index = 0; index < values.length; index += size) {
      result.push(values.slice(index, index + size));
    }
    return result;
  }

  function getTop(rows) {
    return rows.length ? rows[0].value : null;
  }

  function contextPhrase(context) {
    const phrases = {
      short_break: "during short breaks",
      before_bed: "before bed",
      waiting: "while waiting",
      after_study: "after studying",
      bored: "when you felt bored",
      avoiding_task: "when avoiding a task",
      notification: "after a notification",
      other: "in other situations",
    };
    return phrases[context] || `in the context “${displayName(context)}”`;
  }

  function observationText(summary) {
    if (!summary.responseCount) {
      return "There are no check-ins with a recorded intention match linked to this period yet, so there is not enough information to compare intentions with reported activities.";
    }
    const alignment = `${summary.matchedCount} of ${summary.responseCount} answered check-ins matched the stated intention (${formatRate(summary.matchRate)}).`;
    if (!summary.mismatchCount) {
      return `${alignment} No changes between intended and reported activities were recorded in these check-ins.`;
    }
    const topContext = getTop(summary.contexts);
    const intention = getTop(summary.intentions);
    const actual = getTop(summary.actualActivities);
    const context = topContext
      ? ` The most common context was ${contextPhrase(topContext)}.`
      : "";
    const activity = intention && actual
      ? ` '${displayName(intention)}' was the most frequently postponed intention, while '${displayName(actual)}' was the most common reported activity.`
      : "";
    return `${alignment} In ${summary.mismatchCount} check-ins, the reported activity differed from the intention.${context}${activity}`;
  }

  function reflectionQuestion(summary) {
    if (!summary.responseCount) {
      return "When more check-ins include an intention match, this view can help you reflect on how those moments fit into your day.";
    }
    if (!summary.mismatchCount) {
      return "What helped your phone use align with your intention in these moments?";
    }
    const topContext = getTop(summary.contexts);
    return topContext
      ? `What was happening ${contextPhrase(topContext)}, and how did the phone use fit into that moment?`
      : "What was happening in the moments where your intention and reported activity differed?";
  }

  function showToast(message) {
    elements.toast.textContent = message;
    elements.toast.classList.remove("hidden");
    window.clearTimeout(showToast.timeoutId);
    showToast.timeoutId = window.setTimeout(
      () => elements.toast.classList.add("hidden"),
      5000,
    );
  }

  function setView(name) {
    state.currentView = name;
    elements.tabs.forEach((tab) => {
      const selected = tab.dataset.view === name;
      tab.classList.toggle("active", selected);
      tab.setAttribute("aria-pressed", String(selected));
    });
    elements.dailyView.classList.toggle("hidden", name !== "daily");
    elements.weeklyView.classList.toggle("hidden", name !== "weekly");
    loadCurrentSelection();
  }

  function renderEmpty(title, description) {
    return `<div class="empty-state"><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(description)}</p></div></div>`;
  }

  function metricCard(label, value, detail) {
    return `<article class="metric-card"><p class="metric-label">${escapeHtml(label)}</p><p class="metric-value">${escapeHtml(value)}</p><p class="metric-detail">${escapeHtml(detail)}</p></article>`;
  }

  function barList(items, formatValue, emptyText, valueKey = "seconds") {
    if (!items.length) return renderEmpty("Nothing to compare yet", emptyText);
    const max = Math.max(...items.map((item) => item[valueKey]), 1);
    return `<div class="bar-list">${items.map((item) => {
      const width = Math.max(3, (item[valueKey] / max) * 100);
      return `<div class="bar-row">
        <span class="bar-label" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${width}%"></span></span>
        <span class="bar-value">${escapeHtml(formatValue(item[valueKey]))}</span>
      </div>`;
    }).join("")}</div>`;
  }

  function contextList(items) {
    if (!items.length) {
      return renderEmpty(
        "No mismatch contexts",
        "Contexts are shown only when a completed check-in reports a change between intention and phone use.",
      );
    }
    return `<div class="context-list">${items.map((item) => `
      <div class="context-item">
        <span class="context-name">${escapeHtml(displayName(item.value))}</span>
        <span class="context-count">${item.count} ${item.count === 1 ? "check-in" : "check-ins"}</span>
      </div>`).join("")}</div>`;
  }

  function responseTable(responses, sessions) {
    if (!responses.length) {
      return renderEmpty(
        "No completed check-ins",
        "There are no completed questionnaire responses linked to sessions in this period.",
      );
    }
    const sessionById = new Map(sessions.map((session) => [session.id, session]));
    const sessionByMergedId = new Map();
    for (const session of sessions) {
      if (session.merged_id && !sessionByMergedId.has(session.merged_id)) {
        sessionByMergedId.set(session.merged_id, session);
      }
    }
    return `<div class="response-table-wrap"><table class="response-table">
      <thead><tr><th>Time</th><th>Intended</th><th>Actual</th><th>Context</th><th>Match to plan</th></tr></thead>
      <tbody>${responses.map((response) => {
        const linkedSession = sessionById.get(response.session_id)
          || sessionByMergedId.get(response.merged_id);
        const timeValue = response.response_time || linkedSession?.start_time;
        const time = timeValue
          ? new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" })
            .format(new Date(timeValue))
          : "—";
        const matched = response.matched_intention === true;
        const status = matched ? "Matched" : response.matched_intention === false ? "Changed" : "Not recorded";
        const statusClass = response.matched_intention === false ? "mismatch" : "";
        return `<tr>
          <td>${escapeHtml(time)}</td>
          <td>${escapeHtml(displayName(response.intended_activity))}</td>
          <td>${escapeHtml(displayName(response.actual_activity))}</td>
          <td>${escapeHtml(displayName(response.context))}</td>
          <td><span class="match-pill ${statusClass}">${status}</span></td>
        </tr>`;
      }).join("")}</tbody>
    </table></div>`;
  }

  function renderDaily(date, sessions, responses) {
    elements.dailyHeading.textContent = formatDate(date);
    const summary = summarize(sessions, responses);
    if (!sessions.length) {
      elements.dailyContent.innerHTML = renderEmpty(
        "No usage sessions for this day",
        "Choose another date. Days without recorded sessions are left blank rather than treated as zero phone use.",
      );
      return;
    }
    const dayLabel = formatDate(date, { month: "long", day: "numeric" });
    const apps = summary.apps.slice(0, 7).map((app) => ({
      name: app.name,
      seconds: app.seconds,
    }));

    elements.dailyContent.innerHTML = `
      <div class="metric-grid">
        ${metricCard("Recorded app time", readableDuration(summary.totalSeconds), "From collected app sessions")}
        ${metricCard("Answered check-ins", String(summary.responseCount), "Responses with a recorded intention match")}
        ${metricCard("Plans aligned", formatRate(summary.matchRate), summary.responseCount ? `${summary.matchedCount} of ${summary.responseCount} check-ins` : "No answered check-ins")}
        ${metricCard("Plans changed", String(summary.mismatchCount), summary.responseCount ? `Of ${summary.responseCount} answered check-ins` : "No answered check-ins")}
      </div>
      <article class="reflection-card">
        <p class="eyebrow">What the check-ins show</p>
        <p class="reflection-text">${escapeHtml(observationText(summary))}</p>
        <p class="weekly-question">${escapeHtml(reflectionQuestion(summary))}</p>
      </article>
      <div class="two-column">
        <article class="panel">
          <div class="panel-heading"><div><h3>App time</h3><p>Recorded use by app. This is context, not a target.</p></div></div>
          ${barList(apps, readableDuration, "No app sessions were recorded for this day.")}
        </article>
        <article class="panel">
          <div class="panel-heading"><div><h3>When plans changed</h3><p>Contexts from check-ins where intention and activity differed.</p></div></div>
          ${contextList(summary.contexts)}
        </article>
      </div>
      <article class="panel">
        <div class="panel-heading"><div><h3>Check-ins for ${escapeHtml(dayLabel)}</h3><p>Responses connected to the recorded sessions for this day.</p></div></div>
        ${responseTable(responses, sessions)}
      </article>
    `;
  }

  function renderWeekly(range, sessions, responses) {
    const { start: weekStart, end: weekEnd } = range;
    elements.weeklyHeading.textContent =
      `${formatDate(weekStart, { month: "short", day: "numeric" })} – ${formatDate(weekEnd, { month: "short", day: "numeric", year: "numeric" })}`;
    const summary = summarize(sessions, responses);
    const period = `${formatDate(weekStart, { month: "long", day: "numeric" })}–${formatDate(weekEnd, { month: "long", day: "numeric" })}`;
    const daily = [];
    for (let date = weekStart; date <= weekEnd; date = addDays(date, 1)) {
      const daySessions = sessions.filter((session) => session.activity_date === date);
      const sessionIds = new Set(daySessions.map((session) => session.id));
      const mergedIds = new Set(daySessions.map((session) => session.merged_id).filter(Boolean));
      const dayResponses = responses.filter(
        (response) => sessionIds.has(response.session_id) || mergedIds.has(response.merged_id),
      );
      daily.push({ date, ...summarize(daySessions, dayResponses) });
    }

    const maxSeconds = Math.max(...daily.map((day) => day.totalSeconds), 1);
    const chart = `<div class="chart">${daily.map((day) => {
      const hasData = day.sessionCount > 0;
      const height = hasData ? Math.max(5, (day.totalSeconds / maxSeconds) * 100) : 0;
      const weekday = formatDate(day.date, { weekday: "short" });
      return `<div class="chart-day" title="${escapeHtml(formatDate(day.date))}: ${hasData ? escapeHtml(readableDuration(day.totalSeconds)) : "No data"}">
        <span class="chart-value">${hasData ? escapeHtml(readableDuration(day.totalSeconds)) : "·"}</span>
        <span class="chart-track"><span class="chart-bar ${hasData ? "" : "no-data"}" style="height:${height}%"></span></span>
        <span class="chart-label">${escapeHtml(weekday)}</span>
      </div>`;
    }).join("")}</div>`;

    elements.weeklyContent.innerHTML = `
      <div class="metric-grid">
        ${metricCard("Recorded app time", readableDuration(summary.totalSeconds), "Collected app sessions in this period")}
        ${metricCard("Answered check-ins", String(summary.responseCount), "Responses with a recorded intention match")}
        ${metricCard("Plans aligned", formatRate(summary.matchRate), summary.responseCount ? `${summary.matchedCount} of ${summary.responseCount} check-ins` : "No answered check-ins")}
        ${metricCard("Plans changed", String(summary.mismatchCount), summary.responseCount ? `Of ${summary.responseCount} answered check-ins` : "No answered check-ins")}
      </div>
      <article class="reflection-card">
        <p class="eyebrow">What the check-ins show</p>
        <p class="reflection-text">${escapeHtml(observationText(summary))}</p>
        <p class="weekly-question">${escapeHtml(reflectionQuestion(summary))}</p>
      </article>
      <article class="panel">
        <div class="panel-heading"><div><h3>Recorded app time by day</h3><p>Only dates in this period are shown; days without bars had no recorded sessions.</p></div></div>
        ${chart}
      </article>
      <div class="two-column">
        <article class="panel">
          <div class="panel-heading"><div><h3>Where changes were reported</h3><p>Contexts reported when plans and phone activity did not match.</p></div></div>
          ${contextList(summary.contexts)}
        </article>
        <article class="panel">
          <div class="panel-heading"><div><h3>Apps by recorded time</h3><p>Collected app-session duration for ${escapeHtml(period)}.</p></div></div>
          ${barList(summary.apps.slice(0, 6), readableDuration, "No app sessions were recorded for this week.")}
        </article>
      </div>
      <article class="panel">
        <div class="panel-heading"><div><h3>Check-ins this week</h3><p>Detailed responses linked to the selected week's sessions.</p></div></div>
        ${responseTable(responses, sessions)}
      </article>
    `;
  }

  async function fetchAllRows(table, columns, params) {
    if (
      !CONFIG.supabaseUrl
      || !CONFIG.supabasePublishableKey
      || CONFIG.supabaseUrl.includes("YOUR_PROJECT")
      || CONFIG.supabasePublishableKey.includes("YOUR_")
    ) {
      throw new Error("The public Supabase URL or publishable key is missing from config.js.");
    }
    const rows = [];
    let offset = 0;
    while (true) {
      const url = new URL(`${CONFIG.supabaseUrl.replace(/\/+$/, "")}/rest/v1/${table}`);
      url.search = new URLSearchParams({ select: columns, ...params }).toString();
      const response = await fetch(url, {
        headers: {
          apikey: CONFIG.supabasePublishableKey,
          Range: `${offset}-${offset + PAGE_SIZE - 1}`,
        },
      });
      if (!response.ok) {
        throw new Error(await readApiError(response));
      }
      const batch = await response.json();
      rows.push(...batch);
      if (batch.length < PAGE_SIZE) return rows;
      offset += PAGE_SIZE;
    }
  }

  async function readApiError(response) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      detail = body.message || body.msg || body.error_description || body.error || detail;
    } catch {
      // Keep the HTTP status when the server response is not JSON.
    }
    if (response.status === 401 || response.status === 403) {
      return `${detail}. Check that anonymous users have read access to this table in Supabase.`;
    }
    return detail;
  }

  async function fetchResponses(sessions) {
    const sessionIds = [...new Set(sessions.map((session) => session.id).filter(Boolean))];
    const mergedIds = [...new Set(sessions.map((session) => session.merged_id).filter(Boolean))];
    const columns = "id,session_id,merged_id,intended_activity,actual_activity,context,matched_intention,completed,response_time";
    const requests = [];
    for (const ids of chunks(sessionIds, 100)) {
      requests.push(fetchAllRows("activity_responses", columns, {
        completed: "eq.true",
        session_id: `in.(${ids.join(",")})`,
      }));
    }
    for (const ids of chunks(mergedIds, 100)) {
      requests.push(fetchAllRows("activity_responses", columns, {
        completed: "eq.true",
        merged_id: `in.(${ids.join(",")})`,
      }));
    }
    const batches = await Promise.all(requests);
    const byId = new Map();
    for (const response of batches.flat()) {
      byId.set(response.id, response);
    }
    const sessionById = new Map(sessions.map((session) => [session.id, session]));
    const sessionByMergedId = new Map();
    for (const session of sessions) {
      if (session.merged_id && !sessionByMergedId.has(session.merged_id)) {
        sessionByMergedId.set(session.merged_id, session);
      }
    }
    const getSession = (response) => sessionById.get(response.session_id)
      || sessionByMergedId.get(response.merged_id);
    return [...byId.values()].sort((a, b) => {
      const aTime = Date.parse(a.response_time || getSession(a)?.start_time || "");
      const bTime = Date.parse(b.response_time || getSession(b)?.start_time || "");
      return (Number.isNaN(aTime) ? 0 : aTime) - (Number.isNaN(bTime) ? 0 : bTime);
    });
  }

  async function fetchPeriod(start, end) {
    if (end < MIN_ACTIVITY_DATE) return { sessions: [], responses: [] };
    const boundedStart = start < MIN_ACTIVITY_DATE ? MIN_ACTIVITY_DATE : start;
    const boundedEnd = end > TODAY ? TODAY : end;
    if (boundedStart > boundedEnd) return { sessions: [], responses: [] };
    const sessions = await fetchAllRows(
      "usage_sessions",
      "id,app_name,package_name,start_time,end_time,duration_seconds,merged_id,activity_date",
      {
        activity_date: `gte.${boundedStart}`,
        and: `(activity_date.lte.${boundedEnd})`,
        order: "activity_date.asc,start_time.asc,id.asc",
      },
    );
    const responses = await fetchResponses(sessions);
    return { sessions, responses };
  }

  async function fetchLatestActivityDate() {
    const sessions = await fetchAllRows("usage_sessions", "activity_date", {
      activity_date: `gte.${MIN_ACTIVITY_DATE}`,
      and: `(activity_date.lte.${TODAY})`,
      order: "activity_date.desc",
      limit: "1",
    });
    return sessions[0]?.activity_date || MIN_ACTIVITY_DATE;
  }

  async function loadCurrentSelection() {
    const requestNumber = ++state.requestNumber;
    const isWeekly = state.currentView === "weekly";
    const selectedDate = isWeekly
      ? clampDate(elements.weekPicker.value)
      : clampDate(elements.dayPicker.value);
    if (isWeekly) elements.weekPicker.value = weekStartFor(selectedDate);
    else elements.dayPicker.value = selectedDate;
    const range = isWeekly ? weekRangeFor(selectedDate) : { start: selectedDate, end: selectedDate };
    const { start, end } = range;
    const target = isWeekly ? elements.weeklyContent : elements.dailyContent;
    updateNavigation();
    target.innerHTML = '<div class="loading-state">Gathering your check-ins and session details…</div>';

    try {
      const result = await fetchPeriod(start, end);
      if (requestNumber !== state.requestNumber) return;
      if (isWeekly) renderWeekly(range, result.sessions, result.responses);
      else renderDaily(start, result.sessions, result.responses);
    } catch (error) {
      if (requestNumber !== state.requestNumber) return;
      target.innerHTML = renderEmpty(
        "Could not load this period",
        `${error.message} Check config.js and anonymous read access for usage_sessions and activity_responses.`,
      );
      showToast("The selected period could not be loaded.");
    }
  }

  function updateNavigation() {
    const previousDay = document.querySelector("#previous-day");
    const nextDay = document.querySelector("#next-day");
    const previousWeek = document.querySelector("#previous-week");
    const nextWeek = document.querySelector("#next-week");
    previousDay.disabled = elements.dayPicker.value <= MIN_ACTIVITY_DATE;
    nextDay.disabled = elements.dayPicker.value >= TODAY;
    previousWeek.disabled = elements.weekPicker.value <= MIN_ACTIVITY_DATE;
    nextWeek.disabled = elements.weekPicker.value >= weekStartFor(TODAY);
  }

  elements.dayPicker.min = MIN_ACTIVITY_DATE;
  elements.dayPicker.max = TODAY;
  elements.weekPicker.min = MIN_ACTIVITY_DATE;
  elements.weekPicker.max = TODAY;

  elements.tabs.forEach((tab) => {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  });
  elements.dayPicker.addEventListener("change", () => {
    elements.dayPicker.value = clampDate(elements.dayPicker.value);
    loadCurrentSelection();
  });
  elements.weekPicker.addEventListener("change", () => {
    elements.weekPicker.value = weekStartFor(clampDate(elements.weekPicker.value));
    loadCurrentSelection();
  });
  document.querySelector("#previous-day").addEventListener("click", () => {
    elements.dayPicker.value = clampDate(addDays(elements.dayPicker.value, -1));
    loadCurrentSelection();
  });
  document.querySelector("#next-day").addEventListener("click", () => {
    elements.dayPicker.value = clampDate(addDays(elements.dayPicker.value, 1));
    loadCurrentSelection();
  });
  document.querySelector("#previous-week").addEventListener("click", () => {
    elements.weekPicker.value = weekStartFor(clampDate(addDays(elements.weekPicker.value, -7)));
    loadCurrentSelection();
  });
  document.querySelector("#next-week").addEventListener("click", () => {
    elements.weekPicker.value = weekStartFor(clampDate(addDays(elements.weekPicker.value, 7)));
    loadCurrentSelection();
  });

  async function initializeDashboard() {
    try {
      const latestDate = await fetchLatestActivityDate();
      elements.dayPicker.value = latestDate;
      elements.weekPicker.value = weekStartFor(latestDate);
      updateNavigation();
      await loadCurrentSelection();
    } catch (error) {
      const message = `${error.message} Check config.js and anonymous read access in Supabase.`;
      elements.dailyContent.innerHTML = renderEmpty("Could not connect to Supabase", message);
      elements.weeklyContent.innerHTML = renderEmpty("Could not connect to Supabase", message);
      showToast("Live data could not be loaded.");
    }
  }

  initializeDashboard();
})();
