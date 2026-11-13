package com.twidi.twicc.tts

import org.junit.Assert.*
import org.junit.Test

class TtsRecoveryTest {
    @Test fun onlyExplicitUserActivationCarriesTelemetryEpoch() {
        val h = Harness()
        val epoch = "a".repeat(64)
        val explicit = h.controller.dispatch(TtsCommand.Listen(h.owner, ListeningScope("global", null), false,
            "2026-11-05T12:00:00.000000Z", telemetryEpoch = epoch))
        assertEquals(epoch, listen(explicit)["telemetry_epoch"])
        val activationId = h.controller.snapshot().listening!!.activationId
        h.controller.dispatch(TtsCommand.PauseTimeline(activationId))
        val resumed = h.controller.dispatch(TtsCommand.ResumeTimeline(activationId, "2026-11-05T12:00:01.000000Z"))
        assertFalse(listen(resumed).containsKey("telemetry_epoch"))
        h.lost(); h.now = 120000
        val handshake = h.reconnect()
        assertFalse(listen(h.state("recovery", handshake["seq"])).containsKey("telemetry_epoch"))
    }

    @Test fun replacementKeepsFinalBoundaryAndOwnedBacklogWithoutAdvancingOrdinaryCheckpoint() {
        val h = Harness()
        val activation = h.controller.snapshot().listening!!.activationId
        h.controller.dispatch(TtsCommand.ServerFrame("initial", mapOf("protocol" to 1, "type" to "state",
            "revision" to 1L, "ack_seq" to 2L, "listening" to mapOf("activation_id" to activation,
                "final_boundary" to "2026-11-05T12:00:00.000000Z", "ending_cursor" to 900L,
                "ordinary_after_id" to 899L, "pending_final_ending_ids" to listOf(12L, 31L)))))
        h.controller.dispatch(TtsCommand.ServerFrame("initial", mapOf("protocol" to 1, "type" to "candidate",
            "activation_id" to activation, "event_id" to 7L, "text" to "Final", "final_ending_id" to 31L,
            "occurred_at" to "2026-11-05T12:10:00.000000Z", "final_occurred_at" to "2026-11-05T12:11:00.000000Z")))
        val reading = h.controller.snapshot().playback
        h.controller.dispatch(TtsCommand.PlayerFeedback(reading.readingId!!, reading.generation, PlayerSignal.Started))
        h.lost(); h.now = 60000; h.reconnect()
        h.controller.dispatch(TtsCommand.ServerFrame("recovery", mapOf("protocol" to 1, "type" to "error", "code" to "session_lost")))
        h.controller.dispatch(TtsCommand.TransportOpened("new"))
        val sent = h.opened("new").filterIsInstance<TtsEffect.Send>().filter { it.frame["type"] == "listen" }
        assertTrue(sent.isNotEmpty())
        for (value in sent.map { it.frame }) {
            assertEquals("2026-11-05T12:00:00.000000Z", value["boundary"])
            assertEquals("2026-11-05T12:00:00.000000Z", value["final_boundary"])
            assertEquals(900L, value["ending_cursor"])
            assertEquals(899L, value["ordinary_after_id"])
            assertEquals(listOf(12L, 31L), value["pending_final_ending_ids"])
            assertEquals(listOf(7L), value["excluded_event_ids"])
        }
    }

    @Test fun obsoleteActivationCannotReplaceRecoveryMetadata() {
        val h = Harness()
        h.controller.dispatch(TtsCommand.ServerFrame("initial", mapOf("protocol" to 1, "type" to "state",
            "revision" to 1L, "ack_seq" to 2L, "listening" to mapOf("activation_id" to "old",
                "final_boundary" to "2026-11-05T11:00:00.000000Z", "ending_cursor" to 900L,
                "ordinary_after_id" to 899L, "pending_final_ending_ids" to listOf(12L)))))
        h.lost(); h.now = 60000; h.reconnect()
        h.controller.dispatch(TtsCommand.ServerFrame("recovery", mapOf("protocol" to 1, "type" to "error", "code" to "session_lost")))
        h.controller.dispatch(TtsCommand.TransportOpened("new"))
        val sent = h.opened("new").filterIsInstance<TtsEffect.Send>().filter { it.frame["type"] == "listen" }
        assertTrue(sent.isNotEmpty())
        for (value in sent.map { it.frame }) {
            assertEquals("2026-11-05T12:00:00.000000Z", value["final_boundary"])
            assertEquals(emptyList<Long>(), value["pending_final_ending_ids"])
        }
    }

