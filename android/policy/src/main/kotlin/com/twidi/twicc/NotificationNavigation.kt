package com.twidi.twicc

import java.net.URI

/** Only a session path is transferable between a push payload and its saved instance. */
data class SessionLink private constructor(val path: String, val projectId: String, val sessionId: String) {
    fun isCurrent(url: String): Boolean {
        val current = runCatching { currentPattern.matchEntire(URI(url).rawPath.orEmpty()) }.getOrNull() ?: return false
        return current.groupValues[1] == projectId && current.groupValues[2] == sessionId
    }

    companion object {
        private const val BASE = "/projects?/([A-Za-z0-9_-]{1,255})/session/([A-Za-z0-9_-]{1,255})"
        private val targetPattern = Regex("$BASE/?")
        private val currentPattern = Regex("$BASE(?:/.*)?")
        fun parse(value: String?): SessionLink? {
            val match = value?.let(targetPattern::matchEntire) ?: return null
            return SessionLink(value.removeSuffix("/"), match.groupValues[1], match.groupValues[2])
        }
    }
}

/** One pending tap, removed after Vue Router handles it. No persisted notification history. */
class NotificationNavigation {
    data class Target(val id: String, val path: String?, val action: String? = null, val detail: String? = null)
    var pending: Target? = null
        private set

    fun open(id: String, path: String?, currentUrl: String, action: String? = null, detail: String? = null) {
        val link = SessionLink.parse(path)
        val validAction = action?.takeIf { it in setOf("home", "peerInbox", "peerRequests") }
        pending = if (validAction != null || (link != null && !link.isCurrent(currentUrl)))
            Target(id, link?.path, validAction, detail?.takeIf { it.matches(Regex("[A-Za-z0-9_-]{1,96}")) }) else null
    }

    fun acknowledge(id: String) {
        if (pending?.id == id) pending = null
    }
}
