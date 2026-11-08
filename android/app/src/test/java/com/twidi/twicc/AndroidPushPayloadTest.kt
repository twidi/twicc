package com.twidi.twicc

import com.twidi.twicc.push.PushPayload
import com.twidi.twicc.i18n.UiMessage
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
    private fun semantic() = payload().apply {
        remove("title"); remove("body")
        put("version", 2)
        put("title_ui_message", JSONObject().put("key", "session.notifications.finished")
            .put("params", JSONObject().put("provider", "Codex")))
        put("body_ui_message", JSONObject().put("key", "session.notifications.body")
            .put("params", JSONObject().put("project", "Project").put("session", "Session")))
    }

    @Test fun acceptsSemanticVersion2AndKeepsSafeNavigation() {
        val parsed = parse(semantic().put("path", "/project/p/session/s"))!!
        assertEquals(UiMessage("session.notifications.finished", mapOf("provider" to "Codex")), parsed.title)
        assertEquals("session.notifications.body", parsed.body.key)
        assertEquals("s", parsed.sessionId)
    }

    @Test fun legacyPayloadUsesGenericDescriptorsOnlyAfterContentValidation() {
        val parsed = parse(payload().put("action", "peerInbox").put("detail", "42"))!!
        assertEquals(UiMessage("android.push.genericTitle"), parsed.title)
        assertEquals(UiMessage("android.push.genericBody"), parsed.body)
        assertEquals("peerInbox", parsed.action)
        assertEquals("42", parsed.detail)
        assertNull(parse(payload().put("title", "x".repeat(181))))
        assertNull(parse(payload().put("title", " ")))
        assertNull(parse(payload().put("body", "x".repeat(1001))))
        assertNull(parse(payload().put("body", JSONObject.NULL)))
        assertNull(parse(payload().put("title", 42)))
        assertNull(parse(payload().put("body", 42)))
        assertNotNull(parse(payload().put("title", "😀".repeat(180))))
        assertNull(parse(payload().put("title", "😀".repeat(181))))
    }

    @Test fun rejectsInvalidEnvelopeVersionsAndNavigationBeforeFallback() {
        for (version in listOf(1, 3, "2", 2.5, JSONObject.NULL)) {
            assertNull(parse(semantic().put("version", version)))
        }
        for (base in listOf(payload(), semantic())) {
            assertNull(parse(JSONObject(base.toString()).put("id", "not-a-uuid")))
            assertNull(parse(JSONObject(base.toString()).put("sent_at", "2026-11-06T12:00:00Z")))
            assertNull(parse(JSONObject(base.toString()).put("path", "//evil.example/session/s")))
            assertNull(parse(JSONObject(base.toString()).put("action", "unknown")))
            assertNull(parse(JSONObject(base.toString()).put("detail", "../x")))
            assertNull(parse(JSONObject(base.toString()).put("detail", 42)))
            assertNull(parse(JSONObject(base.toString()).put("detail", "x".repeat(97))))
            assertNull(parse(JSONObject(base.toString()).put("sent_at", "2026-11-06T14:20:00Z")))
            assertNull(parse(JSONObject(base.toString()).put("kind", "unknown")))
        }
        assertNull(parse(semantic().put("title", "legacy text")))
        assertNull(parse(semantic().put("body", "legacy text")))
        assertNull(parse(semantic().put("padding", "x".repeat(4096))))
    }

    @Test fun invalidDescriptorsUseGenericAndKeepSameTap() {
        for (bad in listOf(JSONObject.NULL, "text", JSONObject().put("key", "key")
            .put("params", JSONObject().put("bad", org.json.JSONArray())))) {
            val parsed = parse(semantic().put("body_ui_message", bad).put("path", "/project/p/session/s"))!!
            assertEquals(UiMessage("android.push.genericTitle"), parsed.title)
            assertEquals(UiMessage("android.push.genericBody"), parsed.body)
            assertEquals("/project/p/session/s", parsed.path)
        }
    }

    @Test fun boundsDescriptorDepthAndTotalNodesAcrossBothRoots() {
        fun nested(depth: Int): JSONObject = JSONObject().put("key", "android.push.genericBody").apply {
            if (depth > 1) put("params", JSONObject().put("child", nested(depth - 1)))
        }
        fun wide(children: Int) = JSONObject().put("key", "android.push.genericBody")
            .put("params", JSONObject().apply { repeat(children) { put("p$it", nested(1)) } })
        val bounded = parse(semantic().put("title_ui_message", nested(4)))!!
        assertEquals("session.notifications.body", bounded.body.key)
        val tooDeep = parse(semantic().put("title_ui_message", nested(5)))!!
        assertEquals("android.push.genericBody", tooDeep.body.key)
        val exact = parse(semantic().put("title_ui_message", wide(31)).put("body_ui_message", wide(31)))!!
        assertEquals(31, exact.title.params.size)
        val tooWide = parse(semantic().put("title_ui_message", wide(32)).put("body_ui_message", wide(31)))!!
        assertEquals("android.push.genericTitle", tooWide.title.key)
    }

}
