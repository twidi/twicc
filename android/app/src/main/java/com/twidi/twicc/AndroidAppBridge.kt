package com.twidi.twicc

import org.json.JSONObject

/** Versioned app commands. Engine transports validate the frame before entering this dispatcher. */
class AndroidAppBridge(
    private val state: () -> JSONObject,
    private val visibility: (String, Boolean) -> Unit,
    private val action: (AppCommand, String?) -> String?,
    private val nativeTts: ((String, JSONObject) -> JSONObject)? = null,
) {
    fun receive(text: String): String {
        var id = ""
        fun reply(error: String? = null, state: JSONObject? = null): String = JSONObject()
            .put("protocol", 1).put("id", id).put("ok", error == null).apply {
                if (error != null) put("error", error)
                if (state != null) put("state", state)
            }.toString()
        try {
            frameError(text)?.let { return it }
            val request = JSONObject(text)
            id = (request.opt("id") as? String).orEmpty()
            if (id.isEmpty() || id.length > 96 || request.opt("protocol") != 1) return reply("Unsupported Android protocol.")
            val wire = request.optString("command")
            if (wire in TTS_COMMANDS) return nativeTts?.let { reply(state = it(wire, request)) } ?: reply("Native speech is unavailable.")
            val command = AppCommand.fromWire(wire) ?: return reply("Unknown Android command.")
            when (command) {
                AppCommand.GET_STATE -> return reply(state = state().apply {
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

    companion object {
        const val MENU_MAX_BYTES = 16 * 1024
        const val TTS_MAX_BYTES = 12 * 1024 * 1024
        val TTS_COMMANDS = setOf("tts.attach", "tts.state", "tts.manual", "tts.listen", "tts.set_muted", "tts.disable", "tts.skip", "tts.stop_all")
        /** Engine ingress and common dispatcher use identical UTF-8 limits and explicit rejection replies. */
        fun frameError(text: String): String? {
            val bytes = text.toByteArray(Charsets.UTF_8).size
            if (bytes <= MENU_MAX_BYTES) return null
            val request = runCatching { JSONObject(text) }.getOrNull()
            val tts = request?.optString("command") in TTS_COMMANDS
            if (tts && bytes <= TTS_MAX_BYTES) return null
            return JSONObject().put("protocol", 1).put("id", (request?.opt("id") as? String).orEmpty().take(96))
                .put("ok", false).put("error", "The Android request is too large.").toString()
        }
    }
}
