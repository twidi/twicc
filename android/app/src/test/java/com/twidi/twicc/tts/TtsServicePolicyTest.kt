package com.twidi.twicc.tts

import org.junit.Assert.*
import org.junit.Test

class TtsServicePolicyTest {
    private class Harness(val cache: TtsSnapshotCache = TtsSnapshotCache()) {
        val events = mutableListOf<String>()
        var blocked: TtsActionError? = null
        var wake = false
        var foreground = false
        var stopped = false
        val timers = mutableListOf<Pair<Long, () -> Unit>>()
        var exported: ((Result<List<TtsCookie>>) -> Unit)? = null
        val socket = Socket()
        val platform = object : TtsServicePlatform {
            override fun notificationError() = blocked
            override fun foreground(snapshot: NativeTtsSnapshot, epoch: String) { events += "foreground"; foreground = true }
            override fun update(snapshot: NativeTtsSnapshot, epoch: String) { events += "notification" }
            override fun wakeLock(held: Boolean) { wake = held }
            override fun stop() { foreground = false; stopped = true }
            override fun enqueue(block: () -> Unit) = block()
            override fun after(delayMillis: Long, block: () -> Unit) { timers += delayMillis to block }
            override fun worker(block: () -> Unit) = block()
        }
        val owner = TtsServiceOwner(platform, { events += "connection"; socket },
            { object : TtsPlaybackOutput {
                override fun prepare(resource: AudioResource, connection: TtsTransport, feedback: (PlayerSignal) -> Unit) { events += "prepare" }
                override fun play(readingId: String, generation: Long) {}
                override fun cancel(readingId: String, generation: Long) { events += "cancel" }
                override fun close() {}
            } }, cache)
        val document = DocumentOwner("instance", "page", 1)
        fun configure() {
            owner.configure(TtsActivationSource("instance", "Test", "https://example.test") { _, callback ->
                events += "export"; exported = callback
            })
        }
        init { configure(); owner.dispatch(TtsCommand.AttachDocument(document)) }
        fun manual(id: String = "operation"): Acceptance { configure(); return owner.dispatch(TtsCommand.Manual(document, id,
            mapOf("kind" to "text", "source" to "message", "text" to "Hello", "identity" to null,
                "config_id" to "config", "speech" to mapOf("text" to "Hello")))) }
        fun listen() = owner.dispatch(TtsCommand.Listen(document, ListeningScope("global", null), false, "2026-11-05T10:00:00.000000Z"))
        fun connect() {
            exported!!(Result.success(emptyList()))
            socket.event(TtsConnectionEvent.Opened("connection"))
            socket.event(TtsConnectionEvent.Frame("connection", mapOf("protocol" to 1, "type" to "opened_session",
                "session_id" to "session", "resume_secret" to "secret", "revision" to 1)))
        }
    }
    private class Socket : TtsTransport {
        override var event: (TtsConnectionEvent) -> Unit = {}
        val sent = mutableListOf<TtsFrame>()
        var closed = false
        override fun connect(handshake: TtsFrame): String { sent += handshake; return "connection" }
        override fun send(frame: TtsFrame): Boolean { sent += frame; return true }
        override fun reconciled(connectionId: String) {}
        override fun cancelAudio(resource: AudioResource) {}
        override fun release(resource: AudioResource) {}
        override fun close() { closed = true }
        override val connection: TtsConnection? = null
    }
    @Test fun blockedNotificationChannelRejectsActivation() {
        val h = Harness(); h.blocked = TtsActionError("notifications_blocked", "Enable speech notifications", "notification_settings")
        val acceptance = h.manual()
        assertFalse(acceptance.accepted)
        assertEquals("notification_settings", acceptance.error!!.action)
        assertFalse(h.foreground); assertFalse(h.wake)
        assertFalse(h.events.contains("export")); assertFalse(h.events.contains("connection"))
        assertEquals("idle", h.owner.snapshot().sessionStatus)
    }
    @Test fun foregroundPrecedesAcceptedExportAndSurvivesDocumentReplacement() {
        val h = Harness(); assertTrue(h.manual().accepted)
        assertTrue(h.events.indexOf("foreground") < h.events.indexOf("export"))
        assertTrue(h.wake)
        h.owner.dispatch(TtsCommand.AttachDocument(h.document.copy(generation = 2)))
        h.connect()
        assertEquals("connected", h.owner.snapshot().sessionStatus)
        assertTrue(h.foreground)
    }
    @Test fun listeningWithoutExemptionKeepsForegroundControlsAndWakeLock() {
        val h = Harness()
        assertTrue(h.listen().accepted)
        assertTrue(h.foreground); assertTrue(h.wake)
        assertTrue(h.events.indexOf("foreground") < h.events.indexOf("export"))
        h.connect()
        assertNotNull(h.owner.snapshot().listening)
        h.socket.event(TtsConnectionEvent.Lost("connection", 1000))
        assertTrue(h.foreground); assertTrue(h.wake)
        h.owner.dispatch(TtsCommand.StopAll)
        assertFalse(h.wake)
        h.timers.last { it.first == 5000L }.second()
        assertTrue(h.stopped); assertFalse(h.foreground)
    }
    @Test fun silentListeningAndReconnectKeepForegroundAndWakeLock() {
        val h = Harness(); assertTrue(h.listen().accepted); h.connect()
        h.socket.event(TtsConnectionEvent.Lost("connection", 1000))
        assertEquals("reconnecting", h.owner.snapshot().sessionStatus)
        assertTrue(h.foreground); assertTrue(h.wake)
    }
    @Test fun stopAllDuringExportCannotOpenLateSessionAndStopsWithinFiveSeconds() {
        val h = Harness(); h.manual(); val oldExport = h.exported!!
        assertTrue(h.owner.dispatch(TtsCommand.StopAll).accepted)
        assertFalse(h.wake)
        val timer = h.timers.single { it.first == 5000L }
        timer.second()
        oldExport(Result.success(emptyList()))
        assertFalse(h.events.contains("connection")); assertTrue(h.stopped); assertFalse(h.foreground)
        assertEquals("stopped", h.owner.snapshot().sessionStatus)
    }
    @Test fun oldStopTimerAndNotificationCannotStopNewActivation() {
        val h = Harness(); h.manual(); val oldEpoch = h.owner.sessionEpoch!!
        h.owner.dispatch(TtsCommand.StopAll)
        val timer = h.timers.single { it.first == 5000L }.second
        timer(); h.manual("new-operation")
        assertNotEquals(oldEpoch, h.owner.sessionEpoch)
        timer()
        assertFalse(h.owner.notificationCommand(oldEpoch, TtsCommand.StopAll).accepted)
        assertEquals("new-operation", h.owner.snapshot().playback.operationId)
        assertTrue(h.foreground)
    }
    @Test fun controlsLostDuringHeartbeatStopAndRejectLateTransportCallbacks() {
        val h = Harness(); h.listen(); h.connect()
        val callback = h.socket.event
        h.blocked = TtsActionError("notifications_blocked", "Enable speech notifications", "notification_settings")
        callback(TtsConnectionEvent.HeartbeatDue("connection"))
        assertTrue(h.socket.closed); assertTrue(h.stopped); assertFalse(h.wake)
        callback(TtsConnectionEvent.Opened("connection"))
        assertEquals("error", h.owner.snapshot().sessionStatus)
    }
    @Test fun controlsDisabledDuringExportPreventOpeningTheSession() {
        val h = Harness(); h.manual()
        h.blocked = TtsActionError("notifications_blocked", "Enable speech notifications", "notification_settings")
        h.exported!!(Result.success(emptyList()))
        assertFalse(h.events.contains("connection"))
        assertFalse(h.foreground); assertFalse(h.wake)
        assertEquals("error", h.owner.snapshot().sessionStatus)
    }
    @Test fun instanceSwitchInvalidatesPendingExport() {
        val h = Harness(); h.manual(); val callback = h.exported!!
        h.owner.dispatch(TtsCommand.SwitchInstance("other"))
        callback(Result.success(emptyList()))
        assertFalse(h.events.contains("connection")); assertFalse(h.wake)
    }
    @Test fun switchingInstanceKeepsTerminalResultsWithTheirSourceInstance() {
        val h = Harness(); h.manual()
        h.owner.dispatch(TtsCommand.SwitchInstance("other"))
        assertEquals("other", h.owner.snapshot().instanceId)
        assertTrue(h.owner.snapshot().results.isEmpty())
        assertEquals("operation", h.cache.snapshot("instance").results.single().operationId)
        assertFalse(h.foreground)
    }
    @Test fun terminalResultsStayBoundedAcrossFreshActivations() {
        val h = Harness()
        repeat(66) { index ->
            h.manual("operation-$index")
            h.owner.dispatch(TtsCommand.StopAll)
            h.timers.last { it.first == 5000L }.second()
        }
        assertEquals(64, h.cache.snapshot("instance").results.size)
        assertEquals("operation-2", h.cache.snapshot("instance").results.first().operationId)
        assertEquals("operation-65", h.cache.snapshot("instance").results.last().operationId)
    }
    @Test fun delayedOldConnectionAndPlayerFeedbackCannotReviveCanceledWork() {
        val h = Harness(); h.manual(); h.connect()
        val callback = h.socket.event
        val reading = h.owner.snapshot().playback
        h.owner.dispatch(TtsCommand.StopAll)
        h.timers.last { it.first == 5000L }.second()
        h.manual("fresh")
        callback(TtsConnectionEvent.Frame("connection", mapOf("protocol" to 1, "type" to "opened_session", "session_id" to "old", "resume_secret" to "old")))
        h.owner.feedback(reading.readingId!!, reading.generation, PlayerSignal.Started)
        assertEquals("fresh", h.owner.snapshot().playback.operationId)
        assertFalse(h.owner.snapshot().playback.started)
        assertEquals("starting", h.owner.snapshot().sessionStatus)
    }

