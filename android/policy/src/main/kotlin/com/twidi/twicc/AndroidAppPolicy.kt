package com.twidi.twicc

enum class AppCommand(val wireName: String) {
    GET_STATE("getState"), MENU_VISIBILITY("menuVisibility"), SWITCH_INSTANCE("switchInstance"),
    MANAGE_INSTANCES("manageInstances"), RELOAD("reload"), FORCE_REFRESH("forceRefresh"),
    SERVER_HOME("serverHome"), OPEN_IN_BROWSER("openInBrowser");
    companion object { fun fromWire(value: String): AppCommand? = entries.find { it.wireName == value } }
}

object AndroidAppPolicy {
    fun acceptsFrame(instance: InstanceUrl, url: String, mainFrame: Boolean): Boolean =
        mainFrame && instance.allowsNavigation(url)
}

/** A missing heartbeat reveals native controls; an old rail cannot revoke its successor's lease. */
class AppMenuLease {
    private var owner: String? = null
    private var deadline = 0L
    fun report(id: String, visible: Boolean, now: Long) {
        if (visible) { owner = id; deadline = now + TIMEOUT_MS }
        else if (owner == id) reset()
    }
    fun remainingMs(now: Long): Long = if (owner == null) 0L else (deadline - now).coerceAtLeast(0L)
    fun isVisible(now: Long): Boolean = remainingMs(now) > 0L
    fun reset() { owner = null; deadline = 0L }
    companion object { const val TIMEOUT_MS = 6000L }
}
