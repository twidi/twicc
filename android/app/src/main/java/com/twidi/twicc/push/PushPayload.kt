package com.twidi.twicc.push

import com.twidi.twicc.SessionLink
import com.twidi.twicc.i18n.UiMessage
import com.twidi.twicc.i18n.UiMessageJson
import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.util.UUID

private fun JSONObject.requiredString(name: String): String {
    val value = get(name)
    require(value is String)
    return value
}

private fun envelope(bytes: ByteArray, type: String, now: Instant): JSONObject {
    require(bytes.size <= 4096)
    val value = JSONObject(bytes.toString(Charsets.UTF_8))
    require(value.requiredString("type") == type)
    UUID.fromString(value.requiredString("id"))
    val age = Duration.between(Instant.parse(value.requiredString("sent_at")), now).seconds
    require(age in -300..600)
    return value
}

/** Silent removal command. It cannot create a notification or navigate the app. */
data class PushSessionDismissal(val sessionIds: List<String>, val sentAt: Instant) {
    companion object {
        fun parse(bytes: ByteArray, now: Instant = Instant.now()): PushSessionDismissal? = runCatching {
            val value = envelope(bytes, "twicc.dismiss_sessions", now)
            val ids = value.getJSONArray("session_ids")
            require(ids.length() in 1..12)
            val sessions = (0 until ids.length()).map { index ->
                val id = ids.get(index)
                require(id is String && id.matches(Regex("[A-Za-z0-9_-]{1,255}")))
                id
            }.distinct()
            PushSessionDismissal(sessions, Instant.parse(value.requiredString("sent_at")))
        }.getOrNull()
    }
}

/** Only current, bounded notifications and explicit in-app destinations cross the push boundary. */
data class PushPayload(val id: String, val kind: String, val title: UiMessage, val body: UiMessage,
    val path: String?, val action: String?, val detail: String?, val sentAt: Instant) {
    val sessionId: String? get() = SessionLink.parse(path)?.sessionId
    companion object {
        val kinds = setOf("user_turn", "pending_request", "extra_usage", "peer")
        val actions = setOf("home", "peerInbox", "peerRequests")
        fun parse(bytes: ByteArray, now: Instant = Instant.now()): PushPayload? = runCatching {
            val value = envelope(bytes, "twicc.notification", now)
            val id = UUID.fromString(value.requiredString("id")).toString()
            val kind = value.requiredString("kind").also { require(it in kinds) }
            val path = if (value.has("path")) SessionLink.parse(value.requiredString("path"))?.path.also { require(it != null) } else null
            val action = if (value.has("action")) value.requiredString("action").also { require(it in actions) } else null
            val detail = if (value.has("detail")) value.requiredString("detail").also { require(it.length <= 96 && it.matches(Regex("[A-Za-z0-9_-]+"))) } else null
            val presentation = if (value.has("version")) {
                val version = value.get("version")
                require((version is Int || version is Long) && (version as Number).toLong() == 2L)
                require(!value.has("title") && !value.has("body"))
                semanticPresentation(value)
            } else {
                // Validate every old content bound before discarding legacy prose.
                val title = value.requiredString("title")
                require(title.isNotBlank() && title.codePointCount(0, title.length) <= 180)
                val body = value.requiredString("body")
                require(body.codePointCount(0, body.length) <= 1000)
                genericPresentation()
            }
            PushPayload(id, kind, presentation.first, presentation.second, path, action, detail,
                Instant.parse(value.requiredString("sent_at")))
        }.getOrNull()

        private fun genericPresentation() = UiMessage("android.push.genericTitle") to UiMessage("android.push.genericBody")

        private fun semanticPresentation(value: JSONObject): Pair<UiMessage, UiMessage> = runCatching {
            val title = value.getJSONObject("title_ui_message")
            val body = value.getJSONObject("body_ui_message")
            // The two roots share one budget. UiMessageJson also validates each descriptor's schema.
            var nodes = 0
            fun visit(message: JSONObject, depth: Int) {
                require(depth <= 4 && ++nodes <= 64)
                val params = message.optJSONObject("params") ?: return
                params.keys().forEach { name ->
                    (params.opt(name) as? JSONObject)?.let { visit(it, depth + 1) }
                }
            }
            visit(title, 1)
            visit(body, 1)
            requireNotNull(UiMessageJson.decode(title)) to requireNotNull(UiMessageJson.decode(body))
        }.getOrElse { genericPresentation() }
    }
}