    @Test fun stoppedResultCacheSurvivesOwnerDestructionAndNextActivation() {
        val h = Harness(); h.manual(); h.connect()
        val reading = h.owner.snapshot().playback
        h.owner.feedback(reading.readingId!!, reading.generation, PlayerSignal.Started)
        h.owner.feedback(reading.readingId!!, reading.generation, PlayerSignal.Ended(true))
        assertTrue(h.foreground)
        h.timers.single { it.first == 5000L }.second()
        h.owner.destroy()
        assertEquals(true, h.cache.snapshot("instance").results.single().ended["ok"])
        assertNull(h.cache.snapshot("instance").listening)
        assertEquals("idle", h.cache.snapshot("instance").playback.status)
        val nextService = Harness(h.cache)
        nextService.manual("next")
        assertEquals("operation", nextService.owner.snapshot().results.single().operationId)
        assertEquals("next", nextService.owner.snapshot().playback.operationId)
    }
    @Test fun backgroundListeningFailureSurvivesAttachmentUntilExplicitActivation() {
        val first = Harness()
        assertTrue(first.listen().accepted)
        first.connect()
        val message = "Authentication expired. Open TwiCC and sign in again."
        first.owner.dispatch(TtsCommand.AuthorizationLost(message))
        first.owner.destroy()
        val failure = mapOf("code" to "unauthorized", "message" to message)
        assertEquals("error", first.cache.snapshot("instance").sessionStatus)
        assertEquals(failure, first.cache.snapshot("instance").error)
        assertTrue(first.cache.snapshot("instance").results.isEmpty())

        val returning = Harness(first.cache)
        val attached = returning.owner.snapshot()
        assertEquals("error", attached.sessionStatus)
        assertEquals(failure, attached.error)
        assertNull(attached.listening)
        assertNull(attached.playback.readingId)
        assertEquals("idle", attached.playback.status)
        assertEquals(0, attached.pendingCount)
        assertNull(returning.owner.sessionEpoch)
        assertFalse(returning.foreground); assertFalse(returning.wake)
        assertFalse(returning.events.contains("export")); assertFalse(returning.events.contains("connection"))

        returning.owner.configure(TtsActivationSource("other", "Other", "https://other.test") { _, _ -> fail("Attachment must not export credentials") })
        returning.owner.dispatch(TtsCommand.AttachDocument(DocumentOwner("other", "other-page", 2)))
        assertEquals("idle", returning.owner.snapshot().sessionStatus)
        assertNull(returning.owner.snapshot().error)
        assertEquals(failure, first.cache.snapshot("instance").error)

        val reattached = Harness(first.cache)
        assertEquals(failure, reattached.owner.snapshot().error)
        assertTrue(reattached.manual("fresh-operation").accepted)
        assertEquals("starting", reattached.owner.snapshot().sessionStatus)
        assertNull(reattached.owner.snapshot().error)
        assertEquals("fresh-operation", reattached.owner.snapshot().playback.operationId)
        assertTrue(reattached.events.contains("export"))
    }

