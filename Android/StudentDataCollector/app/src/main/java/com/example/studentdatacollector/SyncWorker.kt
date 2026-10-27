package com.example.studentdatacollector

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit

// Remembers what was already uploaded and the result of the last sync
object SyncState {
    private const val PREFS = "sync_state"
    private const val KEY_WATERMARK = "last_uploaded_start"
    private const val KEY_LAST_RUN = "last_run"
    private const val KEY_LAST_RESULT = "last_result"

    private fun prefs(c: Context) = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun watermark(c: Context): Long = prefs(c).getLong(KEY_WATERMARK, 0L)

    fun setWatermark(c: Context, value: Long) {
        prefs(c).edit().putLong(KEY_WATERMARK, value).apply()
    }

    fun record(c: Context, result: String) {
        prefs(c).edit()
            .putLong(KEY_LAST_RUN, System.currentTimeMillis())
            .putString(KEY_LAST_RESULT, result)
            .apply()
    }

    fun describe(c: Context): String {
        val p = prefs(c)
        val last = p.getLong(KEY_LAST_RUN, 0L)
        if (last == 0L) return "Never synced"
        val time = Csv.time(last).take(16).replace('T', ' ')
        return "Last sync: $time — ${p.getString(KEY_LAST_RESULT, "")}"
    }
}

// Schedules the automatic upload (every X hours) and the manual "Sync now"
object SyncScheduler {
    private const val PERIODIC_WORK = "usage_sync_periodic"
    private const val NOW_WORK = "usage_sync_now"

    private val constraints = Constraints.Builder()
        .setRequiredNetworkType(NetworkType.CONNECTED)
        .build()

    fun schedule(context: Context) {
        val request = PeriodicWorkRequestBuilder<SyncWorker>(
            SupabaseConfig.SYNC_INTERVAL_HOURS, TimeUnit.HOURS
        )
            .setConstraints(constraints)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 15, TimeUnit.MINUTES)
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            PERIODIC_WORK, ExistingPeriodicWorkPolicy.UPDATE, request
        )
    }

    fun syncNow(context: Context) {
        val request = OneTimeWorkRequestBuilder<SyncWorker>()
            .setConstraints(constraints)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            NOW_WORK, ExistingWorkPolicy.REPLACE, request
        )
    }
}

// Sync module: uploads new, finished sessions to Supabase (table usage_sessions)
class SyncWorker(context: Context, params: WorkerParameters) : Worker(context, params) {

    companion object {
        private const val MINUTE = 60_000L
        private const val HOUR = 60 * MINUTE
    }

    override fun doWork(): Result {
        val ctx = applicationContext

        if (!SupabaseConfig.isConfigured) {
            SyncState.record(ctx, "Supabase not configured yet")
            return Result.success()
        }

        return try {
            val now = System.currentTimeMillis()
            val watermark = SyncState.watermark(ctx)
            val from = if (watermark == 0L) {
                now - SupabaseConfig.FIRST_SYNC_LOOKBACK_HOURS * HOUR
            } else {
                watermark - HOUR
            }

            val data = UsageCollector.collect(ctx, from, now)

            // Only sessions that are finished and were not uploaded before
            val newSessions = data.sessions.filter { it.start > watermark && it.end < now - MINUTE }

            if (newSessions.isEmpty()) {
                SyncState.record(ctx, "No new sessions")
                return Result.success()
            }

            for (chunk in newSessions.chunked(500)) {
                val (code, error) = upload(ctx, chunk)
                if (code !in 200..299) {
                    SyncState.record(ctx, "Upload failed (HTTP $code) $error")
                    return Result.retry()
                }
                SyncState.setWatermark(ctx, chunk.maxOf { it.start })
            }

            SyncState.record(ctx, "Uploaded ${newSessions.size} sessions")
            Result.success()
        } catch (e: Exception) {
            SyncState.record(ctx, "Error: ${e.message}")
            Result.retry()
        }
    }

    private fun upload(ctx: Context, sessions: List<Session>): Pair<Int, String> {
        val body = JSONArray()
        for (s in sessions) {
            body.put(JSONObject().apply {
                put(SupabaseConfig.COL_APP_NAME, AppInfo.name(ctx, s.packageName))
                put(SupabaseConfig.COL_PACKAGE_NAME, s.packageName)
                put(SupabaseConfig.COL_START, Csv.time(s.start))
                put(SupabaseConfig.COL_END, Csv.time(s.end))
                put(SupabaseConfig.COL_DURATION, (s.end - s.start) / 1000)
                put(SupabaseConfig.COL_DATE, Csv.date(s.start))
            })
        }

        val query = if (SupabaseConfig.ON_CONFLICT.isNotEmpty()) {
            "?on_conflict=${SupabaseConfig.ON_CONFLICT}"
        } else ""
        val url = URL("${SupabaseConfig.URL.trimEnd('/')}/rest/v1/${SupabaseConfig.TABLE}$query")

        val conn = url.openConnection() as HttpURLConnection
        try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 15_000
            conn.readTimeout = 30_000
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("apikey", SupabaseConfig.PUBLIC_KEY)
            // Legacy anon keys are JWTs ("eyJ...") and also go in the Authorization header
            if (SupabaseConfig.PUBLIC_KEY.startsWith("eyJ")) {
                conn.setRequestProperty("Authorization", "Bearer ${SupabaseConfig.PUBLIC_KEY}")
            }
            val prefer = if (SupabaseConfig.ON_CONFLICT.isNotEmpty()) {
                "return=minimal,resolution=ignore-duplicates"
            } else "return=minimal"
            conn.setRequestProperty("Prefer", prefer)

            conn.outputStream.use { it.write(body.toString().toByteArray()) }

            val code = conn.responseCode
            val error = if (code in 200..299) "" else {
                conn.errorStream?.bufferedReader()?.use { it.readText() }?.take(200) ?: ""
            }
            return code to error
        } finally {
            conn.disconnect()
        }
    }
}
