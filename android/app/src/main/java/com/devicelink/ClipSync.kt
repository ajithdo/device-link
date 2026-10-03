package com.devicelink

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.Toast
import java.util.concurrent.Executors

/** Process-wide state and send helpers shared by the service, share target, tile and activities. */
object ClipSync {
    private const val TAG = "DeviceLink"
    private val sender = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())

    /** Last clipboard text sent or received; never echoed back to the other device. */
    @Volatile
    var lastClip: String? = null

    fun api(context: Context): TelegramApi? {
        val prefs = Prefs(context)
        return if (prefs.isConfigured) TelegramApi(prefs.botToken) else null
    }

    /** Send local clipboard text. Skipped if unchanged, unless [force] (explicit user action). */
    fun sendClip(context: Context, text: String, force: Boolean = false, toast: Boolean = false) {
        if (text.isBlank()) return
        synchronized(this) {
            if (!force && text == lastClip) return
            lastClip = text
        }
        val app = context.applicationContext
        val prefs = Prefs(app)
        sendAsync(app, toast, "Clipboard sent", "Clipboard") { api ->
            if (text.length <= Protocol.MAX_MESSAGE_LEN) {
                api.sendText(prefs.chatId, Protocol.build(Protocol.KIND_CLIP, prefs.deviceName, text))
            } else {
                api.sendDocument(
                    prefs.chatId, "clipboard.txt", text.toByteArray(),
                    Protocol.header(Protocol.KIND_CLIP, prefs.deviceName),
                )
            }
        }
    }

    fun sendLinks(context: Context, urls: List<String>, onDone: (() -> Unit)? = null) {
        val app = context.applicationContext
        val prefs = Prefs(app)
        val label = if (urls.size == 1) "Link sent to laptop" else "${urls.size} links sent to laptop"
        sendAsync(app, true, label, "Link", onDone) { api ->
            api.sendText(prefs.chatId, Protocol.build(Protocol.KIND_LINK, prefs.deviceName, urls.joinToString("\n")))
        }
    }

    /** Write text received from the other device to the local clipboard. */
    fun applyIncoming(context: Context, text: String) {
        main.post {
            lastClip = text
            val cm = context.getSystemService(ClipboardManager::class.java)
            cm.setPrimaryClip(ClipData.newPlainText("Device Link", text))
            Log.i(TAG, "Clipboard updated from laptop (${text.length} chars)")
        }
    }

    private fun sendAsync(
        app: Context,
        toast: Boolean,
        success: String,
        what: String,
        onDone: (() -> Unit)? = null,
        block: (TelegramApi) -> Unit,
    ) {
        val api = api(app)
        if (api == null) {
            showToast(app, "Device Link is not configured")
            onDone?.let { main.post(it) }
            return
        }
        sender.execute {
            var error: Exception? = null
            for (attempt in 1..3) {
                try {
                    block(api)
                    error = null
                    break
                } catch (e: Exception) {
                    error = e
                    Log.w(TAG, "$what send failed (attempt $attempt)", e)
                    Thread.sleep(attempt * 2000L)
                }
            }
            if (error != null) {
                if (what == "Clipboard") lastClip = null
                showToast(app, "$what not sent: ${error.message}")
            } else if (toast) {
                showToast(app, success)
            }
            onDone?.let { main.post(it) }
        }
    }

    fun showToast(context: Context, text: String) {
        main.post { Toast.makeText(context.applicationContext, text, Toast.LENGTH_SHORT).show() }
    }
}
