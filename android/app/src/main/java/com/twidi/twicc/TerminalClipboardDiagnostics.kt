package com.twidi.twicc

import org.json.JSONArray
import org.json.JSONObject

/** Process-local errors only. Never persists clipboard contents or sends them to a server. */
class TerminalClipboardDiagnostics {
    private val errors = ArrayDeque<JSONObject>()

    @Synchronized fun record(diagnostic: JSONObject) {
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
        // The user explicitly requests an English-only diagnostic menu action.
        const val MENU_LABEL = "Get terminal clipboard diagnostics"
        private val FIELDS = listOf("timestamp", "operation", "trigger", "method", "errorName", "errorMessage",
            "focused", "visibility", "secureContext", "userActivation", "clipboardRead", "clipboardWrite",
            "userAgent", "websocketState", "terminalConnected", "textLength", "multiline")
    }
}
