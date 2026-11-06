package com.twidi.twicc

/** One live Activity owns visibility. No read history survives navigation or process death. */
class PushNotificationPolicy {
    private var owner: Any? = null
    private var instanceId: String? = null
    private var sessionId: String? = null

    fun show(owner: Any, instanceId: String, sessionId: String?) {
        this.owner = owner
        this.instanceId = instanceId
        this.sessionId = sessionId
    }

    fun hide(owner: Any) {
        if (this.owner !== owner) return
        this.owner = null
        instanceId = null
        sessionId = null
    }

    fun suppresses(instanceId: String, sessionId: String?): Boolean =
        sessionId != null && this.instanceId == instanceId && this.sessionId == sessionId

    companion object {
        fun tag(instanceId: String, kind: String, sessionId: String?): String =
            "twicc-push:$instanceId:$kind" + (sessionId?.let { ":$it" } ?: "")
    }
}
