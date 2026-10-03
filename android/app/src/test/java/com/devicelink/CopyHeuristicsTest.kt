package com.devicelink

import com.devicelink.CopyHeuristics.TYPE_NOTIFICATION_STATE_CHANGED
import com.devicelink.CopyHeuristics.TYPE_VIEW_CLICKED
import com.devicelink.CopyHeuristics.TYPE_WINDOW_STATE_CHANGED
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CopyHeuristicsTest {
    private val words = listOf("Copy", "copy")

    private fun clicked(vararg texts: String) =
        CopyHeuristics.isCopyEvent(TYPE_VIEW_CLICKED, "com.app", "android.widget.Button", texts.toList(), words)

    @Test
    fun copyButtons() {
        assertTrue(clicked("Copy"))
        assertTrue(clicked("Copy link"))
        assertTrue(clicked("COPY TEXT"))
        assertTrue(clicked("", "Copy"))            // label in content description
    }

    @Test
    fun otherClicksIgnored() {
        assertFalse(clicked("Paste"))
        assertFalse(clicked("Copyright"))
        assertFalse(clicked("Send"))
        assertFalse(clicked("Please copy this long paragraph of text into your notes later"))
    }

    @Test
    fun localizedCopyWord() {
        assertTrue(CopyHeuristics.isCopyEvent(TYPE_VIEW_CLICKED, "a", "b", listOf("Kopieren"), listOf("Kopieren")))
    }

    @Test
    fun copiedToast() {
        assertTrue(
            CopyHeuristics.isCopyEvent(
                TYPE_NOTIFICATION_STATE_CHANGED, "com.whatsapp", "android.widget.Toast\$TN",
                listOf("Message copied"), words,
            )
        )
        assertFalse(
            CopyHeuristics.isCopyEvent(
                TYPE_NOTIFICATION_STATE_CHANGED, "com.whatsapp", "android.app.Notification",
                listOf("Text copied"), words,
            )
        )
    }

    @Test
    fun systemClipboardOverlay() {
        assertTrue(
            CopyHeuristics.isCopyEvent(
                TYPE_WINDOW_STATE_CHANGED, "com.android.systemui", "ClipboardOverlayWindow", emptyList(), words,
            )
        )
        assertFalse(
            CopyHeuristics.isCopyEvent(
                TYPE_WINDOW_STATE_CHANGED, "com.android.chrome", "ClipboardThing", listOf("copied"), words,
            )
        )
    }
}
