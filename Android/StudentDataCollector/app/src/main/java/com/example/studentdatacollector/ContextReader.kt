package com.example.studentdatacollector

import android.app.NotificationManager
import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager

// Reads the phone's current context. None of these values need a runtime permission.
object ContextReader {

    fun snapshotRow(context: Context, trigger: String): String {
        val battery = context.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        val audio = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val notifications = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val connectivity = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val power = context.getSystemService(Context.POWER_SERVICE) as PowerManager

        // Battery
        val batteryLevel = battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        val charging = battery.isCharging

        // Headphones (wired or Bluetooth audio)
        val outputs = audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
        val wiredTypes = setOf(
            AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
            AudioDeviceInfo.TYPE_WIRED_HEADSET,
            AudioDeviceInfo.TYPE_USB_HEADSET
        )
        val bluetoothTypes = mutableSetOf(AudioDeviceInfo.TYPE_BLUETOOTH_A2DP)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            bluetoothTypes.add(AudioDeviceInfo.TYPE_BLE_HEADSET)
        }
        val headphones = when {
            outputs.any { it.type in wiredTypes } -> "wired"
            outputs.any { it.type in bluetoothTypes } -> "bluetooth"
            else -> "none"
        }

        // Sound mode and Do Not Disturb
        val ringerMode = when (audio.ringerMode) {
            AudioManager.RINGER_MODE_SILENT -> "silent"
            AudioManager.RINGER_MODE_VIBRATE -> "vibrate"
            else -> "normal"
        }
        val doNotDisturb =
            notifications.currentInterruptionFilter > NotificationManager.INTERRUPTION_FILTER_ALL

        // Connection type
        val caps = connectivity.getNetworkCapabilities(connectivity.activeNetwork)
        val connection = when {
            caps == null -> "none"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "mobile"
            else -> "other"
        }

        return Csv.row(
            Csv.time(System.currentTimeMillis()),
            trigger,
            batteryLevel,
            charging,
            headphones,
            ringerMode,
            doNotDisturb,
            connection,
            power.isPowerSaveMode
        )
    }
}

