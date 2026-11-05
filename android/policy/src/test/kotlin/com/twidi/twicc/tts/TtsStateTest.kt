package com.twidi.twicc.tts

import org.junit.Assert.*
import org.junit.Test

class TtsStateTest {
    private val owner = DocumentOwner("instance", "page", 1)
    private fun controller() = TtsController("instance").also { it.dispatch(TtsCommand.AttachDocument(owner)) }
    private val request = mapOf<String, Any?>("kind" to "text", "speech" to mapOf("text" to "hello"),
        "source" to "message", "identity" to null, "text" to "hello", "config_id" to null)
    private fun opened(c: TtsController) {
        c.dispatch(TtsCommand.TransportOpened("socket"))
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "opened_session",
            "session_id" to "session", "resume_secret" to "secret", "revision" to 1, "ack_seq" to 1)))
    }
    private fun manual(c: TtsController, op: String = "manual") = c.dispatch(TtsCommand.Manual(owner, op, request))
    private fun audio(c: TtsController, p: NativePlayback = c.snapshot().playback) = c.dispatch(TtsCommand.ServerFrame("socket",
        mapOf("protocol" to 1, "type" to "audio", "reading_id" to p.readingId, "generation" to p.generation,
            "audio_id" to "audio", "content_type" to "audio/mpeg", "identity" to null)))
    private fun listen(c: TtsController) = c.dispatch(TtsCommand.Listen(owner, ListeningScope("worktree", "project"), false, "2026-11-05T12:00:00.000000Z"))

    @Test fun offlineStopAllCannotBeReversedByOldAudio() {
        val c = controller(); listen(c); opened(c); manual(c); val old = c.snapshot().playback
        c.dispatch(TtsCommand.TransportLost("socket")); c.dispatch(TtsCommand.StopAll)
        val effects = audio(c, old)
        assertNull(c.snapshot().listening); assertEquals("idle", c.snapshot().playback.status)
        assertEquals(0, effects.filterIsInstance<TtsEffect.Prepare>().size)
        val resume = c.dispatch(TtsCommand.Reconnect).filterIsInstance<TtsEffect.Connect>().single().handshake
        assertEquals("stop_all", ((resume["commands"] as List<*>).last() as Map<*, *>)["type"])
    }
    @Test fun manualReservesNewGenerationBeforeCancellingOldReading() {
        val c = controller(); manual(c); opened(c); audio(c); val old = c.snapshot().playback
        val effects = manual(c, "next")
        assertTrue(c.snapshot().playback.generation > old.generation)
        assertEquals("next", c.snapshot().playback.operationId)
        assertEquals(old.readingId, effects.filterIsInstance<TtsEffect.Cancel>().single().readingId)
        assertTrue(audio(c, old).none { it is TtsEffect.Prepare })
    }
    @Test fun automaticToManualAndDisablePreserveManual() {
        val c = controller(); listen(c); opened(c)
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "candidate", "activation_id" to c.snapshot().listening!!.activationId,
            "event_id" to 5, "identity" to null, "text" to "event")))
        assertTrue(c.snapshot().playback.automatic)
        manual(c); c.dispatch(TtsCommand.Disable(owner, c.snapshot().listening!!.activationId))
        assertNull(c.snapshot().listening); assertEquals("manual", c.snapshot().playback.operationId)
    }
    @Test fun replacementToggleAndStaleDocumentCannotCancelNewReading() {
        val c = controller(); listen(c); opened(c); val oldActivation = c.snapshot().listening!!.activationId
        listen(c); val active = c.snapshot().listening!!.activationId; assertNotEquals(oldActivation, active)
        c.dispatch(TtsCommand.Disable(owner, oldActivation)); assertNotNull(c.snapshot().listening)
        manual(c); val old = c.snapshot().playback
        c.dispatch(TtsCommand.AttachDocument(owner.copy(generation = 2)))
        c.dispatch(TtsCommand.StopReading(owner, old.readingId!!, null)); assertEquals(old, c.snapshot().playback)
        c.dispatch(TtsCommand.StopReading(owner.copy(generation = 2), "different", null)); assertEquals(old, c.snapshot().playback)
    }
    @Test fun localSkipRetainsSequenceAndRejectsOfflineManual() {
        val c = controller(); manual(c); opened(c); c.dispatch(TtsCommand.TransportLost("socket"))
        c.dispatch(TtsCommand.Skip); assertEquals("idle", c.snapshot().playback.status)
        manual(c, "offline"); assertEquals("idle", c.snapshot().playback.status)
        val first = c.dispatch(TtsCommand.Reconnect).filterIsInstance<TtsEffect.Connect>().single().handshake
        c.dispatch(TtsCommand.TransportOpened("replacement")); c.dispatch(TtsCommand.TransportLost("replacement"))
        val second = c.dispatch(TtsCommand.Reconnect).filterIsInstance<TtsEffect.Connect>().single().handshake
        assertEquals(first["commands"], second["commands"])
    }
    @Test fun duplicateAudioAndOldSocketsNeverRestartPlayer() {
        val c = controller(); manual(c); opened(c); assertEquals(1, audio(c).filterIsInstance<TtsEffect.Prepare>().size)
        assertTrue(audio(c).none { it is TtsEffect.Prepare })
        c.dispatch(TtsCommand.TransportOpened("replacement")); assertTrue(audio(c).isEmpty())
    }
    @Test fun settlingWaitsForPlayerAndResultSurvivesPolling() {
        val c = controller(); manual(c); opened(c); audio(c); val p = c.snapshot().playback
        c.dispatch(TtsCommand.PlayerFeedback(p.readingId!!, p.generation, PlayerSignal.Started))
        c.dispatch(TtsCommand.PlayerFeedback(p.readingId, p.generation, PlayerSignal.Settling))
        assertEquals("playing", c.snapshot().playback.status)
        c.dispatch(TtsCommand.PlayerFeedback(p.readingId, p.generation, PlayerSignal.Ended(true)))
        assertEquals("idle", c.snapshot().playback.status)
        assertTrue(c.snapshot().results.single().started); assertEquals(true, c.snapshot().results.single().ended["ok"])
    }
    @Test fun idleListenerStopsAndInstanceSwitchInvalidatesEverything() {
        val c = controller(); listen(c); opened(c)
        assertTrue(c.dispatch(TtsCommand.StopAll).filterIsInstance<TtsEffect.Send>().any { it.frame["type"] == "stop_all" })
        c.dispatch(TtsCommand.SwitchInstance("other")); assertNull(c.snapshot().listening)
        assertEquals("other", c.snapshot().instanceId); assertTrue(audio(c).isEmpty())
    }
    @Test fun revisionsAdvanceAndSnapshotsContainNoSecrets() {
        val c = controller(); val revisions = mutableListOf(c.snapshot().revision)
        manual(c); revisions += c.snapshot().revision; opened(c); revisions += c.snapshot().revision
        audio(c); revisions += c.snapshot().revision
        assertTrue(revisions.zipWithNext().all { (a,b) -> b > a })
        assertFalse(c.snapshot().toString().contains("secret"))
    }
    @Test fun stopTimeoutBoundsCleanupWithoutTerminalServerAck() {
        val c = controller(); listen(c); opened(c); manual(c); val old = c.snapshot().playback
        c.dispatch(TtsCommand.TransportLost("socket"))
        val timeout = c.dispatch(TtsCommand.StopAll).filterIsInstance<TtsEffect.StopAfter>().single()
        assertEquals(5000L, timeout.delayMillis)
        val ended = c.dispatch(TtsCommand.StopTimeout(timeout.epoch))
        assertTrue(ended.any { it is TtsEffect.StopService }); assertTrue(ended.any { it is TtsEffect.CloseConnection })
        assertTrue(c.dispatch(TtsCommand.Reconnect).isEmpty())
        assertEquals("idle", c.snapshot().playback.status); assertNull(c.snapshot().listening)
        assertTrue(audio(c, old).isEmpty())
        manual(c, "new")
        assertEquals("new", c.snapshot().playback.operationId)
        assertTrue(c.dispatch(TtsCommand.StopTimeout(timeout.epoch)).isEmpty())
        assertEquals("new", c.snapshot().playback.operationId)
    }
    @Test fun stopAckBeforeTimeoutAndServerCloseWithoutAckBothTerminate() {
        val c = controller(); listen(c); opened(c)
        val stop = c.dispatch(TtsCommand.StopAll)
        val timeout = stop.filterIsInstance<TtsEffect.StopAfter>().single()
        val stopSeq = stop.filterIsInstance<TtsEffect.Send>().single().frame["seq"]
        val acknowledged = c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "state", "revision" to 5, "ack_seq" to stopSeq)))
        assertTrue(acknowledged.any { it is TtsEffect.StopService })
        manual(c, "new"); assertTrue(c.dispatch(TtsCommand.StopTimeout(timeout.epoch)).isEmpty())
        val fresh = controller(); listen(fresh); opened(fresh)
        val expiry = fresh.dispatch(TtsCommand.StopAll).filterIsInstance<TtsEffect.StopAfter>().single()
        fresh.dispatch(TtsCommand.TransportLost("socket"))
        assertTrue(fresh.dispatch(TtsCommand.StopTimeout(expiry.epoch)).any { it is TtsEffect.StopService })
    }
    @Test fun manualReservesWhileInitialConnectionStillOpens() {
        val c = controller(); listen(c); manual(c)
        assertEquals("manual", c.snapshot().playback.operationId)
        assertEquals("loading", c.snapshot().playback.status)
    }
    @Test fun mutedChoiceAndOlderServerRevisionCannotRestoreDisabledListening() {
        val c = controller(); listen(c); opened(c); val id = c.snapshot().listening!!.activationId
        c.dispatch(TtsCommand.SetMuted(owner, id, true)); assertTrue(c.snapshot().listening!!.includeMuted)
        c.dispatch(TtsCommand.Disable(owner, id))
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "state", "revision" to 1, "ack_seq" to 1,
            "listening" to mapOf("activation_id" to id), "pending_count" to 5)))
        assertNull(c.snapshot().listening); assertEquals(0, c.snapshot().pendingCount)
    }
    @Test fun authorizationLossAndSessionLossRequireExplicitActivation() {
        for (code in listOf("unauthorized", "session_lost")) {
            val c = controller(); manual(c); opened(c); audio(c)
            c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error", "code" to code)))
            assertEquals("idle", c.snapshot().playback.status); assertNull(c.snapshot().listening)
            assertTrue(c.dispatch(TtsCommand.Reconnect).isEmpty())
        }
    }
    @Test fun resumeDefersAudioUntilStateAcknowledgesReconciliation() {
        val c = controller(); manual(c); opened(c); val p = c.snapshot().playback
        c.dispatch(TtsCommand.TransportLost("socket"))
        val resume = c.dispatch(TtsCommand.Reconnect).filterIsInstance<TtsEffect.Connect>().single().handshake
        c.dispatch(TtsCommand.TransportOpened("resumed"))
        val reference = mapOf("protocol" to 1, "type" to "audio", "reading_id" to p.readingId, "generation" to p.generation,
            "audio_id" to "audio", "content_type" to "audio/mpeg", "identity" to null)
        assertTrue(c.dispatch(TtsCommand.ServerFrame("resumed", reference)).none { it is TtsEffect.Prepare })
        val effects = c.dispatch(TtsCommand.ServerFrame("resumed", mapOf("protocol" to 1, "type" to "state", "revision" to 10, "ack_seq" to resume["seq"])))
        assertEquals(1, effects.filterIsInstance<TtsEffect.Prepare>().size)
        assertTrue(c.dispatch(TtsCommand.ServerFrame("resumed", reference)).none { it is TtsEffect.Prepare })
    }
    @Test fun summaryChangedRetainsSavedReferenceErrorDetails() {
        val c = controller(); manual(c); opened(c); val p = c.snapshot().playback
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error", "code" to "summary_changed",
            "message" to "Saved summary changed", "status" to 409, "saved_id" to 17, "summary_changed" to true,
            "reading_id" to p.readingId, "generation" to p.generation)))
        assertEquals(409, c.snapshot().error!!["status"]); assertEquals(17, c.snapshot().error!!["saved_id"])
        assertEquals("idle", c.snapshot().playback.status)
    }
    @Test fun terminalResultCacheIsBounded() {
        val c = controller(); listen(c); opened(c)
        repeat(70) { manual(c, "op$it") }
        assertEquals(64, c.snapshot().results.size)
        assertEquals("op5", c.snapshot().results.first().operationId)
    }
    @Test fun manualCompletionWithoutListeningStopsServiceAfterBoundedCleanup() {
        val c = controller(); manual(c); opened(c); audio(c); val p = c.snapshot().playback
        c.dispatch(TtsCommand.PlayerFeedback(p.readingId!!, p.generation, PlayerSignal.Started))
        val effects = c.dispatch(TtsCommand.PlayerFeedback(p.readingId, p.generation, PlayerSignal.Ended(true)))
        val timer = effects.filterIsInstance<TtsEffect.StopAfter>().single()
        assertTrue(effects.filterIsInstance<TtsEffect.Send>().any { it.frame["type"] == "stop_all" })
        c.dispatch(TtsCommand.StopTimeout(timer.epoch))
        assertTrue(c.snapshot().results.single().started)
        assertEquals(true, c.snapshot().results.single().ended["ok"])
        assertTrue(c.dispatch(TtsCommand.Reconnect).isEmpty())
    }
    @Test fun initialHandshakeRetryPreservesUnsentManualAndListenSequences() {
        for (startManual in listOf(true, false)) {
            val c = controller()
            val initial = if (startManual) manual(c) else listen(c)
            val first = initial.filterIsInstance<TtsEffect.Connect>().single().handshake
            c.dispatch(TtsCommand.TransportOpened("lost")); c.dispatch(TtsCommand.TransportLost("lost"))
            val retry = c.dispatch(TtsCommand.Reconnect).filterIsInstance<TtsEffect.Connect>().single().handshake
            assertEquals(first, retry)
            c.dispatch(TtsCommand.TransportOpened("retry"))
            val effects = c.dispatch(TtsCommand.ServerFrame("retry", mapOf("protocol" to 1, "type" to "opened_session",
                "session_id" to "session", "resume_secret" to "secret", "revision" to 1, "ack_seq" to retry["seq"])))
            val sent = effects.filterIsInstance<TtsEffect.Send>().single().frame
            assertEquals(if (startManual) "manual" else "listen", sent["type"])
            assertEquals(2L, sent["seq"])
        }
    }
    @Test fun skipDuringResumeFlushesBeforeLaterHeartbeat() {
        val c = controller(); listen(c); opened(c); manual(c)
        c.dispatch(TtsCommand.TransportLost("socket"))
        val resume = c.dispatch(TtsCommand.Reconnect).filterIsInstance<TtsEffect.Connect>().single().handshake
        c.dispatch(TtsCommand.TransportOpened("resumed"))
        assertTrue(c.dispatch(TtsCommand.Skip).none { it is TtsEffect.Send })
        val flushed = c.dispatch(TtsCommand.ServerFrame("resumed", mapOf("protocol" to 1, "type" to "state",
            "revision" to 10, "ack_seq" to resume["seq"]))).filterIsInstance<TtsEffect.Send>()
        assertEquals("skip", flushed.single().frame["type"])
        assertEquals(5L, flushed.single().frame["seq"])
        val ping = c.dispatch(TtsCommand.Heartbeat).filterIsInstance<TtsEffect.Send>().single().frame
        assertEquals("ping", ping["type"]); assertEquals(6L, ping["seq"])
    }
    @Test fun stopAllDuringResumeFlushesEvenWhileTerminalCleanupWaits() {
        val c = controller(); listen(c); opened(c); manual(c)
        c.dispatch(TtsCommand.TransportLost("socket"))
        val resume = c.dispatch(TtsCommand.Reconnect).filterIsInstance<TtsEffect.Connect>().single().handshake
        c.dispatch(TtsCommand.TransportOpened("resumed"))
        assertTrue(c.dispatch(TtsCommand.StopAll).none { it is TtsEffect.Send })
        val flushed = c.dispatch(TtsCommand.ServerFrame("resumed", mapOf("protocol" to 1, "type" to "state",
            "revision" to 10, "ack_seq" to resume["seq"]))).filterIsInstance<TtsEffect.Send>()
        assertEquals("stop_all", flushed.single().frame["type"])
        val done = c.dispatch(TtsCommand.ServerFrame("resumed", mapOf("protocol" to 1, "type" to "state",
            "revision" to 11, "ack_seq" to flushed.single().frame["seq"])))
        assertTrue(done.any { it is TtsEffect.StopService })
    }
    @Test fun scopeUnavailablePreservesUnrelatedManualPlayback() {
        val c = controller(); listen(c); opened(c); manual(c); audio(c)
        val before = c.snapshot().playback
        val effects = c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error",
            "code" to "scope_unavailable", "message" to "The listening scope is empty or no longer exists.")))
        assertEquals(before, c.snapshot().playback)
        assertNull(c.snapshot().listening); assertEquals(0, c.snapshot().pendingCount)
        assertTrue(effects.none { it is TtsEffect.Cancel })
        assertEquals("scope_unavailable", c.snapshot().error!!["code"])
    }

}
