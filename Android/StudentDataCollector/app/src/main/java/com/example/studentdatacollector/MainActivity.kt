package com.example.studentdatacollector

import android.app.AppOpsManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Process
import android.provider.Settings
import android.widget.Toast
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.app.NotificationManagerCompat
import androidx.lifecycle.lifecycleScope
import com.example.studentdatacollector.ui.theme.StudentDataCollectorTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

class MainActivity : ComponentActivity() {

    private var hasUsageAccess by mutableStateOf(false)
    private var hasNotificationAccess by mutableStateOf(false)
    private var todaySummary by mutableStateOf("")
    private var statusMessage by mutableStateOf("")
    private var busy by mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            StudentDataCollectorTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
                    ExplorerScreen(
                        hasUsageAccess = hasUsageAccess,
                        hasNotificationAccess = hasNotificationAccess,
                        todaySummary = todaySummary,
                        statusMessage = statusMessage,
                        busy = busy,
                        onGrantUsage = {
                            startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
                        },
                        onGrantNotifications = {
                            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                        },
                        onRefresh = { refreshToday() },
                        onExport = { exportAll() },
                        onTestSupabase = { testSupabase() },
                        modifier = Modifier.padding(innerPadding)
                    )
                }
            }
        }
    }

    // Runs when the app opens and when returning from Settings
    override fun onResume() {
        super.onResume()
        hasUsageAccess = checkUsageAccess()
        hasNotificationAccess =
            NotificationManagerCompat.getEnabledListenerPackages(this).contains(packageName)
        if (hasUsageAccess) refreshToday()
    }

    private fun checkUsageAccess(): Boolean {
        val appOps = getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = appOps.unsafeCheckOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS,
            Process.myUid(),
            packageName
        )
        return mode == AppOpsManager.MODE_ALLOWED
    }

    // Today's numbers, shown on screen (runs in the background to keep the app smooth)
    private fun refreshToday() {
        if (busy) return
        busy = true
        Thread {
            val ctx = this@MainActivity
            val text = try {
                val start = LocalDate.now().atStartOfDay(ZoneId.systemDefault()).toInstant().toEpochMilli()
                val data = UsageCollector.collect(ctx, start, System.currentTimeMillis())
                val today = UsageCollector.dailyStats(data).find { it.date == LocalDate.now().toString() }
                val notifications = LocalLog.countRows(ctx, PhoneListenerService.NOTIFICATIONS_FILE)
                val snapshots = LocalLog.countRows(ctx, PhoneListenerService.CONTEXT_FILE)
                buildString {
                    appendLine("Sessions: ${today?.sessions ?: 0}")
                    appendLine("App time: ${(today?.appTimeMs ?: 0) / 60000} min")
                    appendLine("Screen time: ${(today?.screenTimeMs ?: 0) / 60000} min")
                    appendLine("Screen turned on: ${today?.screenOnCount ?: 0} times")
                    appendLine("Unlocks: ${today?.unlocks ?: 0}")
                    appendLine("Notifications logged (total): $notifications")
                    append("Context snapshots (total): $snapshots")
                }
            } catch (e: Exception) {
                "Could not read data: ${e.message}"
            }
            runOnUiThread {
                todaySummary = text
                busy = false
            }
        }.start()
    }

    // Exports 6 CSV files with the last 7 days to Downloads/StudentDataCollector
    private fun exportAll() {
        if (busy) return
        busy = true
        statusMessage = "Exporting…"
        Thread {
            val ctx = this@MainActivity
            val message = try {
                val end = System.currentTimeMillis()
                val start = end - 7L * 24 * 60 * 60 * 1000
                val data = UsageCollector.collect(ctx, start, end)
                val stamp = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd_HH-mm"))

                val files = linkedMapOf(
                    "sessions_$stamp.csv" to UsageCollector.sessionsCsv(ctx, data),
                    "daily_summary_$stamp.csv" to UsageCollector.dailyCsv(data),
                    "device_events_$stamp.csv" to UsageCollector.deviceEventsCsv(data),
                    "raw_events_$stamp.csv" to UsageCollector.rawEventsCsv(data),
                    "notifications_$stamp.csv" to LocalLog.read(
                        ctx, PhoneListenerService.NOTIFICATIONS_FILE, PhoneListenerService.NOTIFICATIONS_HEADER
                    ),
                    "context_snapshots_$stamp.csv" to LocalLog.read(
                        ctx, PhoneListenerService.CONTEXT_FILE, PhoneListenerService.CONTEXT_HEADER
                    )
                )
                val saved = files.count { (name, content) -> Exporter.saveToDownloads(ctx, name, content) }
                "Saved $saved of ${files.size} files to Downloads/StudentDataCollector"
            } catch (e: Exception) {
                "Export failed: ${e.message}"
            }
            runOnUiThread {
                statusMessage = message
                busy = false
                Toast.makeText(ctx, message, Toast.LENGTH_LONG).show()
            }
        }.start()
    }

    private fun testSupabase() {
        if (busy) return
        busy = true
        statusMessage = "Database trial…"
        lifecycleScope.launch(Dispatchers.IO) {
            val testRecord = SupabaseRepository.UsageSessionRecord(
                app_name = "App Trial",
                package_name = "com.example.test",
                start_time = Csv.time(System.currentTimeMillis() - 60000),
                end_time = Csv.time(System.currentTimeMillis()),
                duration_seconds = 60,
                created_at = Csv.time(System.currentTimeMillis())
            )

            val result = SupabaseRepository.insertUsageSession(testRecord)

            runOnUiThread {
                busy = false
                result.onSuccess {
                    statusMessage = "✅ Supabase: Done"
                    Toast.makeText(this@MainActivity, "¡Done!", Toast.LENGTH_LONG).show()
                }.onFailure { error ->
                    Log.e("SupabaseTest", "Error inserting ", error)
                    statusMessage = "❌ Error Supabase: ${error.message}"
                    Toast.makeText(this@MainActivity, "Error: ${error.message}", Toast.LENGTH_LONG).show()
                }
            }
        }
    }
}

