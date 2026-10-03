package com.devicelink

import android.app.Activity
import android.content.Intent
import android.os.Bundle

/** Share-sheet target: sends shared links to the laptop (non-link text goes to its clipboard). */
class ShareActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val text = listOfNotNull(
            intent.getStringExtra(Intent.EXTRA_TEXT),
            intent.getStringExtra(Intent.EXTRA_SUBJECT),
        ).joinToString("\n")
        val urls = Protocol.extractUrls(text)
        when {
            urls.isNotEmpty() -> ClipSync.sendLinks(this, urls)
            text.isNotBlank() -> ClipSync.sendClip(this, text.trim(), force = true, toast = true)
            else -> ClipSync.showToast(this, "Nothing to send")
        }
        finish()
    }
}