    @Test fun ordinaryConsumedIdsSurviveCheckpointPruningAndFinalModeReplacement() {
        val h = Harness()
        for ((id, occurred) in listOf(7L to "2026-11-05T12:01:00.000000Z", 8L to "2026-11-05T12:10:00.000000Z")) {
            h.candidate(id, occurred)
            val reading = h.controller.snapshot().playback
            h.controller.dispatch(TtsCommand.PlayerFeedback(reading.readingId!!, reading.generation, PlayerSignal.Started))
            h.controller.dispatch(TtsCommand.PlayerFeedback(reading.readingId, reading.generation, PlayerSignal.Ended(true)))
        }
        h.lost(); h.now = 60000; h.reconnect()
        h.controller.dispatch(TtsCommand.ServerFrame("recovery", mapOf("protocol" to 1, "type" to "error", "code" to "session_lost")))
        h.controller.dispatch(TtsCommand.TransportOpened("new"))
        val sent = h.opened("new").filterIsInstance<TtsEffect.Send>().filter { it.frame["type"] == "listen" }
        assertTrue(sent.isNotEmpty())
        for (value in sent.map { it.frame }) {
            assertEquals(listOf(7L, 8L), value["excluded_event_ids"])
            assertEquals("2026-11-05T12:09:54.999999Z", value["boundary"])
            assertEquals("2026-11-05T12:00:00.000000Z", value["final_boundary"])
        }
    }

    @Test fun resumeAndLongOutageClearFinalBacklogAndCutoff() {
        for (paused in listOf(false, true)) {
            val h = Harness()
            val activation = h.controller.snapshot().listening!!.activationId
            h.controller.dispatch(TtsCommand.ServerFrame("initial", mapOf("protocol" to 1, "type" to "state",
                "revision" to 1L, "ack_seq" to 2L, "listening" to mapOf("activation_id" to activation,
                    "final_boundary" to "2026-11-05T12:00:00.000000Z", "ending_cursor" to 900L,
                    "ordinary_after_id" to 899L, "pending_final_ending_ids" to listOf(12L),
                    "excluded_event_ids" to listOf(7L)))))
            val value = if (paused) {
                h.controller.dispatch(TtsCommand.PauseTimeline(activation))
                listen(h.controller.dispatch(TtsCommand.ResumeTimeline(activation, "2026-11-05T12:30:00.000000Z")))
            } else {
                h.lost(); h.now = 120000; val handshake = h.reconnect()
                listen(h.state("recovery", handshake["seq"]))
            }
            assertEquals(emptyList<Long>(), value["pending_final_ending_ids"])
            assertEquals(emptyList<Long>(), value["excluded_event_ids"])
            assertEquals(0L, value["ending_cursor"])
            assertEquals(0L, value["ordinary_after_id"])
            if (paused) assertEquals("2026-11-05T12:30:00.000000Z", value["final_boundary"])
            else assertEquals(true, value["fresh"])
        }
    }

    @Test fun expiredSessionAfterLongOutageNeverRestoresOldFinalBacklogBeforeFreshReset() {
        val h = Harness()
        val activation = h.controller.snapshot().listening!!.activationId
        h.controller.dispatch(TtsCommand.ServerFrame("initial", mapOf("protocol" to 1, "type" to "state",
            "revision" to 1L, "ack_seq" to 2L, "listening" to mapOf("activation_id" to activation,
                "pending_final_ending_ids" to listOf(12L), "ending_cursor" to 900L, "ordinary_after_id" to 899L))))
        h.lost(); h.now = 120000; h.reconnect()
        h.controller.dispatch(TtsCommand.ServerFrame("recovery", mapOf("protocol" to 1, "type" to "error", "code" to "session_lost")))
        h.controller.dispatch(TtsCommand.TransportOpened("new"))
        val sent = h.opened("new").filterIsInstance<TtsEffect.Send>().filter { it.frame["type"] == "listen" }
        assertTrue(sent.isNotEmpty())
        assertTrue(sent.all { it.frame["fresh"] == true })
        assertTrue(sent.all { it.frame["pending_final_ending_ids"] == emptyList<Long>() })
    }

