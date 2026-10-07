package com.example.studentdatacollector

import android.content.ContentValues
import android.content.Context
import android.os.Environment
import android.provider.MediaStore
import java.io.File
import java.time.Instant
import java.time.ZoneId

// Helpers to build CSV rows and format times
object Csv {
    fun esc(value: Any?): String {
        val s = value?.toString() ?: ""
        return if (s.contains(',') || s.contains('"') || s.contains('\n')) {
            "\"" + s.replace("\"", "\"\"") + "\""
        } else s
    }

    fun row(vararg values: Any?): String = values.joinToString(",") { esc(it) }

    // e.g. 2026-10-06T14:02:05.123+02:00 (works with Supabase timestamptz and pandas)
    fun time(ms: Long): String =
        Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()).toOffsetDateTime().toString()

    fun date(ms: Long): String =
        Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()).toLocalDate().toString()
}

// Files inside the app's private storage, filled in real time by PhoneListenerService
object LocalLog {
    private val lock = Any()

    fun append(context: Context, fileName: String, header: String, line: String) {
        synchronized(lock) {
            val file = File(context.filesDir, fileName)
            if (!file.exists()) file.writeText(header + "\n")
            file.appendText(line + "\n")
        }
    }

    fun read(context: Context, fileName: String, header: String): String {
        synchronized(lock) {
            val file = File(context.filesDir, fileName)
            return if (file.exists()) file.readText() else header + "\n"
        }
    }

    fun countRows(context: Context, fileName: String): Int {
        synchronized(lock) {
            val file = File(context.filesDir, fileName)
            return if (file.exists()) (file.readLines().size - 1).coerceAtLeast(0) else 0
        }
    }
}

// Saves a file to Downloads/StudentDataCollector using the official MediaStore API
object Exporter {
    fun saveToDownloads(context: Context, fileName: String, content: String): Boolean {
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, fileName)
            put(MediaStore.Downloads.MIME_TYPE, "text/csv")
            put(
                MediaStore.Downloads.RELATIVE_PATH,
                Environment.DIRECTORY_DOWNLOADS + "/StudentDataCollector"
            )
        }
        val uri = context.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: return false
        context.contentResolver.openOutputStream(uri)?.use { it.write(content.toByteArray()) }
            ?: return false
        return true
    }
}