@Composable
fun ExplorerScreen(
    hasUsageAccess: Boolean,
    hasNotificationAccess: Boolean,
    todaySummary: String,
    statusMessage: String,
    busy: Boolean,
    onGrantUsage: () -> Unit,
    onGrantNotifications: () -> Unit,
    onRefresh: () -> Unit,
    onExport: () -> Unit,
    onTestSupabase: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text("Student Data Collector", style = MaterialTheme.typography.headlineSmall)
        Text("Data Explorer (Tier 1 + Tier 2)", style = MaterialTheme.typography.bodyMedium)

        PermissionRow("Usage access", hasUsageAccess, onGrantUsage)
        PermissionRow("Notification access", hasNotificationAccess, onGrantNotifications)
        if (!hasNotificationAccess) {
            Text(
                "Notification access is also needed to record the context " +
                        "(battery, headphones, sound mode…) when the screen turns on.",
                style = MaterialTheme.typography.bodySmall
            )
        }

        HorizontalDivider()

        Text("Today", style = MaterialTheme.typography.titleMedium)
        Text(if (todaySummary.isEmpty()) "No data yet" else todaySummary)

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = onRefresh, enabled = hasUsageAccess && !busy) {
                Text("Refresh")
            }
            Button(onClick = onExport, enabled = hasUsageAccess && !busy) {
                Text("Export (last 7 days)")
            }
        }

        Button(onClick = onTestSupabase, enabled = !busy) {
            Text("Send to Database")
        }

        if (busy) LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
        if (statusMessage.isNotEmpty()) Text(statusMessage)
    }
}

@Composable
fun PermissionRow(label: String, granted: Boolean, onGrant: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text("$label: ${if (granted) "✓" else "✗"}")
        if (!granted) Button(onClick = onGrant) { Text("Grant") }
    }
}

