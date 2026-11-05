package com.twidi.twicc

import org.json.JSONObject

/** Versioned app commands. Engine transports validate the frame before entering this dispatcher. */
class AndroidAppBridge(
    private val state: () -> JSONObject,
    private val visibility: (String, Boolean) -> Unit,
    private val action: (AppCommand, String?) -> String?,
) {
    fun receive(text: String): String {
        var id = ""
        fun reply(error: String? = null, state: JSONObject? = null): String = JSONObject()
            .put("protocol", 1).put("id", id).put("ok", error == null).apply {
                if (error != null) put("error", error)
                if (state != null) put("state", state)
            }.toString()
        try {
            if (text.length > 16384) return reply("Invalid Android request.")
            val request = JSONObject(text)
            id = (request.opt("id") as? String).orEmpty()
            if (id.isEmpty() || id.length > 96 || request.opt("protocol") != 1) return reply("Unsupported Android protocol.")
            val command = AppCommand.fromWire(request.optString("command")) ?: return reply("Unknown Android command.")
            when (command) {
                AppCommand.GET_STATE -> return reply(state = state().apply {
                    // Capability stays absent until the common player and integration gates pass.
                    if (!has("capabilities")) put("capabilities", JSONObject())
                })
                AppCommand.MENU_VISIBILITY -> {
                    val owner = request.opt("owner") as? String ?: return reply("Missing menu owner.")
                    val visible = request.opt("visible") as? Boolean ?: return reply("Missing menu visibility.")
                    if (owner.isEmpty() || owner.length > 96) return reply("Invalid menu owner.")
                    visibility(owner, visible)
                    return reply()
                }
                else -> return reply(action(command, request.opt("instanceId") as? String))
            }
        } catch (_: Exception) { return reply("Invalid Android request.") }
    }
}
