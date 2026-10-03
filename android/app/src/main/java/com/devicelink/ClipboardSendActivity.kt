package com.devicelink

import android.app.Activity

/**
 * Invisible activity launched from the notification action, the Quick Settings tile, or as a
 * fallback by [ClipCapture]. Android 10+ only allows clipboard reads once our window has focus,
 * so we read in onWindowFocusChanged.
 */
class ClipboardSendActivity : Activity() {
    companion object {
        /** Set when launched automatically after a detected copy: no toast, skip unchanged text. */
        const val EXTRA_AUTO = "auto"
    }

    private var done = false

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus || done) return
        done = true
        val auto = intent.getBooleanExtra(EXTRA_AUTO, false)
        val text = ClipCapture.readClipboard(this)
        if (text.isNullOrBlank()) {
            if (!auto) ClipSync.showToast(this, "Clipboard is empty")
        } else {
            ClipSync.sendClip(this, text, force = !auto, toast = !auto)
        }
        finish()
        @Suppress("DEPRECATION")
        overridePendingTransition(0, 0)
    }
}
