package com.example.studentdatacollector

import android.app.Notification
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import androidx.core.content.ContextCompat

// Runs in the background once Diana enables "Notification access".
// 1) Logs notifications (app, time, opened/dismissed) WITHOUT their content.
// 2) Logs a context snapshot every time the screen turns on or the phone is unlocked.
class PhoneListenerService : NotificationListenerService() {

    companion object {
        const val NOTIFICATIONS_FILE = "notifications.csv"
        const val NOTIFICATIONS_HEADER =
            "timestamp,event,package_name,app_name,notification_category,notification_id,removal_reason"
        const val CONTEXT_FILE = "context_snapshots.csv"
        const val CONTEXT_HEADER =
            "timestamp,trigger,battery_level,charging,headphones,ringer_mode,do_not_disturb,connection,power_save"
    }

    private val activeKeys = mutableSetOf<String>()
    private var receiverRegistered = false

    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val trigger = when (intent.action) {
                Intent.ACTION_SCREEN_ON -> "screen_on"
                Intent.ACTION_SCREEN_OFF -> "screen_off"
                Intent.ACTION_USER_PRESENT -> "unlock"
                else -> return
            }
            LocalLog.append(
                this@PhoneListenerService, CONTEXT_FILE, CONTEXT_HEADER,
                ContextReader.snapshotRow(this@PhoneListenerService, trigger)
            )

            // When the user locks/turns off the screen, the current app session finishes.
            // Trigger an immediate background sync to upload completed sessions.
            if (intent.action == Intent.ACTION_SCREEN_OFF) {
                SyncScheduler.syncNow(this@PhoneListenerService)
            }
        }
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        activeKeys.clear()
        try {
            activeNotifications?.forEach { activeKeys.add(it.key) }
        } catch (e: Exception) {
            // Ignore: the list is only used to tell new notifications from updates
        }
        if (!receiverRegistered) {
            val filter = IntentFilter().apply {
                addAction(Intent.ACTION_SCREEN_ON)
                addAction(Intent.ACTION_SCREEN_OFF)
                addAction(Intent.ACTION_USER_PRESENT)
            }
            ContextCompat.registerReceiver(
                this, screenReceiver, filter, ContextCompat.RECEIVER_EXPORTED
            )
            receiverRegistered = true
        }
    }

    override fun onListenerDisconnected() {
        unregisterScreenReceiver()
        super.onListenerDisconnected()
    }

    override fun onDestroy() {
        unregisterScreenReceiver()
        super.onDestroy()
    }

    private fun unregisterScreenReceiver() {
        if (receiverRegistered) {
            try {
                unregisterReceiver(screenReceiver)
            } catch (e: Exception) {
                // Already unregistered
            }
            receiverRegistered = false
        }
    }

    // Skip our own app, permanent notifications (music, navigation...) and group summaries
    private fun shouldIgnore(sbn: StatusBarNotification): Boolean {
        val n = sbn.notification ?: return true
        return sbn.packageName == packageName ||
                sbn.isOngoing ||
                (n.flags and Notification.FLAG_GROUP_SUMMARY) != 0
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val s = sbn ?: return
        if (shouldIgnore(s)) return
        val event = if (activeKeys.add(s.key)) "posted" else "updated"
        logNotification(event, s, "")
    }

    override fun onNotificationRemoved(
        sbn: StatusBarNotification?,
        rankingMap: RankingMap?,
        reason: Int
    ) {
        val s = sbn ?: return
        activeKeys.remove(s.key)
        if (shouldIgnore(s)) return
        logNotification("removed", s, reasonName(reason))
    }

    private fun reasonName(reason: Int): String = when (reason) {
        NotificationListenerService.REASON_CLICK -> "opened"
        NotificationListenerService.REASON_CANCEL -> "dismissed"
        NotificationListenerService.REASON_CANCEL_ALL -> "dismissed_all"
        NotificationListenerService.REASON_APP_CANCEL,
        NotificationListenerService.REASON_APP_CANCEL_ALL -> "removed_by_app"
        NotificationListenerService.REASON_TIMEOUT -> "timeout"
        else -> "other_$reason"
    }

    private fun logNotification(event: String, sbn: StatusBarNotification, reason: String) {
        LocalLog.append(
            this, NOTIFICATIONS_FILE, NOTIFICATIONS_HEADER,
            Csv.row(
                Csv.time(System.currentTimeMillis()),
                event,
                sbn.packageName,
                AppInfo.name(this, sbn.packageName),
                sbn.notification?.category ?: "",
                // Anonymous ID to link "posted" with "removed" (no content is stored)
                Integer.toHexString(sbn.key.hashCode()),
                reason
            )
        )
    }
}
