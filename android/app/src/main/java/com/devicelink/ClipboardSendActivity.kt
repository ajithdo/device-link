package com.devicelink

import android.app.Activity
import android.content.ClipboardManager

/**
 * Invisible activity launched from the notification action or Quick Settings tile. Android 10+
 * only allows clipboard reads once our window has focus, so we read in onWindowFocusChanged.
 */
class ClipboardSendActivity : Activity() {
    private var done = false

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus || done) return
        done = true
        val clip = getSystemService(ClipboardManager::class.java).primaryClip
        val text = clip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString()
        if (text.isNullOrBlank()) {
            ClipSync.showToast(this, "Clipboard is empty")
        } else {
            ClipSync.sendClip(this, text, force = true, toast = true)
        }
        finish()
    }
}