    @Test fun stalePageAndInternalCommandsCannotClaimAcceptance() {
        val h = Harness()
        assertFalse(h.owner.dispatch(TtsCommand.Manual(h.document.copy(generation = 0), "old", emptyMap())).accepted)
        assertFalse(h.owner.dispatch(TtsCommand.Reconnect).accepted)
        assertFalse(h.events.contains("export"))
    }
    @Test fun playerAuthorizationLossStopsListeningButStaleGenerationCannotStopReplacement() {
        val h = Harness(); h.listen(); h.connect(); h.manual()
        val old = h.owner.snapshot().playback
        h.manual("replacement")
        val current = h.owner.snapshot().playback
        h.owner.feedback(old.readingId!!, old.generation, PlayerSignal.AuthorizationLost)
        assertNotNull(h.owner.snapshot().listening)
        assertEquals(current.readingId, h.owner.snapshot().playback.readingId)
        h.owner.feedback(current.readingId!!, current.generation, PlayerSignal.AuthorizationLost)
        assertNull(h.owner.snapshot().listening)
        assertEquals("error", h.owner.snapshot().sessionStatus)
        assertEquals("unauthorized", h.owner.snapshot().error!!["code"])
        assertTrue(h.socket.closed); assertTrue(h.stopped)
    }

