package com.devicelink

import android.accessibilityservice.AccessibilityService
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager

/**
 * Reads the clipboard from the background on Android 10+, where only the focused window may read
 * it: briefly add an invisible, focusable 1x1 overlay, read once it gains focus, then remove it.
 * If the overlay never gets focus, fall back to the invisible [ClipboardSendActivity].
 *
 * Triggered by [CopyDetectorService] (accessibility) or the logcat watcher in [SyncService].
 */
object ClipCapture {
    private const val TAG = "DeviceLink"
    private const val FOCUS_TIMEOUT_MS = 1000L
    private const val MIN_INTERVAL_MS = 700L

    private val main = Handler(Looper.getMainLooper())
    private var overlay: View? = null
    private var lastCapture = 0L

    /** Schedule a capture; [delayMs] lets the source app finish writing the clip first. */
    fun request(context: Context, delayMs: Long = 0) {
        main.postDelayed({ capture(context) }, delayMs)
    }

    fun readClipboard(context: Context): String? = try {
        context.getSystemService(ClipboardManager::class.java).primaryClip
            ?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(context)?.toString()
    } catch (e: SecurityException) {
        null
    }

    private fun capture(context: Context) {
        val now = SystemClock.elapsedRealtime()
        if (overlay != null || now - lastCapture < MIN_INTERVAL_MS) return
        lastCapture = now

        val type = when {
            Settings.canDrawOverlays(context) -> WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            context is AccessibilityService -> WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
            else -> {
                launchActivity(context)
                return
            }
        }
        val wm = context.getSystemService(WindowManager::class.java)
        var done = false
        val view = object : View(context) {
            override fun onWindowFocusChanged(hasWindowFocus: Boolean) {
                super.onWindowFocusChanged(hasWindowFocus)
                if (hasWindowFocus && !done) {
                    done = true
                    readClipboard(context)?.let { ClipSync.sendClip(context, it) }
                    remove(wm)
                }
            }
        }
        val params = WindowManager.LayoutParams(
            1, 1, type,
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_ALT_FOCUSABLE_IM or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        ).apply { gravity = Gravity.TOP or Gravity.START }
        try {
            wm.addView(view, params)
            overlay = view
        } catch (e: Exception) {
            Log.w(TAG, "Could not add capture overlay", e)
            launchActivity(context)
            return
        }
        main.postDelayed({
            if (!done) {
                done = true
                remove(wm)
                Log.i(TAG, "Overlay did not get focus; using activity fallback")
                launchActivity(context)
            }
        }, FOCUS_TIMEOUT_MS)
    }

    private fun remove(wm: WindowManager) {
        val view = overlay ?: return
        overlay = null
        try {
            wm.removeView(view)
        } catch (_: Exception) {
        }
    }

    private fun launchActivity(context: Context) {
        try {
            context.startActivity(
                Intent(context, ClipboardSendActivity::class.java)
                    .putExtra(ClipboardSendActivity.EXTRA_AUTO, true)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION)
            )
        } catch (e: Exception) {
            Log.w(TAG, "Could not launch clipboard activity", e)
        }
    }
}
