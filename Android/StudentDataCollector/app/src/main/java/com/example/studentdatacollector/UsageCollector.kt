package com.example.studentdatacollector

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

data class Session(
    val packageName: String,
    val start: Long,
    val end: Long,
    val pickupId: Int,
    val firstInPickup: Boolean
)

data class DeviceEvent(val time: Long, val type: String)

data class RawEvent(val time: Long, val packageName: String, val className: String?, val type: String)

data class UsageData(
    val sessions: List<Session>,
    val deviceEvents: List<DeviceEvent>,
    val rawEvents: List<RawEvent>,
    val end: Long
)

data class DayStats(
    val date: String,
    var screenTimeMs: Long = 0,
    var screenOnCount: Int = 0,
    var unlocks: Int = 0,
    var sessions: Int = 0,
    var appTimeMs: Long = 0,
    var firstUse: Long? = null,
    var lastUse: Long? = null
)

// App name and category (cached)
object AppInfo {
    private val names = ConcurrentHashMap<String, String>()
    private val categories = ConcurrentHashMap<String, String>()

    fun name(context: Context, pkg: String): String = names.getOrPut(pkg) {
        try {
            val info = context.packageManager.getApplicationInfo(pkg, 0)
            context.packageManager.getApplicationLabel(info).toString()
        } catch (e: PackageManager.NameNotFoundException) {
            pkg
        }
    }

    fun category(context: Context, pkg: String): String = categories.getOrPut(pkg) {
        try {
            when (context.packageManager.getApplicationInfo(pkg, 0).category) {
                ApplicationInfo.CATEGORY_GAME -> "game"
                ApplicationInfo.CATEGORY_AUDIO -> "audio"
                ApplicationInfo.CATEGORY_VIDEO -> "video"
                ApplicationInfo.CATEGORY_IMAGE -> "image"
                ApplicationInfo.CATEGORY_SOCIAL -> "social"
                ApplicationInfo.CATEGORY_NEWS -> "news"
                ApplicationInfo.CATEGORY_MAPS -> "maps"
                ApplicationInfo.CATEGORY_PRODUCTIVITY -> "productivity"
                else -> "undefined"
            }
        } catch (e: PackageManager.NameNotFoundException) {
            "unknown"
        }
    }
}

// Reads UsageStatsManager events and turns them into sessions, device events and daily totals
object UsageCollector {

    // System screens that are not real "app use"
    private val IGNORED_PACKAGES = setOf("com.android.systemui")

    // The home screen app (launcher) is ignored too
    private fun launcherPackages(context: Context): Set<String> {
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
        return context.packageManager.queryIntentActivities(intent, 0)
            .map { it.activityInfo.packageName }
            .toSet()
    }

    fun collect(context: Context, start: Long, end: Long): UsageData {
        val usm = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val ignored = IGNORED_PACKAGES + launcherPackages(context) + context.packageName

        val sessions = mutableListOf<Session>()
        val deviceEvents = mutableListOf<DeviceEvent>()
        val rawEvents = mutableListOf<RawEvent>()

        var currentPackage: String? = null
        var currentStart = 0L
        var currentPickupId = 0
        var currentFirstInPickup = false
        var lastPause: Long? = null
        var pickupId = 0
        var pickupHasSession = false

        fun closeSession(at: Long) {
            val pkg = currentPackage
            if (pkg != null && pkg !in ignored && at > currentStart) {
                sessions.add(Session(pkg, currentStart, at, currentPickupId, currentFirstInPickup))
            }
            currentPackage = null
            lastPause = null
        }

        val events = usm.queryEvents(start, end)
        val event = UsageEvents.Event()
        while (events.hasNextEvent()) {
            events.getNextEvent(event)
            val time = event.timeStamp
            val pkg = event.packageName ?: ""
            rawEvents.add(RawEvent(time, pkg, event.className, eventName(event.eventType)))

            when (event.eventType) {
                UsageEvents.Event.ACTIVITY_RESUMED -> {
                    if (pkg == currentPackage) {
                        // Moving between screens of the same app: same session
                        lastPause = null
                    } else {
                        closeSession(lastPause ?: time)
                        currentPackage = pkg
                        currentStart = time
                        currentPickupId = pickupId
                        val counts = pkg !in ignored
                        currentFirstInPickup = counts && !pickupHasSession
                        if (counts) pickupHasSession = true
                    }
                }
                UsageEvents.Event.ACTIVITY_PAUSED -> {
                    if (pkg == currentPackage) lastPause = time
                }
                UsageEvents.Event.SCREEN_INTERACTIVE -> {
                    pickupId++
                    pickupHasSession = false
                    deviceEvents.add(DeviceEvent(time, "screen_on"))
                }
                UsageEvents.Event.SCREEN_NON_INTERACTIVE -> {
                    closeSession(lastPause ?: time)
                    deviceEvents.add(DeviceEvent(time, "screen_off"))
                }
                UsageEvents.Event.KEYGUARD_HIDDEN -> deviceEvents.add(DeviceEvent(time, "unlock"))
                UsageEvents.Event.KEYGUARD_SHOWN -> deviceEvents.add(DeviceEvent(time, "lock"))
                UsageEvents.Event.DEVICE_STARTUP -> deviceEvents.add(DeviceEvent(time, "device_startup"))
                UsageEvents.Event.DEVICE_SHUTDOWN -> {
                    closeSession(lastPause ?: time)
                    deviceEvents.add(DeviceEvent(time, "device_shutdown"))
                }
            }
        }
        closeSession(lastPause ?: end)

        return UsageData(sessions, deviceEvents, rawEvents, end)
    }

