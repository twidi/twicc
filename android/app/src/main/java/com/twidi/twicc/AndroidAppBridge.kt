package com.twidi.twicc

import org.json.JSONObject
import java.util.logging.Level
import java.util.logging.Logger
import com.twidi.twicc.i18n.AppLanguage
import com.twidi.twicc.i18n.LanguageSelection
import com.twidi.twicc.i18n.UiMessage
import com.twidi.twicc.i18n.UiMessageJson

/** Versioned app commands. Engine transports validate the frame before entering this dispatcher. */
class AndroidAppBridge(
    private val state: () -> JSONObject,
    private val visibility: (String, Boolean) -> Unit,
    private val action: (AppCommand, String?) -> UiMessage?,
    private val nativeTts: ((String, JSONObject) -> JSONObject)? = null,
    private val androidPush: ((String, JSONObject) -> JSONObject)? = null,
    private val acknowledgeNavigation: ((String) -> Unit)? = null,
    private val sessionVisibility: ((String?) -> Unit)? = null,
    private val appLanguage: ((String, JSONObject) -> LanguageSelection)? = null,
    private val terminalClipboardDiagnostic: ((JSONObject) -> Unit)? = null,
) {
    fun receive(text: String): String {
        var id = ""
        fun reply(error: String? = null, state: JSONObject? = null, uiMessage: UiMessage? = null): String = JSONObject()
            .put("protocol", 1).put("id", id).put("ok", error == null && uiMessage == null).apply {
                if (error != null) put("error", error)
                if (state != null) put("state", state)
                if (uiMessage != null) put("ui_message", UiMessageJson.encode(uiMessage))
            }.toString()
        fun invalid(reason: String): String {
            Logger.getLogger("TwiCC.AndroidAppBridge").warning(reason)
            return reply(uiMessage = UiMessage("common.android.errors.actionFailed"))
        }
        try {
            frameError(text)?.let { return it }
            val request = JSONObject(text)
            id = (request.opt("id") as? String).orEmpty()
            if (id.isEmpty() || id.length > 96 || request.opt("protocol") != 1) return invalid("Unsupported Android protocol or request ID")
            val wire = request.optString("command")
            if (wire == "terminalClipboard.record") {
                val diagnostic = request.optJSONObject("diagnostic") ?: return invalid("Missing terminal clipboard diagnostic")
                terminalClipboardDiagnostic?.invoke(diagnostic) ?: return invalid("Terminal clipboard diagnostics unavailable")
                return reply()
            }
            if (wire == "locale.get" || wire == "locale.set") {
                val selection = appLanguage?.invoke(wire, request)
                    ?: return reply(uiMessage = UiMessage("android.language.errors.updateApp"))
                return reply(state = selection.state.bridgeState(), uiMessage = selection.error)
            }
            if (wire in TTS_COMMANDS) return nativeTts?.let { reply(state = it(wire, request)) } ?: reply(uiMessage = UiMessage("android.speech.errors.updateRequired"))
            if (wire in setOf("push.state", "push.preferences", "push.configure", "push.permission", "push.remove", "push.soundSettings", "push.prepareSounds")) return androidPush?.let { reply(state = it(wire, request)) }
                ?: reply(uiMessage = UiMessage("android.push.errors.updateApp"))
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
                    appLanguage?.let { language ->
                        getJSONObject("capabilities").put("appLanguage", 1)
                        put("appLanguage", language("locale.get", JSONObject()).state.bridgeState())
                    }
                })
                AppCommand.MENU_VISIBILITY -> {
                    val owner = request.opt("owner") as? String ?: return invalid("Missing menu owner")
                    val visible = request.opt("visible") as? Boolean ?: return invalid("Missing menu visibility")
                    if (owner.isEmpty() || owner.length > 96) return invalid("Invalid menu owner")
                    val sessionId = if (request.isNull("sessionId")) null else request.opt("sessionId") as? String
                        ?: return invalid("Invalid displayed session")
                    if (sessionId != null && !sessionId.matches(Regex("[A-Za-z0-9_-]{1,255}"))) return invalid("Invalid displayed session")
                    if (request.has("sessionId")) sessionVisibility?.invoke(sessionId)
                    visibility(owner, visible)
                    // The existing heartbeat also delivers a pending notification tap to the SPA.
                    val current = state()
                    return reply(state = JSONObject().apply {
                        current.optJSONObject("navigation")?.let { put("navigation", it) }
                        current.optJSONObject("push")?.let { put("push", it) }
                    })
                }
                else -> return reply(uiMessage = action(command, request.opt("instanceId") as? String))
            }
        } catch (error: Exception) {
            Logger.getLogger("TwiCC.AndroidAppBridge").log(Level.WARNING, "Android request failed", error)
            return reply(uiMessage = UiMessage("common.android.errors.actionFailed"))
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
                .put("ok", false).put("error", "This action contains too much data. Try a smaller selection.")
                .put("ui_message", UiMessageJson.encode(UiMessage("common.android.errors.tooLarge"))).toString()
        }
    }
}

/** Both engines supply their active document lease, independently of speech service attachment. */
class AppLanguageBridge(
    private val read: () -> AppLanguage,
    private val documentToken: () -> String?,
    private val select: (String, Long) -> LanguageSelection,
) {
    fun receive(command: String, args: JSONObject): LanguageSelection {
        if (command == "locale.get") return LanguageSelection(read(), true)
        fun reject(key: String) = LanguageSelection(read(), false, UiMessage(key))
        val expectedToken = documentToken()
        if (expectedToken == null || args.opt("documentToken") != expectedToken)
            return reject("android.language.errors.staleDocument")
        val locale = args.opt("locale") as? String ?: return reject("android.language.errors.unsupported")
        val revision = args.opt("expectedRevision") as? Number
            ?: return reject("android.language.errors.invalidRevision")
        val numeric = revision.toDouble()
        if (!numeric.isFinite() || numeric < 0 || numeric > AppLanguage.MAX_REVISION.toDouble() || numeric != revision.toLong().toDouble())
            return reject("android.language.errors.invalidRevision")
        return select(locale, revision.toLong())
    }
}

private fun AppLanguage.bridgeState(): JSONObject = JSONObject().put("locale", locale).put("revision", revision)
    .put("supportedLocales", org.json.JSONArray(AppLanguage.SUPPORTED_LOCALES))
