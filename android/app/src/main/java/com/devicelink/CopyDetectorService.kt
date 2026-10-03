package com.devicelink

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
import android.content.res.Resources
import android.provider.Settings
import android.view.accessibility.AccessibilityEvent

/**
 * Accessibility service that only listens for copy actions (it does not read window content).
 * When the user taps "Copy" or a "Copied" toast appears, it asks [ClipCapture] to read the
 * clipboard and send it to the laptop.
 */
class CopyDetectorService : AccessibilityService() {

    companion object {
        fun isEnabled(context: Context): Boolean {
            val enabled = Settings.Secure.getString(
                context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
            ) ?: return false
            val me = ComponentName(context, CopyDetectorService::class.java)
            return enabled.split(':').any { ComponentName.unflattenFromString(it) == me }
        }
    }

    private val copyWords by lazy {
        listOf(Resources.getSystem().getString(android.R.string.copy), "copy")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (event.packageName == packageName) return
        val texts = event.text.mapNotNull { it?.toString() } + listOfNotNull(event.contentDescription?.toString())
        val isCopy = CopyHeuristics.isCopyEvent(
            event.eventType, event.packageName?.toString(), event.className?.toString(), texts, copyWords,
        )
        if (isCopy && Prefs(this).syncEnabled) {
            ClipCapture.request(this, delayMs = 300)
        }
    }

    override fun onInterrupt() {}
}
