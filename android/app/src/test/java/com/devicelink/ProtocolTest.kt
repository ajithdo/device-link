package com.devicelink

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ProtocolTest {
    @Test
    fun roundTrip() {
        val text = Protocol.build(Protocol.KIND_CLIP, "My Phone!", "hello\nworld")
        assertEquals("#clip #from_my_phone_\nhello\nworld", text)
        assertEquals(Protocol.Message("clip", "my_phone_", "hello\nworld"), Protocol.parse(text))
    }

    @Test
    fun plainPost() {
        val msg = Protocol.parse("look https://example.com")
        assertNull(msg.kind)
        assertEquals("look https://example.com", msg.payload)
    }

    @Test
    fun desktopFormatIsUnderstood() {
        assertEquals(Protocol.Message("clip", "laptop", "x"), Protocol.parse("#clip #from_laptop\nx"))
    }

    @Test
    fun extractUrls() {
        assertEquals(
            listOf("https://a.com/x?y=1", "http://b.org"),
            Protocol.extractUrls("See (https://a.com/x?y=1). Also http://b.org, and https://a.com/x?y=1"),
        )
        assertEquals(emptyList<String>(), Protocol.extractUrls("file:///etc/passwd"))
    }
}
