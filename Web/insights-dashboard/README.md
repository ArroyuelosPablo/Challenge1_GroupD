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
automatically and initially selects the latest date with recorded sessions.

## Publish with GitHub Pages

1. In the repository settings, open **Pages** and set the source to **GitHub
   Actions**.
2. Push changes to `main`. The workflow at
   `.github/workflows/deploy-dashboard.yml` publishes this folder automatically.
3. After the workflow succeeds, open the Pages URL shown in the workflow's
   `github-pages` environment. For this repository it should be
   `https://arroyuelospablo.github.io/Challenge1_GroupD/`.

## Supabase configuration

`config.js` contains this project's URL and publishable key. Both values are
intended to be included in a browser app. The app reads only from
`usage_sessions` and `activity_responses`; it does not write to Supabase or
read `weekly_stats`.

## Data shown

- **Day by day:** session duration, completed check-ins, intention match rate,
  app-use breakdown, mismatches by reported context, and response details.
- **Weekly reflections:** the seven daily totals, match rate, common displaced
  and actual activities, contexts, and a neutral reflection question.

The dashboard selects the latest available `activity_date` at startup. Day
and week filters use that column from `usage_sessions`. Responses are included
only when their `session_id` or `merged_id` belongs to the selected usage
records. The weekly view is calculated from source rows; it does not assume
that `weekly_stats` already contains a complete week.

## Privacy note

This app handles sensitive, personal usage data and intentionally exposes it
without authentication. Deploy only over HTTPS, and share the site only if
anyone being able to inspect these records is acceptable. To make the data
private again, restore authentication and remove anonymous `SELECT` access in
Supabase before sharing or deploying.
