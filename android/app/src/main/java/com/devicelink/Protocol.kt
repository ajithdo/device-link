package com.devicelink

/**
 * Message format shared with desktop/device_link.py.
 *
 *   #link #from_phone          #clip #from_laptop
 *   https://example.com        some copied text
 *
 * Plain posts without tags are treated as links by the desktop and ignored by the phone.
 */
object Protocol {
    const val KIND_LINK = "link"
    const val KIND_CLIP = "clip"
    const val MAX_MESSAGE_LEN = 4000
    const val MAX_CLIP_BYTES = 5 * 1024 * 1024

    data class Message(val kind: String?, val source: String?, val payload: String)

    private val URL_RE = Regex("""https?://[^\s<>"'`]+""", RegexOption.IGNORE_CASE)
    private const val TRAILING_PUNCT = ".,;:!?)]}'\""

    fun sanitizeDevice(name: String): String =
        name.trim().lowercase().replace(Regex("""\W"""), "_").ifEmpty { "device" }.take(32)

    fun header(kind: String, device: String) = "#$kind #from_${sanitizeDevice(device)}"

    fun build(kind: String, device: String, payload: String) = "${header(kind, device)}\n$payload"

    fun parse(text: String): Message {
        val first = text.substringBefore('\n')
        val rest = if ('\n' in text) text.substringAfter('\n') else ""
        val tags = first.split(Regex("""\s+""")).filter { it.isNotEmpty() }
        if (tags.isNotEmpty() && tags.all { it.startsWith("#") }) {
            var kind: String? = null
            var source: String? = null
            for (tag in tags.map { it.lowercase() }) {
                when {
                    tag == "#link" || tag == "#clip" -> kind = tag.drop(1)
                    tag.startsWith("#from_") -> source = tag.removePrefix("#from_")
                }
            }
            if (kind != null) return Message(kind, source, rest)
        }
        return Message(null, null, text)
    }

    fun extractUrls(text: String?): List<String> =
        URL_RE.findAll(text.orEmpty())
            .map { it.value.trimEnd { c -> c in TRAILING_PUNCT } }
            .filter { it.isNotEmpty() }
            .distinct()
            .toList()
}