    @Test fun bufferingAndResumeNeverCreateASecondStartedCommand() {
        val h = Harness(); h.manual(); h.connect()
        val p = h.owner.snapshot().playback
        h.owner.feedback(p.readingId!!, p.generation, PlayerSignal.Resumed)
        assertEquals("loading", h.owner.snapshot().playback.status)
        h.owner.feedback(p.readingId!!, p.generation, PlayerSignal.Started)
        h.owner.feedback(p.readingId!!, p.generation, PlayerSignal.Buffering)
        assertEquals("loading", h.owner.snapshot().playback.status)
        h.owner.feedback(p.readingId!!, p.generation, PlayerSignal.Resumed)
        assertEquals("playing", h.owner.snapshot().playback.status)
        assertEquals(1, h.socket.sent.count { it["type"] == "started" })
    }

    @Test fun automaticFailureCacheStaysBoundedAndKeeepsItsSourceInstance() {
        val cache = TtsSnapshotCache()
        fun value(instance: String, results: List<NativeTtsResult>) = NativeTtsSnapshot(revision = 1, instanceId = instance,
            sessionStatus = "connected", playback = NativePlayback(), listening = null, pendingCount = 0, error = null,
            configurationAvailable = true, results = results)
        fun result(id: Int, automatic: Boolean) = NativeTtsResult("result-$id", false,
            mapOf("ok" to false, "error" to "Failed"), mapOf("ok" to false, "error" to "Failed"), automatic, "reading-$id", id.toLong())
        cache.publish(value("source", listOf(result(0, true))))
        cache.publish(value("other", listOf(result(1, false))))
        assertTrue(cache.snapshot("source").results.single().automatic)
        assertFalse(cache.snapshot("other").results.single().automatic)
        assertEquals("reading-0", cache.snapshot("source").results.single().readingId)
        for (id in 2..65) cache.publish(value("other", listOf(result(id, id % 2 == 0))))
        assertTrue(cache.snapshot("source").results.isEmpty())
        assertEquals(64, cache.snapshot("other").results.size)
        assertEquals("result-2", cache.snapshot("other").results.first().operationId)
        val stopped = cache.stopped(value("other", emptyList()))
        assertEquals(64, stopped.results.size)
        assertEquals("idle", stopped.playback.status)
    }

}
