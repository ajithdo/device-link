package com.devicelink

import android.content.Context

class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("device_link", Context.MODE_PRIVATE)

    var botToken: String
        get() = sp.getString("bot_token", "").orEmpty()
        set(v) = sp.edit().putString("bot_token", v.trim()).apply()

    var chatId: Long
        get() = sp.getLong("chat_id", 0L)
        set(v) = sp.edit().putLong("chat_id", v).apply()

    var deviceName: String
        get() = sp.getString("device_name", "phone").orEmpty().ifBlank { "phone" }
        set(v) = sp.edit().putString("device_name", v.trim()).apply()

    var syncEnabled: Boolean
        get() = sp.getBoolean("sync_enabled", false)
        set(v) = sp.edit().putBoolean("sync_enabled", v).apply()

    val isConfigured get() = botToken.isNotEmpty() && chatId != 0L
}
