package com.example.studentdatacollector

// Supabase settings. Fill in URL and PUBLIC_KEY with the values from your teammate
// (Supabase → Project Settings → API). NEVER use the secret / service_role key here.
object SupabaseConfig {
    const val URL = "https://YOUR_PROJECT_ID.supabase.co"
    const val PUBLIC_KEY = "YOUR_PUBLISHABLE_OR_ANON_KEY"

    // Table and column names (change here if the database uses different names)
    const val TABLE = "usage_sessions"
    const val COL_APP_NAME = "app_name"
    const val COL_PACKAGE_NAME = "package_name"
    const val COL_START = "start_time"
    const val COL_END = "end_time"
    const val COL_DURATION = "duration_seconds"
    const val COL_DATE = "activity_date"

    // Needs a UNIQUE constraint on (package_name, start_time) in Supabase,
    // so repeated sessions are ignored. Set to "" to do plain inserts instead.
    const val ON_CONFLICT = "package_name,start_time"

    // How often the app uploads new sessions (minimum allowed by Android: 15 min)
    const val SYNC_INTERVAL_HOURS = 2L

    // On the very first sync, how far back to look
    const val FIRST_SYNC_LOOKBACK_HOURS = 24L

    val isConfigured: Boolean
        get() = !URL.contains("YOUR_") && !PUBLIC_KEY.contains("YOUR_")
}
