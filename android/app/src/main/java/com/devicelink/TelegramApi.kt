package com.devicelink

import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.UUID

class TelegramException(message: String) : IOException(message)

/** Minimal Telegram Bot API client using only platform classes. Call from background threads. */
class TelegramApi(private val token: String) {
    private val base = "https://api.telegram.org"

    fun getMe(): JSONObject = call("getMe", JSONObject(), 15_000) as JSONObject

    fun getUpdates(offset: Long?, timeoutSec: Int = 25): JSONArray {
        val params = JSONObject()
            .put("timeout", timeoutSec)
            .put("allowed_updates", JSONArray().put("channel_post"))
        if (offset != null) params.put("offset", offset)
        return call("getUpdates", params, (timeoutSec + 10) * 1000) as JSONArray
    }

    fun sendText(chatId: Long, text: String) {
        call(
            "sendMessage",
            JSONObject().put("chat_id", chatId).put("text", text)
                .put("disable_web_page_preview", true).put("disable_notification", true),
            20_000,
        )
    }

    fun sendDocument(chatId: Long, filename: String, content: ByteArray, caption: String) {
        val boundary = "----DeviceLink" + UUID.randomUUID()
        val body = ByteArrayOutputStream()
        fun field(name: String, value: String) {
            body.write("--$boundary\r\nContent-Disposition: form-data; name=\"$name\"\r\n\r\n$value\r\n".toByteArray())
        }
        field("chat_id", chatId.toString())
        field("caption", caption)
        field("disable_notification", "true")
        body.write(
            ("--$boundary\r\nContent-Disposition: form-data; name=\"document\"; filename=\"$filename\"\r\n" +
                "Content-Type: text/plain; charset=utf-8\r\n\r\n").toByteArray()
        )
        body.write(content)
        body.write("\r\n--$boundary--\r\n".toByteArray())
        request("sendDocument", "multipart/form-data; boundary=$boundary", body.toByteArray(), 60_000)
    }

    fun downloadFile(fileId: String): ByteArray {
        val info = call("getFile", JSONObject().put("file_id", fileId), 20_000) as JSONObject
        val path = info.getString("file_path").split('/').joinToString("/") { URLEncoder.encode(it, "UTF-8") }
        val conn = URL("$base/file/bot$token/$path").openConnection() as HttpURLConnection
        conn.connectTimeout = 15_000
        conn.readTimeout = 60_000
        try {
            if (conn.responseCode != 200) throw TelegramException("download: HTTP ${conn.responseCode}")
            return conn.inputStream.use { it.readBytes() }
        } finally {
            conn.disconnect()
        }
    }

    private fun call(method: String, params: JSONObject, timeoutMs: Int): Any =
        request(method, "application/json", params.toString().toByteArray(), timeoutMs)

    private fun request(method: String, contentType: String, body: ByteArray, timeoutMs: Int): Any {
        while (true) {
            val conn = URL("$base/bot$token/$method").openConnection() as HttpURLConnection
            conn.connectTimeout = 15_000
            conn.readTimeout = timeoutMs
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", contentType)
            try {
                conn.outputStream.use { it.write(body) }
                val code = conn.responseCode
                val stream = if (code in 200..299) conn.inputStream else conn.errorStream
                val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
                val json = try {
                    JSONObject(text)
                } catch (e: Exception) {
                    throw TelegramException("$method: HTTP $code")
                }
                if (json.optBoolean("ok")) return json.get("result")
                val retry = json.optJSONObject("parameters")?.optInt("retry_after", 0) ?: 0
                if (code == 429 && retry > 0) {
                    Thread.sleep((retry + 1) * 1000L)
                    continue
                }
                throw TelegramException("$method: ${json.optString("description", "HTTP $code")}")
            } finally {
                conn.disconnect()
            }
        }
    }
}
