package com.twidi.twicc.push

import com.twidi.twicc.SessionLink
import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.util.UUID

/** Only current, bounded notifications and explicit in-app destinations cross the push boundary. */
data class PushPayload(val id: String, val kind: String, val title: String, val body: String,
    val path: String?, val action: String?, val detail: String?) {
    val sessionId: String? get() = SessionLink.parse(path)?.sessionId
    companion object {
        val kinds = setOf("user_turn", "pending_request", "extra_usage", "peer")
        val actions = setOf("home", "peerInbox", "peerRequests")
        fun parse(bytes: ByteArray, now: Instant = Instant.now()): PushPayload? = runCatching {
            require(bytes.size <= 4096)
            val value = JSONObject(bytes.toString(Charsets.UTF_8))
            require(value.getString("type") == "twicc.notification")
            val id = UUID.fromString(value.getString("id")).toString()
            val age = Duration.between(Instant.parse(value.getString("sent_at")), now).seconds
            require(age in -300..600)
            val kind = value.getString("kind").also { require(it in kinds) }
            val title = value.getString("title").also { require(it.isNotBlank() && it.codePointCount(0, it.length) <= 180) }
            val body = value.getString("body").also { require(it.codePointCount(0, it.length) <= 1000) }
            val path = if (value.has("path")) SessionLink.parse(value.getString("path"))?.path.also { require(it != null) } else null
            val action = if (value.has("action")) value.getString("action").also { require(it in actions) } else null
            val detail = if (value.has("detail")) value.getString("detail").also { require(it.length <= 96 && it.matches(Regex("[A-Za-z0-9_-]+"))) } else null
            PushPayload(id, kind, title, body, path, action, detail)
        }.getOrNull()
    }
}
