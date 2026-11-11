package com.twidi.twicc

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/** Process-local errors only. Never persists clipboard contents or sends them to a server. */
class TerminalClipboardDiagnostics {
    private val errors = ArrayDeque<JSONObject>()
    @Volatile var enabled = false
        private set

    @Synchronized fun configure(enabled: Boolean) {
        this.enabled = enabled
        if (!enabled) errors.clear()
    }

    @Synchronized fun record(diagnostic: JSONObject) {
        if (!enabled) return
        val entry = JSONObject().put("recordedAt", System.currentTimeMillis())
        for (field in FIELDS) {
            when (val value = diagnostic.opt(field)) {
                is String -> entry.put(field, value.take(512))
                is Boolean, is Number -> entry.put(field, value)
            }
        }
        if (errors.size == 20) errors.removeFirst()
        errors.addLast(entry)
    }

    @Synchronized fun report(environment: JSONObject): String = JSONObject()
        .put("type", "TwiCC terminal clipboard diagnostics")
        .put("environment", environment).put("errors", JSONArray(errors.toList())).toString(2)

    companion object {
        val process = TerminalClipboardDiagnostics()
        // These personal diagnostic controls deliberately stay English.
        const val MENU_LABEL = "Get terminal clipboard diagnostics"
        const val ENABLED_MESSAGE = "Terminal clipboard diagnostics enabled"
        const val DISABLED_MESSAGE = "Terminal clipboard diagnostics disabled"

        fun restore(context: Context) {
            process.configure(preferences(context).getBoolean("enabled", false))
        }

        fun toggle(context: Context): Boolean {
            val preferences = preferences(context)
            val enabled = !preferences.getBoolean("enabled", false)
            preferences.edit().putBoolean("enabled", enabled).apply()
            process.configure(enabled)
            return enabled
        }

        private fun preferences(context: Context) =
            context.getSharedPreferences("terminalClipboardDiagnostics", Context.MODE_PRIVATE)

        private val FIELDS = listOf("timestamp", "operation", "trigger", "method", "errorName", "errorMessage",
            "focused", "visibility", "secureContext", "userActivation", "clipboardRead", "clipboardWrite",
            "userAgent", "websocketState", "terminalConnected", "textLength", "multiline")
    }
}
