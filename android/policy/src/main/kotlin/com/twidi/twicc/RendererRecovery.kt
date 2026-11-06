package com.twidi.twicc

/** One automatic renderer restart per minute. Manual reload starts a new budget. */
class RendererRecovery(var lastAttemptMs: Long? = null) {
    private var pending = false

    fun stopped() { pending = true }

    fun takeAttempt(nowMs: Long, foreground: Boolean): Boolean {
        if (!pending || !foreground) return false
        val elapsed = lastAttemptMs?.let { nowMs - it }
        if (elapsed != null && elapsed in 0 until 60_000) return false
        pending = false
        lastAttemptMs = nowMs
        return true
    }

    fun reset() { pending = false; lastAttemptMs = null }
}
