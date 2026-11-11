# Daylight insights dashboard

A lightweight, public dashboard for exploring phone-use sessions and
intention check-ins by day or week. It uses plain HTML, CSS, and JavaScript,
so no Node.js or build step is needed.

## Public access warning

This dashboard has no login. Anyone who can reach the site can view its data.
The browser uses the Supabase publishable key from `config.js`; this key is
designed to be public and is not an authorization boundary. Never use a
`service_role` or secret key in this app.

Anonymous read access to `usage_sessions` and `activity_responses` was verified
for this Supabase project. That means people can also query the database API
directly; the dashboard's visible fields do not limit what the API may expose.
Only use this setup if public disclosure of the underlying records is intended.

## Local preview

From the repository root, run:

```sh
python3 -m http.server 4173 --directory Web/insights-dashboard
```

Then open <http://localhost:4173>. The dashboard loads real Supabase data
automatically and initially selects the latest date with recorded sessions on
or after the configured start date (`2026-11-06`).

## Cloudflare deployment

The dashboard is currently deployed at
<https://daylight-insights.pabloarroyuelos.workers.dev/> as a static Cloudflare
Worker. It was uploaded directly from the Cloudflare dashboard; no build step
or Wrangler CLI is needed. To publish later changes, upload the updated
`index.html`, `styles.css`, `app.js`, and `config.js` together as a new
deployment for the `daylight-insights` Worker.

For regular updates, the more reliable option is to connect the Worker to the
GitHub repository (or deploy it with Wrangler from a GitHub Action). That
avoids uploading only some files and makes each deployment correspond to a
specific commit. The current Worker is still manually deployed; automatic
deployment has not been configured. The old GitHub Pages workflow was removed
because it targeted a different host and Pages was not enabled.

## Supabase configuration

`config.js` contains this project's URL and publishable key. Both values are
intended to be included in a browser app. The app reads only from
`usage_sessions` and `activity_responses`; it does not write to Supabase or
read `weekly_stats`.

## Data shown

- **Day by day:** recorded app time, answered check-ins, the intention-match
  rate, reported mismatch contexts, and check-in details.
- **Weekly reflections:** the selected dates' app-time pattern, intention-match
  rate, displaced and actual activities, contexts, and a neutral reflection
  question.

`config.js` sets the earliest date the dashboard may query. The current value
is `2026-11-06`; the lower bound is applied to both the initial lookup and
every daily or weekly query. Date pickers and previous-period controls cannot
go earlier. A week that crosses the start date begins on 6 November, and a
week in progress ends today rather than showing future dates as empty data.

The dashboard selects the latest available `activity_date` on or after the
start date at startup. Responses are included only when their `session_id` or
`merged_id` belongs to the selected usage records. The weekly view is
calculated from source rows; it does not assume that `weekly_stats` already
contains a complete week. Intention-match percentages use only check-ins with
a recorded boolean `matched_intention`; they do not represent all phone use.

## Privacy note

This app handles sensitive, personal usage data and intentionally exposes it
without authentication. Deploy only over HTTPS, and share the site only if
anyone being able to inspect these records is acceptable. To make the data
private again, restore authentication and remove anonymous `SELECT` access in
Supabase before sharing or deploying.
