package com.devicelink

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import org.json.JSONObject

/**
 * Foreground service that
 *  1. long-polls the phone bot for "#clip" posts from the laptop and writes them to the clipboard;
 *  2. watches the local clipboard and posts changes as "#clip".
 *
 * Android 10+ only lets the focused app read the clipboard. When READ_LOGS has been granted via
 * ADB, we watch logcat for ClipboardService's "Denying clipboard access to <us>" line (emitted on
 * every copy because we hold a clipboard listener) and then briefly add an invisible, focusable
 * 1x1 overlay window so we are allowed to read the new clip.
 */
class SyncService : Service() {

    companion object {
        private const val TAG = "DeviceLink"
        private const val CHANNEL_ID = "sync"
        private const val NOTIFICATION_ID = 1
        private const val ACTION_STOP = "com.devicelink.STOP"

        fun start(context: Context) {
            context.startForegroundService(Intent(context, SyncService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, SyncService::class.java))
        }

        fun canAutoCapture(context: Context): Boolean =
            Build.VERSION.SDK_INT < Build.VERSION_CODES.Q ||
                (hasReadLogs(context) && Settings.canDrawOverlays(context))

        fun hasReadLogs(context: Context) =
            context.checkSelfPermission(Manifest.permission.READ_LOGS) == PackageManager.PERMISSION_GRANTED
    }

    @Volatile
    private var running = false
    private var pollThread: Thread? = null
    private var logcatProcess: Process? = null
    private val main = Handler(Looper.getMainLooper())
    private lateinit var clipboard: ClipboardManager
    private var overlay: View? = null

    private val clipListener = ClipboardManager.OnPrimaryClipChangedListener {
        // Fires directly on Android < 10, or on newer versions while our UI is focused.
        readClipboard()?.let { ClipSync.sendClip(this, it) }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        clipboard = getSystemService(ClipboardManager::class.java)
        startInForeground()
        running = true
        clipboard.addPrimaryClipChangedListener(clipListener)
        pollThread = Thread(::pollLoop, "telegram-poll").also { it.start() }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && hasReadLogs(this)) {
            Thread(::logcatLoop, "logcat-watch").start()
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            Prefs(this).syncEnabled = false
            stopSelf()
            return START_NOT_STICKY
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        clipboard.removePrimaryClipChangedListener(clipListener)
        logcatProcess?.destroy()
        pollThread?.interrupt()
        removeOverlay()
        super.onDestroy()
    }

    // ------------------------------------------------------------------ notification

    private fun startInForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Clipboard sync", NotificationManager.IMPORTANCE_LOW)
        )
        val flags = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), flags)
        val send = PendingIntent.getActivity(
            this, 1,
            Intent(this, ClipboardSendActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            flags,
        )
        val stop = PendingIntent.getService(
            this, 2, Intent(this, SyncService::class.java).setAction(ACTION_STOP), flags
        )
        val mode = if (canAutoCapture(this)) "Automatic clipboard sync" else "Laptop → phone sync active"
        val notification = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_link)
            .setContentTitle("Device Link")
            .setContentText(mode)
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Send clipboard", send).build())
            .addAction(Notification.Action.Builder(null, "Stop", stop).build())
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    // ------------------------------------------------------------------ laptop -> phone

    private fun pollLoop() {
        val prefs = Prefs(this)
        val api = TelegramApi(prefs.botToken)
        val me = Protocol.sanitizeDevice(prefs.deviceName)
        var offset: Long? = null
        var backoff = 1000L
        while (running) {
            try {
                val updates = api.getUpdates(offset)
                backoff = 1000L
                var latest: Pair<JSONObject, Protocol.Message>? = null
                for (i in 0 until updates.length()) {
                    val upd = updates.getJSONObject(i)
                    offset = upd.getLong("update_id") + 1
                    val post = upd.optJSONObject("channel_post") ?: continue
                    if (post.optJSONObject("chat")?.optLong("id") != prefs.chatId) continue
                    val text = post.optString("text").ifEmpty { post.optString("caption") }
                    val msg = Protocol.parse(text)
                    if (msg.kind == Protocol.KIND_CLIP && msg.source != me) latest = post to msg
                }
                latest?.let { (post, msg) -> applyClip(api, post, msg) }
            } catch (e: InterruptedException) {
                return
            } catch (e: Exception) {
                if (!running) return
                Log.w(TAG, "Polling failed, retrying in ${backoff}ms", e)
                try {
                    Thread.sleep(backoff)
                } catch (ie: InterruptedException) {
                    return
                }
                backoff = (backoff * 2).coerceAtMost(60_000L)
            }
        }
    }

    private fun applyClip(api: TelegramApi, post: JSONObject, msg: Protocol.Message) {
        val doc = post.optJSONObject("document")
        val text = if (doc != null) {
            if (doc.optLong("file_size") > Protocol.MAX_CLIP_BYTES) return
            String(api.downloadFile(doc.getString("file_id")))
        } else {
            msg.payload
        }
        ClipSync.applyIncoming(this, text)
    }

    // ------------------------------------------------------------------ phone -> laptop

    private fun readClipboard(): String? = try {
        clipboard.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString()
    } catch (e: SecurityException) {
        null
    }

    private fun logcatLoop() {
        val needle = "Denying clipboard access to $packageName"
        try {
            val process = Runtime.getRuntime().exec(arrayOf("logcat", "-T", "1", "-v", "brief", "ClipboardService:E", "*:S"))
            logcatProcess = process
            val started = SystemClock.elapsedRealtime()
            process.inputStream.bufferedReader().useLines { lines ->
                for (line in lines) {
                    if (!running) break
                    // Skip the replayed tail line from before we started.
                    if (SystemClock.elapsedRealtime() - started < 1500) continue
                    if (line.contains(needle)) main.post(::captureViaOverlay)
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "logcat watcher stopped", e)
        }
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun captureViaOverlay() {
        if (overlay != null) return
        if (!Settings.canDrawOverlays(this)) {
            Log.w(TAG, "Overlay permission missing; cannot capture clipboard automatically")
            return
        }
        val wm = getSystemService(WindowManager::class.java)
        val view = object : View(this) {
            override fun onWindowFocusChanged(hasWindowFocus: Boolean) {
                super.onWindowFocusChanged(hasWindowFocus)
                if (hasWindowFocus) {
                    readClipboard()?.let { ClipSync.sendClip(this@SyncService, it) }
                    removeOverlay()
                }
            }
        }
        val params = WindowManager.LayoutParams(
            1, 1,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_ALT_FOCUSABLE_IM or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        ).apply { gravity = Gravity.TOP or Gravity.START }
        try {
            wm.addView(view, params)
            overlay = view
            main.postDelayed(::removeOverlay, 1500)   // never keep focus longer than this
        } catch (e: Exception) {
            Log.w(TAG, "Could not add overlay", e)
        }
    }

    private fun removeOverlay() {
        val view = overlay ?: return
        overlay = null
        try {
            getSystemService(WindowManager::class.java).removeView(view)
        } catch (_: Exception) {
        }
    }
}
