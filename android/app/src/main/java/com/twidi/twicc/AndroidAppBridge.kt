package com.twidi.twicc

import org.json.JSONObject
import java.util.logging.Level
import java.util.logging.Logger

/** Versioned app commands. Engine transports validate the frame before entering this dispatcher. */
class AndroidAppBridge(
    private val state: () -> JSONObject,
    private val visibility: (String, Boolean) -> Unit,
    private val action: (AppCommand, String?) -> String?,
    private val nativeTts: ((String, JSONObject) -> JSONObject)? = null,
    private val androidPush: ((String, JSONObject) -> JSONObject)? = null,
    private val acknowledgeNavigation: ((String) -> Unit)? = null,
) {
    fun receive(text: String): String {
        var id = ""
        fun reply(error: String? = null, state: JSONObject? = null): String = JSONObject()
            .put("protocol", 1).put("id", id).put("ok", error == null).apply {
                if (error != null) put("error", error)
                if (state != null) put("state", state)
            }.toString()
        fun invalid(reason: String): String {
            Logger.getLogger("TwiCC.AndroidAppBridge").warning(reason)
            return reply("Could not complete this action. Reload this page and try again.")
        }
        try {
            frameError(text)?.let { return it }
            val request = JSONObject(text)
            id = (request.opt("id") as? String).orEmpty()
            if (id.isEmpty() || id.length > 96 || request.opt("protocol") != 1) return invalid("Unsupported Android protocol or request ID")
            val wire = request.optString("command")
            if (wire in TTS_COMMANDS) return nativeTts?.let { reply(state = it(wire, request)) } ?: reply("Speech is unavailable. Update the Android app and try again.")
            if (wire in setOf("push.state", "push.preferences", "push.configure", "push.permission", "push.remove")) return androidPush?.let { reply(state = it(wire, request)) }
                ?: reply("Android notifications are unavailable. Update the Android app and try again.")
            if (wire == "navigation.acknowledge") {
                val navigationId = request.opt("navigationId") as? String ?: return invalid("Missing navigation ID")
                if (navigationId.isEmpty() || navigationId.length > 96) return invalid("Invalid navigation ID")
                acknowledgeNavigation?.invoke(navigationId)
                return reply()
            }
            val command = AppCommand.fromWire(wire) ?: return invalid("Unknown Android command")
            when (command) {
                AppCommand.GET_STATE -> return reply(state = state().apply {
                    if (!has("capabilities")) put("capabilities", JSONObject())
                })
                AppCommand.MENU_VISIBILITY -> {
                    val owner = request.opt("owner") as? String ?: return invalid("Missing menu owner")
                    val visible = request.opt("visible") as? Boolean ?: return invalid("Missing menu visibility")
                    if (owner.isEmpty() || owner.length > 96) return invalid("Invalid menu owner")
                    visibility(owner, visible)
                    // The existing heartbeat also delivers a pending notification tap to the SPA.
                    val current = state()
                    return reply(state = JSONObject().apply {
                        current.optJSONObject("navigation")?.let { put("navigation", it) }
                        current.optJSONObject("push")?.let { put("push", it) }
                    })
                }
                else -> return reply(action(command, request.opt("instanceId") as? String))
            }
        } catch (error: Exception) {
            Logger.getLogger("TwiCC.AndroidAppBridge").log(Level.WARNING, "Android request failed", error)
            return reply("Could not complete this action. Reload this page and try again.")
        }
    }

    companion object {
        const val MENU_MAX_BYTES = 16 * 1024
        const val TTS_MAX_BYTES = 12 * 1024 * 1024
        val TTS_COMMANDS = setOf("tts.attach", "tts.state", "tts.acknowledge", "tts.manual", "tts.listen", "tts.set_muted", "tts.disable", "tts.skip", "tts.stop_all")
        /** Engine ingress and common dispatcher use identical UTF-8 limits and explicit rejection replies. */
        fun frameError(text: String): String? {
            val bytes = text.toByteArray(Charsets.UTF_8).size
            if (bytes <= MENU_MAX_BYTES) return null
            val request = runCatching { JSONObject(text) }.getOrNull()
            val tts = request?.optString("command") in TTS_COMMANDS
            if (tts && bytes <= TTS_MAX_BYTES) return null
            return JSONObject().put("protocol", 1).put("id", (request?.opt("id") as? String).orEmpty().take(96))
                .put("ok", false).put("error", "This action contains too much data. Try a smaller selection.").toString()
        }
    }
}