    fun dailyStats(data: UsageData): List<DayStats> {
        val days = sortedMapOf<String, DayStats>()
        fun day(time: Long): DayStats {
            val d = Csv.date(time)
            return days.getOrPut(d) { DayStats(d) }
        }

        var screenOnSince: Long? = null
        for (ev in data.deviceEvents) {
            when (ev.type) {
                "screen_on" -> {
                    day(ev.time).screenOnCount++
                    screenOnSince = ev.time
                }
                "screen_off", "device_shutdown" -> {
                    screenOnSince?.let { day(it).screenTimeMs += ev.time - it }
                    screenOnSince = null
                }
                "unlock" -> day(ev.time).unlocks++
            }
        }
        screenOnSince?.let { day(it).screenTimeMs += data.end - it }

        for (s in data.sessions) {
            val d = day(s.start)
            d.sessions++
            d.appTimeMs += s.end - s.start
            d.firstUse = minOf(d.firstUse ?: s.start, s.start)
            d.lastUse = maxOf(d.lastUse ?: s.end, s.end)
        }
        return days.values.toList()
    }

    // ---------- CSV builders ----------

    private fun minutes(ms: Long) = String.format(Locale.US, "%.1f", ms / 60000.0)

    fun sessionsCsv(context: Context, data: UsageData): String = buildString {
        appendLine("date,package_name,app_name,category,start_time,end_time,duration_sec,pickup_id,first_in_pickup")
        for (s in data.sessions) {
            appendLine(
                Csv.row(
                    Csv.date(s.start), s.packageName,
                    AppInfo.name(context, s.packageName),
                    AppInfo.category(context, s.packageName),
                    Csv.time(s.start), Csv.time(s.end),
                    (s.end - s.start) / 1000, s.pickupId, s.firstInPickup
                )
            )
        }
    }

    fun dailyCsv(data: UsageData): String = buildString {
        appendLine("date,screen_time_min,screen_on_count,unlocks,sessions,app_time_min,first_use,last_use")
        for (d in dailyStats(data)) {
            appendLine(
                Csv.row(
                    d.date, minutes(d.screenTimeMs), d.screenOnCount, d.unlocks,
                    d.sessions, minutes(d.appTimeMs),
                    d.firstUse?.let { Csv.time(it) } ?: "",
                    d.lastUse?.let { Csv.time(it) } ?: ""
                )
            )
        }
    }

    fun deviceEventsCsv(data: UsageData): String = buildString {
        appendLine("timestamp,event")
        for (e in data.deviceEvents) appendLine(Csv.row(Csv.time(e.time), e.type))
    }

    fun rawEventsCsv(data: UsageData): String = buildString {
        appendLine("timestamp,package_name,class_name,event_type")
        for (e in data.rawEvents) {
            appendLine(Csv.row(Csv.time(e.time), e.packageName, e.className ?: "", e.type))
        }
    }

    private fun eventName(type: Int): String = when (type) {
        UsageEvents.Event.ACTIVITY_RESUMED -> "ACTIVITY_RESUMED"
        UsageEvents.Event.ACTIVITY_PAUSED -> "ACTIVITY_PAUSED"
        UsageEvents.Event.ACTIVITY_STOPPED -> "ACTIVITY_STOPPED"
        UsageEvents.Event.CONFIGURATION_CHANGE -> "CONFIGURATION_CHANGE"
        UsageEvents.Event.USER_INTERACTION -> "USER_INTERACTION"
        UsageEvents.Event.SHORTCUT_INVOCATION -> "SHORTCUT_INVOCATION"
        UsageEvents.Event.STANDBY_BUCKET_CHANGED -> "STANDBY_BUCKET_CHANGED"
        UsageEvents.Event.SCREEN_INTERACTIVE -> "SCREEN_INTERACTIVE"
        UsageEvents.Event.SCREEN_NON_INTERACTIVE -> "SCREEN_NON_INTERACTIVE"
        UsageEvents.Event.KEYGUARD_SHOWN -> "KEYGUARD_SHOWN"
        UsageEvents.Event.KEYGUARD_HIDDEN -> "KEYGUARD_HIDDEN"
        UsageEvents.Event.FOREGROUND_SERVICE_START -> "FOREGROUND_SERVICE_START"
        UsageEvents.Event.FOREGROUND_SERVICE_STOP -> "FOREGROUND_SERVICE_STOP"
        UsageEvents.Event.DEVICE_SHUTDOWN -> "DEVICE_SHUTDOWN"
        UsageEvents.Event.DEVICE_STARTUP -> "DEVICE_STARTUP"
        else -> "OTHER_$type"
    }
}
