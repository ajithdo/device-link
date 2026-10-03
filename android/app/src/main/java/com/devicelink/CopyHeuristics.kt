package com.devicelink

/** Decides whether an accessibility event means "the user just copied something". Pure, for unit tests. */
object CopyHeuristics {
    const val TYPE_VIEW_CLICKED = 1                  // AccessibilityEvent constants
    const val TYPE_WINDOW_STATE_CHANGED = 32
    const val TYPE_NOTIFICATION_STATE_CHANGED = 64

    private const val MAX_BUTTON_LABEL = 30

    fun isCopyEvent(
        eventType: Int,
        packageName: String?,
        className: String?,
        texts: List<String>,
        copyWords: Collection<String>,
    ): Boolean {
        val words = copyWords.map { it.lowercase().trim() }.filter { it.isNotEmpty() }
        val lowered = texts.map { it.lowercase().trim() }.filter { it.isNotEmpty() }
        return when (eventType) {
            // A tap on a short "Copy" / "Copy link" / "Copy text" button or menu item.
            TYPE_VIEW_CLICKED -> lowered.any { t ->
                t.length <= MAX_BUTTON_LABEL && words.any { w -> t == w || t.startsWith("$w ") }
            }
            // Toasts like "Copied to clipboard" / "Link copied".
            TYPE_NOTIFICATION_STATE_CHANGED ->
                className.orEmpty().contains("Toast") && lowered.any { "copied" in it || "clipboard" in it }
            // Android 13+ system clipboard preview shown after any copy.
            TYPE_WINDOW_STATE_CHANGED ->
                packageName.orEmpty().contains("systemui") &&
                    (className.orEmpty().contains("clipboard", ignoreCase = true) ||
                        lowered.any { "copied" in it || "clipboard" in it })
            else -> false
        }
    }
}
