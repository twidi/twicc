package com.twidi.twicc.tts

import com.twidi.twicc.AndroidAppBridge
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class NativeTtsBridgeTest {
    private val owner = DocumentOwner("instance", "document", 4)
    private val commands = mutableListOf<TtsCommand>()
    private val snapshot = NativeTtsSnapshot(revision = 12, instanceId = "instance", sessionStatus = "connected",
        playback = NativePlayback(readingId = "reading", operationId = "operation", identity = mapOf("sourceKey" to null, "sourceHash" to "hash", "variant" to "summary"), source = "summary", phase = "settling"),
        listening = NativeListening("activation", "project-one", ListeningScope("project", "project-one", "One"), "2026-11-05T12:00:00.000000Z", true),
        pendingCount = 2, error = mapOf("summary_changed" to true, "saved_id" to 7), configurationAvailable = true,
        results = listOf(NativeTtsResult("operation", true, mapOf("ok" to true), mapOf("ok" to true)),
            NativeTtsResult("automatic:first:3", false, mapOf("ok" to false), mapOf("ok" to false, "error" to "Failed"), true, "first", 3)))
    private fun bridge(enabled: Boolean = true) = NativeTtsBridge({ enabled }, { owner }, { snapshot }, { "document" }) { command ->
        commands += command; Acceptance(true, (command as? TtsCommand.Manual)?.operationId)
    }
    private fun NativeTtsBridge.request(command: String, args: JSONObject) = receive(command, args.put("documentToken", "document"))
    @Test fun summaryAndSelectionReachTheCommonOwnerUnchanged() {
        val request = JSONObject("""{"kind":"summary","speech":{"cache":true},"summary":{"source":{"text":"selection","purpose":"selection"},"source_key":null,"source_hash":"hash","policy_key":"policy"},"identity":{"sourceKey":null,"sourceHash":"hash","variant":"summary"},"text":"","config_id":null}""")
        val result = bridge().request("tts.manual", JSONObject().put("operationId", "op").put("request", request))
        assertTrue(result.getBoolean("accepted"))
        val command = commands.single() as TtsCommand.Manual
        assertEquals(owner, command.owner); assertEquals("op", command.operationId)
        assertEquals(null, (command.request["identity"] as Map<*, *>)["sourceKey"])
        assertEquals("selection", ((command.request["summary"] as Map<*, *>)["source"] as Map<*, *>)["text"])
    }
    @Test fun listeningRestoresStableTimelinePageWithoutReplacingDocumentAuthority() {
        bridge().request("tts.listen", JSONObject("""{"pageId":"project-one","scope":{"kind":"project","id":"project-one","label":"One"},"includeMuted":true,"boundary":"2026-11-05T12:00:00.000000Z"}"""))
        val command = commands.single() as TtsCommand.Listen
        assertEquals(owner, command.owner); assertEquals("project-one", command.pageId)
        val controller = TtsController("instance")
        controller.dispatch(TtsCommand.AttachDocument(owner)); controller.dispatch(command)
        assertEquals("project-one", controller.snapshot().listening!!.pageId)
    }
    @Test fun snapshotIsSafeAndIncludesTerminalResultsAndMutedScope() {
        val result = bridge().request("tts.state", JSONObject()).getJSONObject("snapshot")
        assertEquals("settling", result.getJSONObject("playback").getString("phase"))
        assertEquals("project-one", result.getJSONObject("listening").getString("pageId"))
        assertTrue(result.getJSONObject("listening").getBoolean("includeMuted"))
        assertEquals("operation", result.getJSONArray("results").getJSONObject(0).getString("operationId"))
        assertFalse(result.getJSONArray("results").getJSONObject(0).getBoolean("automatic"))
        assertTrue(result.getJSONArray("results").getJSONObject(0).has("readingId"))
        assertEquals(0L, result.getJSONArray("results").getJSONObject(0).getLong("generation"))
        val automatic = result.getJSONArray("results").getJSONObject(1)
        assertTrue(automatic.getBoolean("automatic")); assertEquals("first", automatic.getString("readingId"))
        assertEquals(3L, automatic.getLong("generation")); assertEquals("Failed", automatic.getJSONObject("ended").getString("error"))
        assertFalse(result.toString().contains("resume_secret"))
        assertEquals(0, commands.size)
    }
    @Test fun unsupportedBuildRejectsActivationAndReadingStopRetainsIdentity() {
        assertFalse(bridge(false).request("tts.attach", JSONObject()).getBoolean("accepted"))
        bridge().request("tts.skip", JSONObject("""{"readingId":"reading","identity":{"sourceKey":null,"sourceHash":"hash","variant":"summary"}}"""))
        val command = commands.single() as TtsCommand.StopReading
        assertEquals("reading", command.readingId); assertEquals(snapshot.playback.identity, command.identity)
        assertFalse(bridge().request("tts.skip", JSONObject().put("operationId", "stale-operation")).getBoolean("accepted"))
        assertEquals(1, commands.size)
        assertTrue(bridge().request("tts.skip", JSONObject().put("operationId", "operation")).getBoolean("accepted"))
        assertEquals(command, commands.last())
    }
    @Test fun commonAndEngineGuardUsesUtf8AndKeepsOnlyTtsAtTwelveMiB() {
        val bridge = AndroidAppBridge({ JSONObject() }, { _, _ -> }, { _, _ -> null }, { command, args -> this.bridge().request(command, args) })
        val selection = JSONObject().put("protocol", 1).put("id", "large").put("command", "tts.manual").put("operationId", "op")
            .put("request", JSONObject().put("kind", "text").put("text", "é".repeat(20000)).put("source", "selection").put("speech", JSONObject()))
        assertTrue(JSONObject(bridge.receive(selection.toString())).getBoolean("ok"))
        val menu = selection.put("command", "menuVisibility").toString()
        assertNotNull(AndroidAppBridge.frameError(menu)); assertFalse(JSONObject(bridge.receive(menu)).getBoolean("ok"))
        val huge = selection.put("command", "tts.manual").put("padding", "é".repeat(6 * 1024 * 1024)).toString()
        val error = JSONObject(AndroidAppBridge.frameError(huge)!!)
        assertEquals("large", error.getString("id")); assertTrue(error.getString("error").contains("large"))
        assertEquals(1, commands.size)
    }
    @Test fun oldDocumentCannotAttachOrControlAfterSameOriginReload() {
        var current = owner
        var token = "first-document"
        val bridge = NativeTtsBridge({ true }, { current }, { snapshot }, { token }) { command -> commands += command; Acceptance(true) }
        assertTrue(bridge.receive("tts.attach", JSONObject().put("documentToken", token)).getBoolean("accepted"))
        current = owner.copy(pageId = "replacement-document", generation = 5)
        token = "second-document"
        for (command in listOf("tts.attach", "tts.state", "tts.stop_all")) {
            val rejected = bridge.receive(command, JSONObject().put("documentToken", "first-document"))
            assertFalse(rejected.getBoolean("accepted"))
            assertFalse(rejected.has("snapshot"))
            assertFalse(rejected.toString().contains("second-document"))
        }
        assertEquals(1, commands.size)
        assertTrue(bridge.receive("tts.attach", JSONObject().put("documentToken", token)).getBoolean("accepted"))
        assertEquals(current, (commands.last() as TtsCommand.AttachDocument).owner)
    }
    @Test fun lateCommitAndRegistrationCannotReplaceTheCurrentDocumentToken() {
        val lease = TtsDocumentLease()
        val first = lease.begin()
        assertTrue(lease.commit(first, "first-document"))
        val second = lease.begin()
        assertFalse(lease.commit(first, "first-document"))
        assertFalse(lease.matches("first-document"))
        assertTrue(lease.commit(second, "second-document"))
        assertFalse(lease.commit(second, "first-document"))
        assertTrue(lease.matches("second-document"))
    }

}
