(() => {
  "use strict";

  const CONFIG = window.APP_CONFIG || {};
  const PAGE_SIZE = 1000;
  const state = {
    currentView: "daily",
    requestNumber: 0,
    sessions: [],
    responses: [],
  };

  const elements = {
    connectionLabel: document.querySelector("#connection-label"),
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

  function reflectionText(summary, period) {
    if (!summary.responseCount) {
      return `There are no completed check-ins for ${period}. A quieter week is not a problem; it simply means there is less to compare.`;
    }
    if (!summary.mismatchCount) {
      return `In the completed check-ins for ${period}, your phone use matched your intention. What helped that alignment feel possible?`;
    }
    const topContext = getTop(summary.contexts);
    if (topContext) {
      return `In these check-ins, changes between plans and phone use were reported most often ${contextPhrase(topContext)}. What was happening in those moments, and how did the phone use fit in?`;
    }
    return `Some check-ins showed a change between your plan and phone use. What was happening in those moments, and how did the phone use fit in?`;
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
    elements.tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.view === name));
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
      <thead><tr><th>Time</th><th>Intended</th><th>Actual</th><th>Context</th><th>Reflection</th></tr></thead>
      <tbody>${responses.map((response) => {
        const linkedSession = response.session_id
          ? sessionById.get(response.session_id)
          : sessionByMergedId.get(response.merged_id);
        const time = linkedSession?.start_time
          ? new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" })
            .format(new Date(linkedSession.start_time))
          : "—";
        const matched = response.matched_intention === true;
        const status = matched ? "Aligned" : response.matched_intention === false ? "Changed" : "Not answered";
        const statusClass = matched ? "" : "mismatch";
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
    const topIntention = getTop(summary.intentions);
    const topActual = getTop(summary.actualActivities);
    const dayLabel = formatDate(date, { month: "long", day: "numeric" });
    const apps = summary.apps.slice(0, 7).map((app) => ({
      name: app.name,
      seconds: app.seconds,
    }));

    elements.dailyContent.innerHTML = `
      <div class="metric-grid">
        ${metricCard("Recorded app time", readableDuration(summary.totalSeconds), "From collected app sessions")}
        ${metricCard("Recorded sessions", String(summary.sessionCount), "Sessions in the selected day")}
        ${metricCard("Completed check-ins", String(summary.responseCount), "Responses linked to these sessions")}
        ${metricCard("Matched intention", formatRate(summary.matchRate), summary.responseCount ? `${summary.matchedCount} of ${summary.responseCount} check-ins` : "No completed check-ins")}
      </div>
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
      <article class="reflection-card">
        <p class="eyebrow">A gentle reflection</p>
        <p class="reflection-text">${escapeHtml(reflectionText(summary, dayLabel))}</p>
      </article>
      ${topIntention && topActual && summary.mismatchCount
        ? `<p class="small-observation">In the changed check-ins, <strong>${escapeHtml(displayName(topIntention))}</strong> was the most frequently postponed intention, while <strong>${escapeHtml(displayName(topActual))}</strong> was the most frequent reported activity.</p>`
        : ""}
    `;
  }

  function renderWeekly(weekStart, sessions, responses) {
    const weekEnd = addDays(weekStart, 6);
    elements.weeklyHeading.textContent =
      `${formatDate(weekStart, { month: "short", day: "numeric" })} – ${formatDate(weekEnd, { month: "short", day: "numeric", year: "numeric" })}`;
    const summary = summarize(sessions, responses);
    const intention = getTop(summary.intentions);
    const actual = getTop(summary.actualActivities);
    const period = `${formatDate(weekStart, { month: "long", day: "numeric" })}–${formatDate(weekEnd, { month: "long", day: "numeric" })}`;
    const daily = [];
    for (let index = 0; index < 7; index += 1) {
      const date = addDays(weekStart, index);
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

    const reflection = summary.mismatchCount
      ? `Across the completed check-ins, '${displayName(intention || "not recorded")}' was the most frequently postponed intention. '${displayName(actual || "not recorded")}' was the most frequent activity reported instead.`
      : "This week has no completed check-ins reporting a change between intention and activity.";
    const topContext = getTop(summary.contexts);
    const contextExplanation = topContext
      ? `The context mentioned most often in changed check-ins was ${contextPhrase(topContext)}.`
      : "There is no repeated mismatch context to highlight from this week's check-ins.";

    elements.weeklyContent.innerHTML = `
      <div class="metric-grid">
        ${metricCard("Recorded app time", readableDuration(summary.totalSeconds), "Sum of collected sessions for the week")}
        ${metricCard("Recorded sessions", String(summary.sessionCount), "Across the seven selected dates")}
        ${metricCard("Completed check-ins", String(summary.responseCount), "Responses linked to these sessions")}
        ${metricCard("Matched intention", formatRate(summary.matchRate), summary.responseCount ? `${summary.matchedCount} of ${summary.responseCount} check-ins` : "No completed check-ins")}
      </div>
      <article class="panel">
        <div class="panel-heading"><div><h3>Recorded app time, day by day</h3><p>Empty days mean no sessions were returned for that date.</p></div></div>
        ${chart}
      </article>
      <div class="two-column">
        <article class="panel">
          <div class="panel-heading"><div><h3>Where changes were reported</h3><p>Contexts reported when plans and phone activity did not match.</p></div></div>
          ${contextList(summary.contexts)}
        </article>
        <article class="panel">
          <div class="panel-heading"><div><h3>Most frequent app activity</h3><p>Collected app-session duration for ${escapeHtml(period)}.</p></div></div>
          ${barList(summary.apps.slice(0, 6), readableDuration, "No app sessions were recorded for this week.")}
        </article>
      </div>
      <article class="reflection-card">
        <p class="eyebrow">What the check-ins show</p>
        <p class="reflection-text">${escapeHtml(reflection)} ${escapeHtml(contextExplanation)}</p>
        <p class="weekly-question">${escapeHtml(reflectionText(summary, period))}</p>
      </article>
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
    const columns = "id,session_id,merged_id,intended_activity,actual_activity,context,matched_intention,completed";
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
    return [...byId.values()].sort((a, b) => {
      const aSession = sessions.find((session) => session.id === a.session_id)
        || sessions.find((session) => session.merged_id === a.merged_id);
      const bSession = sessions.find((session) => session.id === b.session_id)
        || sessions.find((session) => session.merged_id === b.merged_id);
      return Date.parse(aSession?.start_time || "") - Date.parse(bSession?.start_time || "");
    });
  }

  async function fetchPeriod(start, end) {
    const sessions = await fetchAllRows(
      "usage_sessions",
      "id,app_name,package_name,start_time,end_time,duration_seconds,merged_id,activity_date",
      { activity_date: `gte.${start}`, and: `(activity_date.lte.${end})` },
    );
    const responses = await fetchResponses(sessions);
    return { sessions, responses };
  }

  async function fetchLatestActivityDate() {
    const sessions = await fetchAllRows("usage_sessions", "activity_date", {
      order: "activity_date.desc",
      limit: "1",
    });
    return sessions[0]?.activity_date || localDateString(new Date());
  }

  async function loadCurrentSelection() {
    const requestNumber = ++state.requestNumber;
    const isWeekly = state.currentView === "weekly";
    const start = isWeekly ? elements.weekPicker.value : elements.dayPicker.value;
    const end = isWeekly ? addDays(start, 6) : start;
    const target = isWeekly ? elements.weeklyContent : elements.dailyContent;
    target.innerHTML = '<div class="loading-state">Gathering your check-ins and session details…</div>';

    try {
      const result = await fetchPeriod(start, end);
      if (requestNumber !== state.requestNumber) return;
      state.sessions = result.sessions;
      state.responses = result.responses;
      if (isWeekly) renderWeekly(start, result.sessions, result.responses);
      else renderDaily(start, result.sessions, result.responses);
    } catch (error) {
      if (requestNumber !== state.requestNumber) return;
      elements.connectionLabel.textContent = "Connection issue";
      target.innerHTML = renderEmpty(
        "Could not load this period",
        `${error.message} Check config.js and anonymous read access for usage_sessions and activity_responses.`,
      );
      showToast("The selected period could not be loaded.");
    }
  }

  elements.tabs.forEach((tab) => {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  });
  elements.dayPicker.addEventListener("change", loadCurrentSelection);
  elements.weekPicker.addEventListener("change", () => {
    elements.weekPicker.value = localDateString(mondayOf(dateFromInput(elements.weekPicker.value)));
    loadCurrentSelection();
  });
  document.querySelector("#previous-day").addEventListener("click", () => {
    elements.dayPicker.value = addDays(elements.dayPicker.value, -1);
    loadCurrentSelection();
  });
  document.querySelector("#next-day").addEventListener("click", () => {
    elements.dayPicker.value = addDays(elements.dayPicker.value, 1);
    loadCurrentSelection();
  });
  document.querySelector("#previous-week").addEventListener("click", () => {
    elements.weekPicker.value = addDays(elements.weekPicker.value, -7);
    loadCurrentSelection();
  });
  document.querySelector("#next-week").addEventListener("click", () => {
    elements.weekPicker.value = addDays(elements.weekPicker.value, 7);
    loadCurrentSelection();
  });

  async function initializeDashboard() {
    try {
      const latestDate = await fetchLatestActivityDate();
      elements.dayPicker.value = latestDate;
      elements.weekPicker.value = localDateString(mondayOf(dateFromInput(latestDate)));
      elements.connectionLabel.textContent = "Live public data";
      await loadCurrentSelection();
    } catch (error) {
      elements.connectionLabel.textContent = "Connection issue";
      const message = `${error.message} Check config.js and anonymous read access in Supabase.`;
      elements.dailyContent.innerHTML = renderEmpty("Could not connect to Supabase", message);
      elements.weeklyContent.innerHTML = renderEmpty("Could not connect to Supabase", message);
      showToast("Live data could not be loaded.");
    }
  }

  initializeDashboard();
})();
