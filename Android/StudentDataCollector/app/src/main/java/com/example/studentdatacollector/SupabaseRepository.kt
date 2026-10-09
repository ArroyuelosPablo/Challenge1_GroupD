package com.example.studentdatacollector

import android.content.Context
import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.createSupabaseClient
import io.github.jan.supabase.postgrest.Postgrest
import io.github.jan.supabase.postgrest.postgrest
import kotlinx.serialization.Serializable

object SupabaseRepository {


    private const val SUPABASE_URL = "https://dwixegqotavqnwnafyyi.supabase.co"
    private const val SUPABASE_ANON_KEY = "sb_publishable_DuesiPp-aeolgkT77foLSg_MWYfVSPc"

    val client: SupabaseClient by lazy {
        createSupabaseClient(
            supabaseUrl = SUPABASE_URL,
            supabaseKey = SUPABASE_ANON_KEY
        ) {
            install(Postgrest)
        }
    }


    @Serializable
    data class UsageSessionRecord(
        val app_name: String,
        val package_name: String? = null,
        val start_time: String,
        val end_time: String,
        val duration_seconds: Int,
        val created_at: String
    )


    suspend fun insertUsageSession(record: UsageSessionRecord): Result<Unit> {
        return runCatching {
            client.postgrest["usage_sessions"].insert(record)
        }
    }


    suspend fun insertUsageSessions(records: List<UsageSessionRecord>): Result<Unit> {
        return runCatching {
            client.postgrest["usage_sessions"].insert(records)
        }
    }


    suspend fun insertFromAppSession(context: Context, session: Session): Result<Unit> {
        val appName = AppInfo.name(context, session.packageName)
        val durationSec = ((session.end - session.start) / 1000).toInt().coerceAtLeast(0)
        val nowMs = System.currentTimeMillis()

        val record = UsageSessionRecord(
            app_name = appName,
            package_name = session.packageName,
            start_time = Csv.time(session.start),
            end_time = Csv.time(session.end),
            duration_seconds = durationSec,
            created_at = Csv.time(nowMs)
        )

        return insertUsageSession(record)
    }
}
