package com.twidi.twicc

import com.twidi.twicc.push.PushPayload
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidPushPayloadTest {
    private fun parse(value: JSONObject) = PushPayload.parse(value.toString().toByteArray(), java.time.Instant.parse("2026-11-06T14:14:00Z"))
    private fun payload() = JSONObject().put("type", "twicc.notification")
        .put("id", "12345678-1234-1234-1234-123456789abc").put("sent_at", "2026-11-06T14:13:53Z")
        .put("kind", "user_turn").put("title", "Codex finished working").put("body", "Session: Example")

    @Test fun handlesSessionAndPeerDestinationsAndRejectsExternalUrls() {
        assertEquals("/project/p/session/s", parse(payload().put("path", "/project/p/session/s"))?.path)
        assertEquals("s", parse(payload().put("path", "/projects/p/session/s"))?.sessionId)
        assertNull(parse(payload())?.sessionId)
        assertNull(parse(payload().put("path", "https://evil.example")))
        assertEquals("peerInbox", parse(payload().put("action", "peerInbox").put("detail", "42"))?.action)
        assertNull(parse(payload().put("action", "unknown")))
    }
    @Test fun rejectsOtherMessagesAndExpiredOrOversizedNotifications() {
        assertNull(PushPayload.parse(payload().put("type", "twicc.push_probe").toString().toByteArray()))
        assertNull(PushPayload.parse(payload().put("body", "x".repeat(5000)).toString().toByteArray()))
        assertNull(PushPayload.parse(payload().toString().toByteArray(), java.time.Instant.parse("2026-11-06T15:00:00Z")))
    }
}
