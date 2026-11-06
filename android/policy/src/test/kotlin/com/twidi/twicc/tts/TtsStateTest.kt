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

    @Test fun consumedListeningFailureCannotReturnInLaterBackendState() {
        val c = controller(); listen(c); opened(c)
        val failure = mapOf("code" to "scope_unavailable", "message" to "Timeline reading stopped.",
            "activation_id" to c.snapshot().listening!!.activationId)
        val effects = c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error") + failure))
        assertNotNull(c.snapshot().error)
        assertNull(c.snapshot().listening)
        c.forgetOutcomes(emptySet(), c.snapshot().error)
        val sequence = effects.filterIsInstance<TtsEffect.Send>().last().frame["seq"]
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "state",
            "revision" to 10, "ack_seq" to sequence, "error" to failure)))
        assertNull(c.snapshot().error)
        assertNull(c.snapshot().listening)
    }

    @Test fun timelinePauseSkipsEitherReadingAndSurvivesAcknowledgedBackendDisable() {
        for (automatic in listOf(false, true)) {
            val c = controller(); listen(c); opened(c)
            if (automatic) candidate(c) else manual(c)
            audio(c)
            val before = c.snapshot().playback
            val id = c.snapshot().listening!!.activationId
            val effects = c.dispatch(TtsCommand.PauseTimeline(id))
            assertTrue(c.snapshot().listening!!.paused)
            assertNull(c.snapshot().playback.readingId)
            assertEquals(listOf("skip", "disable"), effects.filterIsInstance<TtsEffect.Send>().map { it.frame["type"] })
            assertTrue(effects.any { it is TtsEffect.Cancel && it.readingId == before.readingId })
            assertEquals(1, effects.filterIsInstance<TtsEffect.Release>().size)
            assertTrue(effects.none { it is TtsEffect.StopService || it is TtsEffect.StopAfter })
            val sequence = effects.filterIsInstance<TtsEffect.Send>().last().frame["seq"]
            c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "state",
                "revision" to 10, "ack_seq" to sequence, "listening" to null, "pending_count" to 0)))
            assertTrue(c.snapshot().listening!!.paused)
            assertTrue(candidate(c, 2).none { it is TtsEffect.Send && it.frame["type"] == "select" })
            assertTrue(audio(c, before).none { it is TtsEffect.Prepare })
            assertNull(c.snapshot().playback.readingId)
        }
    }
    @Test fun manualReadingWorksDuringTimelinePauseAndCompletionKeepsPause() {
        val c = controller(); listen(c); opened(c)
        c.dispatch(TtsCommand.PauseTimeline(c.snapshot().listening!!.activationId))
        manual(c); assertFalse(c.snapshot().playback.automatic)
        assertTrue(audio(c).any { it is TtsEffect.Prepare })
        val reading = c.snapshot().playback
        val effects = c.dispatch(TtsCommand.PlayerFeedback(reading.readingId!!, reading.generation, PlayerSignal.Ended(true)))
        assertTrue(c.snapshot().listening!!.paused)
        assertTrue(effects.none { it is TtsEffect.StopAfter || it is TtsEffect.StopService })
    }
    @Test fun timelineResumeUsesFreshBoundaryActivationAndPausedFilterChoice() {
        val c = controller(); listen(c); opened(c)
        val before = c.snapshot().listening!!
        c.dispatch(TtsCommand.PauseTimeline(before.activationId))
        val muted = c.dispatch(TtsCommand.SetMuted(owner, before.activationId, true))
        assertTrue(muted.none { it is TtsEffect.Send })
        val boundary = "2026-11-05T13:05:01.123456Z"
        val sent = c.dispatch(TtsCommand.ResumeTimeline(before.activationId, boundary)).filterIsInstance<TtsEffect.Send>().single().frame
        val after = c.snapshot().listening!!
        assertFalse(after.paused); assertNotEquals(before.activationId, after.activationId)
        assertEquals(before.scope, after.scope); assertEquals(before.pageId, after.pageId)
        assertEquals(boundary, after.boundary); assertTrue(after.includeMuted)
        assertEquals("listen", sent["type"]); assertEquals(boundary, sent["boundary"])
        assertEquals(after.activationId, sent["activation_id"]); assertEquals(true, sent["include_muted"])
        val old = c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "candidate",
            "activation_id" to before.activationId, "event_id" to 1)))
        assertTrue(old.none { it is TtsEffect.Send })
        assertTrue(candidate(c, 2).any { it is TtsEffect.Send && it.frame["type"] == "select" })
    }
    @Test fun timelinePauseSurvivesOfflineReconciliationAndRejectsDeferredAudio() {
        val c = controller(); listen(c); opened(c); candidate(c); audio(c)
        val old = c.snapshot().playback
        c.dispatch(TtsCommand.TransportLost("socket"))
        c.dispatch(TtsCommand.PauseTimeline(c.snapshot().listening!!.activationId))
        val resume = c.dispatch(TtsCommand.Reconnect).filterIsInstance<TtsEffect.Connect>().single().handshake
        val commands = (resume["commands"] as List<*>).filterIsInstance<Map<*, *>>()
        assertEquals(listOf("skip", "disable"), commands.takeLast(2).map { it["type"] })
        c.dispatch(TtsCommand.TransportOpened("resumed"))
        c.dispatch(TtsCommand.ServerFrame("resumed", mapOf("protocol" to 1, "type" to "audio",
            "reading_id" to old.readingId, "generation" to old.generation, "audio_id" to "old", "content_type" to "audio/mpeg")))
        val effects = c.dispatch(TtsCommand.ServerFrame("resumed", mapOf("protocol" to 1, "type" to "state",
            "revision" to 20, "ack_seq" to resume["seq"], "listening" to null)))
        assertTrue(c.snapshot().listening!!.paused)
        assertNull(c.snapshot().playback.readingId)
        assertTrue(effects.none { it is TtsEffect.Prepare || it is TtsEffect.StopService })
    }
    @Test fun staleTimelineNotificationActionsCannotChangeNewActivationOrManualPlayback() {
        val c = controller(); listen(c); opened(c)
        val old = c.snapshot().listening!!.activationId
        c.dispatch(TtsCommand.PauseTimeline(old))
        manual(c)
        assertTrue(c.dispatch(TtsCommand.PauseTimeline(old)).isEmpty())
        assertEquals("manual", c.snapshot().playback.operationId)
        c.dispatch(TtsCommand.ResumeTimeline(old, "2026-11-05T13:00:00.000000Z"))
        val before = c.snapshot()
        assertTrue(c.dispatch(TtsCommand.ResumeTimeline(old, "2026-11-05T14:00:00.000000Z")).isEmpty())
        assertTrue(c.dispatch(TtsCommand.PauseTimeline(old)).isEmpty())
        assertEquals(before, c.snapshot())
        c.dispatch(TtsCommand.StopAll)
        assertTrue(c.dispatch(TtsCommand.ResumeTimeline(old, "2026-11-05T14:00:00.000000Z")).isEmpty())
    }
    @Test fun disablingPausedTimelineReleasesForegroundIntent() {
        for (stopAll in listOf(false, true)) {
            val c = controller(); listen(c); opened(c)
            val id = c.snapshot().listening!!.activationId
            c.dispatch(TtsCommand.PauseTimeline(id))
            val effects = c.dispatch(if (stopAll) TtsCommand.StopAll else TtsCommand.Disable(owner, id))
            assertNull(c.snapshot().listening)
            val timer = effects.filterIsInstance<TtsEffect.StopAfter>().single()
            assertTrue(c.dispatch(TtsCommand.StopTimeout(timer.epoch)).any { it is TtsEffect.StopService })
        }
    }
    @Test fun delayedListenerErrorsCannotDiscardPauseOrItsManualReading() {
        for (code in listOf("scope_unavailable", "catchup_incomplete", "tts_unavailable")) {
            for (viaState in listOf(false, true)) {
                val c = controller(); listen(c); opened(c)
                val id = c.snapshot().listening!!.activationId
                val pause = c.dispatch(TtsCommand.PauseTimeline(id))
                val pauseSeq = pause.filterIsInstance<TtsEffect.Send>().last().frame["seq"]
                val failure = mapOf("code" to code, "message" to "Old listener failed")
                val frame = if (viaState) mapOf("protocol" to 1, "type" to "state", "revision" to 10,
                    "ack_seq" to pauseSeq, "listening" to null, "error" to failure)
                    else mapOf("protocol" to 1, "type" to "error") + failure
                c.dispatch(TtsCommand.ServerFrame("socket", frame))
                assertTrue("$code via state=$viaState retains Resume", c.snapshot().listening?.paused == true)
                assertNull(c.snapshot().error)
                manual(c); audio(c)
                val reading = c.snapshot().playback
                c.dispatch(TtsCommand.ServerFrame("socket", frame + mapOf("revision" to 11)))
                assertEquals(reading, c.snapshot().playback)
                assertTrue(c.snapshot().listening!!.paused)
                c.dispatch(TtsCommand.TransportLost("socket"))
                val resume = c.dispatch(TtsCommand.Reconnect).filterIsInstance<TtsEffect.Connect>().single().handshake
                c.dispatch(TtsCommand.TransportOpened("resumed"))
                c.dispatch(TtsCommand.ServerFrame("resumed", mapOf("protocol" to 1, "type" to "state",
                    "revision" to 20, "ack_seq" to resume["seq"], "listening" to null, "error" to failure)))
                assertEquals(reading, c.snapshot().playback)
                assertTrue(c.snapshot().listening!!.paused)
                assertNull(c.snapshot().error)
            }
        }
    }
    @Test fun resumeDoesNotStopOnPreActivationBackendAcknowledgement() {
        val c = controller(); listen(c); opened(c)
        val id = c.snapshot().listening!!.activationId
        c.dispatch(TtsCommand.PauseTimeline(id))
        val frame = c.dispatch(TtsCommand.ResumeTimeline(id, "2026-11-05T14:00:00.000000Z"))
            .filterIsInstance<TtsEffect.Send>().single().frame
        val effects = c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "state",
            "revision" to 30, "ack_seq" to frame["seq"], "listening" to null)))
        assertNotNull(c.snapshot().listening)
        assertTrue(effects.none { it is TtsEffect.StopAfter || it is TtsEffect.StopService })
        assertTrue(candidate(c, 2).any { it is TtsEffect.Send && it.frame["type"] == "select" })
    }
    @Test fun oldListenerErrorCannotStopResumedActivationButItsOwnErrorCan() {
        val c = controller(); listen(c); opened(c)
        val old = c.snapshot().listening!!.activationId
        c.dispatch(TtsCommand.PauseTimeline(old))
        val frame = c.dispatch(TtsCommand.ResumeTimeline(old, "2026-11-05T14:00:00.000000Z"))
            .filterIsInstance<TtsEffect.Send>().single().frame
        val current = c.snapshot().listening!!
        val failure = mapOf("code" to "scope_unavailable", "message" to "Old scope disappeared", "activation_id" to old)
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error") + failure))
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "state",
            "revision" to 30, "ack_seq" to frame["seq"], "listening" to null, "error" to failure)))
        assertEquals(current, c.snapshot().listening)
        assertNull(c.snapshot().error)
        val effects = c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error",
            "code" to "scope_unavailable", "message" to "Current scope disappeared", "activation_id" to current.activationId)))
        assertNull(c.snapshot().listening)
        assertEquals("Current scope disappeared", c.snapshot().error!!["message"])
        assertTrue(effects.any { it is TtsEffect.StopAfter })
    }

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

    @Test fun notificationCancellationRetainsAbortedBeforeAndAfterStart() {
        for (started in listOf(false, true)) for (command in listOf(TtsCommand.Skip, TtsCommand.StopAll)) {
            val c = controller(); manual(c); opened(c)
            val reading = c.snapshot().playback
            if (started) c.dispatch(TtsCommand.PlayerFeedback(reading.readingId!!, reading.generation, PlayerSignal.Started))
            c.dispatch(command)
            val result = c.snapshot().results.single()
            assertEquals(mapOf("ok" to false, "aborted" to true), result.ended)
            assertEquals(if (started) mapOf("ok" to true) else result.ended, result.start)
        }
    }
    @Test fun readingErrorsRetainAutomaticOrManualOwnershipAfterPlaybackClears() {
        for (automatic in listOf(false, true)) {
            val c = controller(); listen(c); opened(c)
            if (automatic) c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "candidate", "activation_id" to c.snapshot().listening!!.activationId, "event_id" to 5)))
            else manual(c)
            val reading = c.snapshot().playback
            c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error", "reading_id" to reading.readingId,
                "generation" to reading.generation, "code" to "failed", "message" to "Preparation failed")))
            assertEquals("idle", c.snapshot().playback.status)
            assertEquals(automatic, c.snapshot().error!!["automatic"])
            assertEquals(if (automatic) null else "manual", c.snapshot().error!!["operation_id"])
        }
    }
    @Test fun summaryMismatchRemainsInTheAffectedOperationAfterTheNextAutomaticReading() {
        val c = controller(); listen(c); opened(c); manual(c)
        val reading = c.snapshot().playback
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error", "reading_id" to reading.readingId,
            "generation" to reading.generation, "code" to "summary_changed", "message" to "Changed", "summary_changed" to true, "saved_id" to 7)))
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "candidate", "activation_id" to c.snapshot().listening!!.activationId, "event_id" to 5)))
        val result = c.snapshot().results.single()
        assertEquals(true, result.start["summaryChanged"])
        assertEquals(7, result.start["savedId"])
    }

    @Test fun automaticErrorSurvivesRawStateNextCandidateAndAcknowledgedErrorClearBeforePoll() {
        val c = controller(); listen(c); opened(c)
        fun candidate(id: Int, text: String): List<TtsEffect> = c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1,
            "type" to "candidate", "activation_id" to c.snapshot().listening!!.activationId, "event_id" to id, "text" to text)))
        val firstEffects = candidate(1, "First")
        val first = c.snapshot().playback
        val failure = mapOf("code" to "failed", "message" to "Automatic preparation failed", "reading_id" to first.readingId, "generation" to first.generation)
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error") + failure))
        val firstSeq = firstEffects.filterIsInstance<TtsEffect.Send>().last().frame["seq"]
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "state", "revision" to 1, "ack_seq" to firstSeq, "error" to failure)))
        val nextEffects = candidate(2, "Second")
        val nextSeq = nextEffects.filterIsInstance<TtsEffect.Send>().last().frame["seq"]
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "state", "revision" to 2, "ack_seq" to nextSeq, "error" to null)))
        val snapshot = c.snapshot()
        assertNull(snapshot.error); assertEquals("Second", snapshot.playback.text)
        val retained = snapshot.results.single()
        assertTrue(retained.automatic); assertEquals(first.readingId, retained.readingId); assertEquals(first.generation, retained.generation)
        assertEquals("Automatic preparation failed", retained.ended["error"])
        assertEquals(false, retained.ended["ok"])
    }
    @Test fun automaticTerminalFailuresShareTheExistingSixtyFourResultBound() {
        val c = controller(); listen(c); opened(c)
        val ids = mutableListOf<String>()
        repeat(66) { id ->
            c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "candidate",
                "activation_id" to c.snapshot().listening!!.activationId, "event_id" to id)))
            val reading = c.snapshot().playback
            ids += reading.readingId!!
            c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error", "code" to "failed", "message" to "Failed",
                "reading_id" to reading.readingId, "generation" to reading.generation)))
        }
        assertEquals(64, c.snapshot().results.size)
        assertEquals(ids[2], c.snapshot().results.first().readingId)
    }

    private fun candidate(c: TtsController, id: Int = 1) = c.dispatch(TtsCommand.ServerFrame("socket",
        mapOf("protocol" to 1, "type" to "candidate", "activation_id" to c.snapshot().listening!!.activationId, "event_id" to id)))
    private fun rejected(p: NativePlayback) = mapOf("protocol" to 1, "type" to "selection_rejected",
        "reading_id" to p.readingId, "generation" to p.generation)
    @Test fun rejectedSelectionReleasesOnlyItsUnopenedAutomaticReservation() {
        val c = controller(); listen(c); opened(c); candidate(c)
        val old = c.snapshot().playback
        c.dispatch(TtsCommand.ServerFrame("socket", rejected(old)))
        assertNull(c.snapshot().playback.readingId)
        assertNull(c.snapshot().error); assertTrue(c.snapshot().results.isEmpty())
        assertNotNull(c.snapshot().listening)
        candidate(c, 2)
        assertNotEquals(old.readingId, c.snapshot().playback.readingId)
        assertTrue(audio(c).any { it is TtsEffect.Prepare })
    }
    @Test fun staleSelectionDispositionsPreserveManualSkipNewActivationAndOpenedAudio() {
        for (replacement in listOf("manual", "skip", "activation", "audio", "generation", "connection")) {
            val c = controller(); listen(c); opened(c); candidate(c)
            val old = c.snapshot().playback
            when (replacement) {
                "manual" -> manual(c)
                "skip" -> { c.dispatch(TtsCommand.Skip); candidate(c, 2) }
                "activation" -> { listen(c); candidate(c, 2) }
                "audio" -> audio(c)
                "connection" -> c.dispatch(TtsCommand.TransportOpened("other"))
            }
            val before = c.snapshot().playback
            val frame = if (replacement == "generation") rejected(old) + mapOf("generation" to old.generation - 1) else rejected(old)
            c.dispatch(TtsCommand.ServerFrame("socket", frame))
            assertEquals(replacement, before, c.snapshot().playback)
        }
    }
    @Test fun ttsUnavailableStopsListenerWithoutCancellingManualAudio() {
        val c = controller(); listen(c); opened(c); manual(c); audio(c)
        val before = c.snapshot().playback
        val effects = c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error",
            "code" to "tts_unavailable", "message" to "Configuration is unavailable")))
        assertEquals(before, c.snapshot().playback); assertNull(c.snapshot().listening)
        assertTrue(effects.none { it is TtsEffect.Cancel || it is TtsEffect.Release })
        assertTrue(c.snapshot().results.isEmpty())
    }
    @Test fun delayedAutomaticErrorDoesNotFailManualReplacement() {
        val c = controller(); listen(c); opened(c); candidate(c)
        val old = c.snapshot().playback
        manual(c); audio(c)
        val before = c.snapshot().playback
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error",
            "code" to "summary_unavailable", "message" to "Summary is unavailable", "reading_id" to old.readingId, "generation" to old.generation)))
        assertEquals(before, c.snapshot().playback); assertNull(c.snapshot().error)
        c.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "state", "revision" to 5,
            "ack_seq" to 4, "error" to mapOf("code" to "summary_unavailable", "message" to "Late failure",
                "reading_id" to old.readingId, "generation" to old.generation))))
        assertEquals(before, c.snapshot().playback); assertNull(c.snapshot().error)
        assertTrue(c.snapshot().results.isEmpty())
    }

    @Test fun silenceFactUsesStrictMonotonicAudibleEndBeforeEndingSettlement() {
        for ((delay, reset) in listOf(180000L to false, 180001L to true)) {
            var now = 1000L
            val c = TtsController("instance", elapsedMs = { now }); c.dispatch(TtsCommand.AttachDocument(owner))
            listen(c); opened(c); candidate(c)
            val reading = c.snapshot().playback
            c.dispatch(TtsCommand.PlayerFeedback(reading.readingId!!, reading.generation, PlayerSignal.Started))
            now = 90000L
            c.dispatch(TtsCommand.PlayerFeedback(reading.readingId, reading.generation, PlayerSignal.Settling))
            now += delay // The ending HTTP result stays blocked throughout this wait.
            c.dispatch(TtsCommand.PlayerFeedback(reading.readingId, reading.generation, PlayerSignal.Ended(true)))
            val selected = candidate(c, 2).filterIsInstance<TtsEffect.Send>().single().frame
            assertEquals(reset, selected["silence_reset"])
            now += 300000L
            c.dispatch(TtsCommand.PlayerFeedback(reading.readingId, reading.generation, PlayerSignal.Settling))
            val resume = c.dispatch(TtsCommand.Reconnect).filterIsInstance<TtsEffect.Connect>().single().handshake
            val retained = (resume["commands"] as List<*>).filterIsInstance<Map<*, *>>().last { it["type"] == "select" }
            assertEquals(selected, retained) // Preparation and replay preserve the selected fact.
        }
    }
    @Test fun inaudibleOrStalePlayerSettlementCannotResetSilenceClock() {
        var now = 1000L
        val c = TtsController("instance", elapsedMs = { now }); c.dispatch(TtsCommand.AttachDocument(owner))
        listen(c); opened(c); candidate(c)
        val first = c.snapshot().playback
        c.dispatch(TtsCommand.PlayerFeedback(first.readingId!!, first.generation, PlayerSignal.Started))
        c.dispatch(TtsCommand.PlayerFeedback(first.readingId, first.generation, PlayerSignal.Settling))
        c.dispatch(TtsCommand.PlayerFeedback(first.readingId, first.generation, PlayerSignal.Ended(true)))
        candidate(c, 2); val inaudible = c.snapshot().playback
        now += 180001L
        c.dispatch(TtsCommand.PlayerFeedback(first.readingId, first.generation, PlayerSignal.Settling))
        c.dispatch(TtsCommand.PlayerFeedback(inaudible.readingId!!, inaudible.generation, PlayerSignal.Settling))
        c.dispatch(TtsCommand.PlayerFeedback(inaudible.readingId, inaudible.generation, PlayerSignal.Ended(true)))
        val next = candidate(c, 3).filterIsInstance<TtsEffect.Send>().single().frame
        assertEquals(true, next["silence_reset"])
    }

}
