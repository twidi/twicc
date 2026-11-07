package com.twidi.twicc.push

import com.twidi.twicc.SessionLink
import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.util.UUID

private fun envelope(bytes: ByteArray, type: String, now: Instant): JSONObject {
    require(bytes.size <= 4096)
    val value = JSONObject(bytes.toString(Charsets.UTF_8))
    require(value.getString("type") == type)
    UUID.fromString(value.getString("id"))
    val age = Duration.between(Instant.parse(value.getString("sent_at")), now).seconds
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
            PushSessionDismissal(sessions, Instant.parse(value.getString("sent_at")))
        }.getOrNull()
    }
}

/** Only current, bounded notifications and explicit in-app destinations cross the push boundary. */
data class PushPayload(val id: String, val kind: String, val title: String, val body: String,
    val path: String?, val action: String?, val detail: String?, val sentAt: Instant) {
    val sessionId: String? get() = SessionLink.parse(path)?.sessionId
    companion object {
        val kinds = setOf("user_turn", "pending_request", "extra_usage", "peer")
        val actions = setOf("home", "peerInbox", "peerRequests")
        fun parse(bytes: ByteArray, now: Instant = Instant.now()): PushPayload? = runCatching {
            val value = envelope(bytes, "twicc.notification", now)
            val id = UUID.fromString(value.getString("id")).toString()
            val kind = value.getString("kind").also { require(it in kinds) }
            val title = value.getString("title").also { require(it.isNotBlank() && it.codePointCount(0, it.length) <= 180) }
            val body = value.getString("body").also { require(it.codePointCount(0, it.length) <= 1000) }
            val path = if (value.has("path")) SessionLink.parse(value.getString("path"))?.path.also { require(it != null) } else null
            val action = if (value.has("action")) value.getString("action").also { require(it in actions) } else null
            val detail = if (value.has("detail")) value.getString("detail").also { require(it.length <= 96 && it.matches(Regex("[A-Za-z0-9_-]+"))) } else null
            PushPayload(id, kind, title, body, path, action, detail, Instant.parse(value.getString("sent_at")))
        }.getOrNull()
    }
}