    private class Harness {
        var now = 0L
        val owner = DocumentOwner("instance", "page", 1)
        val controller = TtsController("instance", elapsedMs = { now }, recoveryAnnouncement = { "Localized notice" })
        init {
            controller.dispatch(TtsCommand.AttachDocument(owner))
            controller.dispatch(TtsCommand.Listen(owner, ListeningScope("global", null), false, "2026-11-05T12:00:00.000000Z"))
            controller.dispatch(TtsCommand.TransportOpened("initial"))
            opened("initial")
        }
        fun opened(id: String): List<TtsEffect> = controller.dispatch(TtsCommand.ServerFrame(id,
            mapOf("protocol" to 1, "type" to "opened_session", "session_id" to "session", "resume_secret" to "secret", "ack_seq" to 1)))
        fun lost(id: String = "initial") = controller.dispatch(TtsCommand.TransportLost(id))
        fun reconnect(id: String = "recovery"): TtsFrame {
            val handshake = controller.dispatch(TtsCommand.Reconnect).filterIsInstance<TtsEffect.Connect>().single().handshake
            controller.dispatch(TtsCommand.TransportOpened(id))
            return handshake
        }
        fun state(id: String, seq: Any?, revision: Long = 100, boundary: String? = null): List<TtsEffect> = controller.dispatch(TtsCommand.ServerFrame(id,
            mapOf("protocol" to 1, "type" to "state", "revision" to revision, "ack_seq" to seq, "listening" to mapOf("activation_id" to controller.snapshot().listening?.activationId, "boundary" to boundary))))
        fun candidate(eventId: Long, occurred: String = "2026-11-05T12:01:00.000000Z") = controller.dispatch(TtsCommand.ServerFrame("initial",
            mapOf("protocol" to 1, "type" to "candidate", "activation_id" to controller.snapshot().listening!!.activationId,
                "event_id" to eventId, "occurred_at" to occurred, "text" to "Hello")))
    }
    private fun listen(effects: List<TtsEffect>) = effects.filterIsInstance<TtsEffect.Send>().single { it.frame["type"] == "listen" }.frame

