package com.devicelink

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.ActivityManager
import android.app.NotificationManager
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView

class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var token: EditText
    private lateinit var chatId: EditText
    private lateinit var device: EditText
    private lateinit var status: TextView
    private lateinit var toggle: Button

    private val adbCommand get() = "adb shell pm grant $packageName android.permission.READ_LOGS"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = Prefs(this)
        token = findViewById(R.id.token)
        chatId = findViewById(R.id.chat_id)
        device = findViewById(R.id.device)
        status = findViewById(R.id.status)
        toggle = findViewById(R.id.toggle)

        token.setText(prefs.botToken)
        if (prefs.chatId != 0L) chatId.setText(prefs.chatId.toString())
        device.setText(prefs.deviceName)
        findViewById<TextView>(R.id.adb).text = adbCommand

        findViewById<Button>(R.id.save).setOnClickListener { saveAndTest() }
        findViewById<Button>(R.id.detect).setOnClickListener { detectChannel() }
        toggle.setOnClickListener { toggleSync() }
        findViewById<Button>(R.id.copy_adb).setOnClickListener {
            getSystemService(ClipboardManager::class.java)
                .setPrimaryClip(ClipData.newPlainText("adb", adbCommand))
            ClipSync.lastClip = adbCommand   // don't sync this to the laptop
            ClipSync.showToast(this, "Copied")
        }
        findViewById<Button>(R.id.overlay).setOnClickListener {
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        }
        findViewById<Button>(R.id.battery).setOnClickListener { requestBatteryExemption() }
        findViewById<Button>(R.id.accessibility).setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            ClipSync.showToast(this, "Open \"Device Link\" (maybe under Downloaded apps) and turn it on")
        }
        findViewById<Button>(R.id.notifications).setOnClickListener {
            startActivity(
                Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                    .putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
            )
        }

        if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    private fun save(): Boolean {
        val id = chatId.text.toString().trim().toLongOrNull()
        if (token.text.isBlank() || id == null) {
            status.text = "Enter the bot token and channel ID first."
            return false
        }
        prefs.botToken = token.text.toString()
        prefs.chatId = id
        prefs.deviceName = device.text.toString()
        return true
    }

    private fun saveAndTest() {
        if (!save()) return
        status.text = "Testing…"
        val api = TelegramApi(prefs.botToken)
        val chat = prefs.chatId
        val name = prefs.deviceName
        Thread {
            val result = try {
                val me = api.getMe()
                api.sendText(chat, "✅ Device Link connected ($name, @${me.optString("username")})")
                "Connected as @${me.optString("username")}. A test message was posted to the channel."
            } catch (e: Exception) {
                "Test failed: ${e.message}"
            }
            runOnUiThread {
                refreshStatus()
                status.text = result + "\n\n" + status.text
            }
            if (prefs.syncEnabled) runOnUiThread { SyncService.stop(this); SyncService.start(this) }
        }.start()
    }

    /** Waits for any post in the channel the phone bot administers and fills in its ID. */
    private fun detectChannel() {
        if (token.text.isBlank()) {
            status.text = "Enter the bot token first."
            return
        }
        if (isServiceRunning()) SyncService.stop(this)   // only one getUpdates consumer at a time
        status.text = "Post any message in your private channel now…"
        val api = TelegramApi(token.text.toString().trim())
        Thread {
            var found: Pair<Long, String>? = null
            var offset: Long? = null
            try {
                val deadline = System.currentTimeMillis() + 60_000
                while (found == null && System.currentTimeMillis() < deadline) {
                    val updates = api.getUpdates(offset, 20)
                    for (i in 0 until updates.length()) {
                        val upd = updates.getJSONObject(i)
                        offset = upd.getLong("update_id") + 1
                        val chat = upd.optJSONObject("channel_post")?.optJSONObject("chat") ?: continue
                        found = chat.getLong("id") to chat.optString("title")
                    }
                }
                if (offset != null) api.getUpdates(offset, 0)
            } catch (e: Exception) {
                runOnUiThread { status.text = "Detect failed: ${e.message}" }
                return@Thread
            }
            runOnUiThread {
                val f = found
                if (f == null) {
                    status.text = "No channel post seen. Is the bot an admin of the channel?"
                } else {
                    chatId.setText(f.first.toString())
                    status.text = "Found channel \"${f.second}\" (${f.first}). Tap Save & test."
                }
            }
        }.start()
    }

    private fun toggleSync() {
        if (isServiceRunning()) {
            prefs.syncEnabled = false
            SyncService.stop(this)
        } else {
            if (!save()) return
            prefs.syncEnabled = true
            SyncService.start(this)
        }
        toggle.postDelayed(::refreshStatus, 300)
    }

    @SuppressLint("BatteryLife")
    private fun requestBatteryExemption() {
        val pm = getSystemService(PowerManager::class.java)
        if (pm.isIgnoringBatteryOptimizations(packageName)) {
            ClipSync.showToast(this, "Already exempt")
        } else {
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
        }
    }

    @Suppress("DEPRECATION")
    private fun isServiceRunning(): Boolean =
        getSystemService(ActivityManager::class.java).getRunningServices(Int.MAX_VALUE)
            .any { it.service.className == SyncService::class.java.name }

    private fun refreshStatus() {
        val running = isServiceRunning()
        toggle.text = if (running) "Stop sync" else "Start sync"
        fun mark(ok: Boolean) = if (ok) "✅" else "❌"
        val notificationsOn = getSystemService(NotificationManager::class.java).areNotificationsEnabled()
        findViewById<Button>(R.id.notifications).visibility =
            if (notificationsOn) android.view.View.GONE else android.view.View.VISIBLE
        val lines = mutableListOf(
            "${mark(running)} Sync service ${if (running) "running" else "stopped"}",
            "${mark(prefs.isConfigured)} Configured",
            "${mark(notificationsOn)} Notifications allowed" +
                if (notificationsOn) "" else " (needed for the \"Send clipboard\" button)",
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val a11y = CopyDetectorService.isEnabled(this)
            findViewById<Button>(R.id.accessibility).visibility =
                if (a11y) android.view.View.GONE else android.view.View.VISIBLE
            lines += "${mark(a11y)} Automatic copy detection (Accessibility)"
            lines += "${mark(Settings.canDrawOverlays(this))} Display over other apps"
            if (!a11y) lines += "${mark(SyncService.hasReadLogs(this))} READ_LOGS granted (ADB alternative)"
            lines += if (SyncService.canAutoCapture(this)) {
                "→ Copies on this phone are sent automatically."
            } else {
                "→ Phone → laptop: use the \"Send clipboard\" notification button, Quick Settings tile, " +
                    "or share text to \"Send to laptop\". Laptop → phone is always automatic."
            }
        } else {
            lines += "→ Android ${Build.VERSION.RELEASE}: clipboard is synced automatically."
        }
        status.text = lines.joinToString("\n")
    }
}
