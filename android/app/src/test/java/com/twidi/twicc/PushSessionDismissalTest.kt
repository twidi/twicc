package com.twidi.twicc

import com.twidi.twicc.push.PushSessionDismissal
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.time.Instant

class PushSessionDismissalTest {
    private val now = Instant.parse("2026-11-07T12:00:00Z")
    private fun message() = JSONObject().put("type", "twicc.dismiss_sessions")
        .put("id", "12345678-1234-1234-1234-123456789abc").put("sent_at", now.toString())
        .put("session_ids", JSONArray(listOf("a", "b")))
    private fun parse(value: JSONObject) = PushSessionDismissal.parse(value.toString().toByteArray(), now)

    @Test fun acceptsOnlyCurrentBoundedSessionDismissals() {
        assertEquals(listOf("a", "b"), parse(message())?.sessionIds)
        assertEquals(now, parse(message())?.sentAt)
        assertNull(parse(message().put("type", "twicc.notification")))
        assertNull(parse(message().put("session_ids", JSONArray())))
        assertNull(parse(message().put("session_ids", JSONArray(listOf("../a")))))
        assertNull(parse(message().put("session_ids", JSONArray(listOf(42)))))
        assertNull(parse(message().put("session_ids", JSONArray(List(13) { "s$it" }))))
        assertNull(parse(message().put("sent_at", now.minusSeconds(601).toString())))
        assertNull(parse(message().put("sent_at", now.plusSeconds(301).toString())))
        assertNull(parse(message().put("id", "not-a-uuid")))
        // Semantic notification fields cannot turn an event into a silent removal command.
        assertNull(parse(message().put("type", "twicc.notification").put("version", 2)
            .put("title_ui_message", JSONObject().put("key", "android.push.genericTitle"))
            .put("body_ui_message", JSONObject().put("key", "android.push.genericBody"))))
    }
}