    @Test fun shortOutageCatchesUpAndLongOutagesResetWithOneLocalizedNotice() {
        for (duration in listOf(60000L, 120000L, 600000L, 1800000L)) {
            val h = Harness(); h.candidate(1); h.lost(); h.now = duration
            val handshake = h.reconnect()
            val reset = listen(h.state("recovery", handshake["seq"]))
            assertEquals(duration >= 120000L, reset["fresh"])
            assertEquals("reconnecting", h.controller.snapshot().sessionStatus)
            if (duration >= 120000L) {
                assertEquals("Localized notice", (reset["notice"] as Map<*, *>)["text"])
                assertEquals("recovery", h.controller.snapshot().playback.source)
            } else assertNull(reset["notice"])
            val done = h.state("recovery", reset["seq"], 101)
            assertTrue(done.filterIsInstance<TtsEffect.Send>().none { it.frame["type"] == "listen" })
            assertEquals("connected", h.controller.snapshot().sessionStatus)
            assertNotNull(h.controller.snapshot().listening)
        }
    }
    @Test fun reconciliationThatCrossesThresholdEscalatesEvenAfterTcpOpen() {
        val h = Harness(); h.candidate(1); h.lost(); h.now = 119000
        val handshake = h.reconnect()
        val short = listen(h.state("recovery", handshake["seq"]))
        assertEquals(false, short["fresh"])
        h.now = 120000
        val long = listen(h.state("recovery", short["seq"], 101))
        assertEquals(true, long["fresh"]); assertNotNull(long["notice"])
    }
    @Test fun expiredSessionReopensAndCheckpointsStartedMessagesOnly() {
        val h = Harness(); h.candidate(7)
        val reading = h.controller.snapshot().playback
        h.controller.dispatch(TtsCommand.PlayerFeedback(reading.readingId!!, reading.generation, PlayerSignal.Started))
        h.lost(); h.now = 60000
        h.reconnect()
        val effects = h.controller.dispatch(TtsCommand.ServerFrame("recovery", mapOf("protocol" to 1, "type" to "error", "code" to "session_lost")))
        assertEquals("open", effects.filterIsInstance<TtsEffect.Connect>().single().handshake["type"])
        assertTrue(effects.none { it is TtsEffect.StopService })
        h.controller.dispatch(TtsCommand.TransportOpened("new"))
        val replacement = h.opened("new").filterIsInstance<TtsEffect.Send>().filter { it.frame["type"] == "listen" }
        assertTrue(replacement.isNotEmpty())
        assertTrue(replacement.any { it.frame["excluded_event_ids"] == listOf(7L) })
        assertTrue(replacement.any { it.frame["boundary"] == "2026-11-05T12:00:54.999999Z" })
    }
    @Test fun lateStartedMessageCannotRegressCheckpointOrReplayEarlierStartedMessages() {
        val h = Harness()
        for ((id, timestamp) in listOf(7L to "2026-11-05T12:01:03.000000Z", 8L to "2026-11-05T12:01:01.000000Z")) {
            h.candidate(id, timestamp)
            val reading = h.controller.snapshot().playback
            h.controller.dispatch(TtsCommand.PlayerFeedback(reading.readingId!!, reading.generation, PlayerSignal.Started))
            h.controller.dispatch(TtsCommand.PlayerFeedback(reading.readingId, reading.generation, PlayerSignal.Ended(true)))
        }
        h.lost(); h.now = 60000; h.reconnect()
        h.controller.dispatch(TtsCommand.ServerFrame("recovery", mapOf("protocol" to 1, "type" to "error", "code" to "session_lost")))
        h.controller.dispatch(TtsCommand.TransportOpened("new"))
        val sent = h.opened("new").filterIsInstance<TtsEffect.Send>().filter { it.frame["type"] == "listen" }
        assertTrue(sent.all { it.frame["excluded_event_ids"] == listOf(7L, 8L) })
        assertTrue(sent.all { it.frame["boundary"] == "2026-11-05T12:00:57.999999Z" })
    }
    @Test fun pauseDefersNoticeUntilResumeAndStopCancelsRecovery() {
        val h = Harness(); h.lost(); h.now = 1800000
        h.controller.dispatch(TtsCommand.PauseTimeline(h.controller.snapshot().listening!!.activationId))
        val handshake = h.reconnect()
        assertTrue(h.state("recovery", handshake["seq"]).filterIsInstance<TtsEffect.Send>().none { it.frame["notice"] != null })
        val resumed = listen(h.controller.dispatch(TtsCommand.ResumeTimeline(h.controller.snapshot().listening!!.activationId,
            "2026-11-05T12:30:00.000000Z")))
        assertNotNull(resumed["notice"])
        h.controller.dispatch(TtsCommand.StopAll)
        assertNull(h.controller.snapshot().listening)
        assertNull(h.controller.snapshot().playback.readingId)
        assertTrue(h.state("recovery", resumed["seq"], 101).filterIsInstance<TtsEffect.Send>().none { it.frame["notice"] != null })
    }
    @Test fun flappingTransportKeepsOriginalOutageAndRejectsOldAudio() {
        val h = Harness(); h.candidate(8); val old = h.controller.snapshot().playback
        h.lost(); h.now = 60000; h.reconnect("flap"); h.lost("flap")
        h.now = 120000; val handshake = h.reconnect()
        val reset = listen(h.state("recovery", handshake["seq"]))
        assertNotNull(reset["notice"])
        val stale = h.controller.dispatch(TtsCommand.ServerFrame("initial", mapOf("protocol" to 1, "type" to "audio",
            "reading_id" to old.readingId, "generation" to old.generation, "audio_id" to "old", "content_type" to "audio/mpeg")))
        assertTrue(stale.isEmpty())
    }
    @Test fun lostNoticeBeforeListenerAckPreservesShortBacklogAndResetsNewLongOutage() {
        for (started in listOf(false, true)) for (duration in listOf(1000L, 120000L)) {
            val h = Harness(); h.lost(); h.now = 600000
            val handshake = h.reconnect(); listen(h.state("recovery", handshake["seq"]))
            val original = h.controller.snapshot().playback
            if (started) h.controller.dispatch(TtsCommand.PlayerFeedback(original.readingId!!, original.generation, PlayerSignal.Started))
            h.lost("recovery"); h.now += duration
            val resumed = h.reconnect("again")
            val acceptedBoundary = "2026-11-05T12:10:00.000000Z"
            val effects = h.state("again", resumed["seq"], 101, boundary = acceptedBoundary)
            val notices = effects.filterIsInstance<TtsEffect.Send>().filter { it.frame["type"] == "listen" && it.frame["notice"] != null }
            val needsNotice = !started || duration >= 120000L
            assertEquals(if (needsNotice) 1 else 0, notices.size)
            if (needsNotice) {
                assertEquals(duration >= 120000L, notices.single().frame["fresh"])
                if (duration < 120000L) assertEquals(acceptedBoundary, notices.single().frame["boundary"])
                assertTrue(h.controller.snapshot().playback.generation > original.generation)
                val completed = h.state("again", notices.single().frame["seq"], 102)
                assertTrue(completed.filterIsInstance<TtsEffect.Send>().none { it.frame["type"] == "listen" })
            }
        }
    }
    @Test fun freshServerBoundaryPersistsAcrossLaterSessionReplacement() {
        val h = Harness(); h.lost(); h.now = 600000
        val handshake = h.reconnect(); val reset = listen(h.state("recovery", handshake["seq"]))
        val boundary = "2026-11-05T12:10:00.000000Z"
        h.controller.dispatch(TtsCommand.ServerFrame("recovery", mapOf("protocol" to 1, "type" to "state",
            "revision" to 101, "ack_seq" to reset["seq"], "listening" to mapOf(
                "activation_id" to h.controller.snapshot().listening!!.activationId, "boundary" to boundary))))
        assertEquals(boundary, h.controller.snapshot().listening!!.boundary)
        h.lost("recovery"); h.now += 60000; h.reconnect("replacement")
        h.controller.dispatch(TtsCommand.ServerFrame("replacement", mapOf("protocol" to 1, "type" to "error", "code" to "session_lost")))
        h.controller.dispatch(TtsCommand.TransportOpened("new"))
        val sent = h.opened("new").filterIsInstance<TtsEffect.Send>().filter { it.frame["type"] == "listen" }
        assertTrue(sent.all { it.frame["boundary"] == boundary })
    }
    @Test fun acknowledgementOfPreviousListenerCannotFinishRecovery() {
        val h = Harness(); h.lost(); h.now = 600000
        val handshake = h.reconnect(); val reset = listen(h.state("recovery", handshake["seq"]))
        h.controller.dispatch(TtsCommand.ServerFrame("recovery", mapOf("protocol" to 1, "type" to "state",
            "revision" to 101, "ack_seq" to reset["seq"], "listening" to null)))
        assertEquals("reconnecting", h.controller.snapshot().sessionStatus)
        h.state("recovery", reset["seq"], 102)
        assertEquals("connected", h.controller.snapshot().sessionStatus)
    }
    @Test fun failedNoticeDoesNotDisableListenerOrRepeatNotice() {
        val h = Harness(); h.lost(); h.now = 600000
        val handshake = h.reconnect(); val reset = listen(h.state("recovery", handshake["seq"]))
        h.state("recovery", reset["seq"], 101)
        val notice = h.controller.snapshot().playback
        val effects = h.controller.dispatch(TtsCommand.ServerFrame("recovery", mapOf("protocol" to 1, "type" to "error",
            "code" to "speech_failed", "reading_id" to notice.readingId, "generation" to notice.generation, "message" to "Failed")))
        assertNotNull(h.controller.snapshot().listening)
        assertNull(h.controller.snapshot().playback.readingId)
        assertTrue(effects.none { it is TtsEffect.StopService })
    }
}
