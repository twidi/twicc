package com.twidi.twicc.tts

import java.util.UUID

/** All calls run on the service's single state owner. Nothing survives process death. */
class TtsController(private var instanceId: String, private val newId: () -> String = { UUID.randomUUID().toString() },
    private val elapsedMs: () -> Long = { System.nanoTime() / 1_000_000 },
    private val recoveryAnnouncement: () -> String = { "Connection restored. Messages received during the interruption will not be read. Automatic reading resumes." }) {
    // Preserve the original trailing newId lambda constructor.
    constructor(instanceId: String, newId: () -> String) : this(instanceId, newId, { System.nanoTime() / 1_000_000 })
    private val clientId = newId()
    private var owner: DocumentOwner? = null
    private var revision = 0L
    private var generation = 0L
    private var seq = 0L
    private var ack = 0L
    private var serverRevision = -1L
    private var connectionId: String? = null
    private var sessionId: String? = null
    private var secret: String? = null
    private var ready = false
    private var active = false
    private var stopSeq: Long? = null
    private var stopEpoch: String? = null
    // Inert cancellation evidence only. No code resumes an abandoned session.
    private var tombstone: Pair<String?, List<TtsFrame>>? = null
    private var handshakeSeq = 0L
    private var sentSeq = 0L
    private var openingFrame: TtsFrame? = null
    private var status = "idle"
    private var playback = NativePlayback()
    private var audibleEndedAt: Long? = null
    private var listening: NativeListening? = null
    private var outageAt: Long? = null
    private var recoverySeq: Long? = null
    private var recoveryLostAt: Long? = null
    private var noticeDue = false
    private var longPolicyApplied = false
    private var checkpointReset = false
    private var checkpoint: String? = null
    private val checkpointEvents = linkedMapOf<Long, java.time.Instant>()
    private val consumedEvents = linkedSetOf<Long>()
    private var playbackFinalEndingId: Long? = null
    private var playbackEvent: Pair<Long, String>? = null
    private var pendingCount = 0
    private var error: TtsFrame? = null
    private var configuration: Boolean? = null
    private var resource: AudioResource? = null
    private val retained = linkedMapOf<Long, TtsFrame>()
    private val results = ArrayDeque<NativeTtsResult>()
    private val deferred = ArrayDeque<Pair<String, TtsFrame>>()
    private var effects = mutableListOf<TtsEffect>()

    fun snapshot() = NativeTtsSnapshot(revision = revision, instanceId = instanceId, sessionStatus = status,
        playback = playback, listening = listening, pendingCount = pendingCount, error = error,
        configurationAvailable = configuration, results = results.toList())
    /** Delivered or expired page outcomes are removed, never kept as an error history. */
    fun forgetOutcomes(operationIds: Set<String>, consumedError: TtsFrame?) {
        results.removeAll { it.operationId in operationIds }
        if (consumedError != null && error == consumedError) error = null
    }
    private fun valid(value: DocumentOwner) = value == owner && value.instanceId == instanceId
    private fun nextSeq(): Long { check(seq < 9007199254740991L); return ++seq }
    private fun frame(type: String, fields: TtsFrame = emptyMap()): TtsFrame =
        mapOf("protocol" to 1, "type" to type, "seq" to nextSeq()) + fields
    private fun queue(type: String, fields: TtsFrame = emptyMap()): Long {
        // A full replay lane must fail closed instead of dropping cancellation intent.
        if (retained.size >= 256) { terminate("replay_overflow", "Speech stopped. Start playback or turn on timeline reading again.", mapOf("ui_message" to ttsUiMessage("backend.tts.sessionLost"))); return seq }
        val value = frame(type, fields); val sequence = (value["seq"] as Number).toLong()
        retained[sequence] = value
        if (ready) flushPending()
        return sequence
    }
    private fun flushPending() {
        for ((sequence, value) in retained) {
            if (sequence > sentSeq) { effects += TtsEffect.Send(value); sentSeq = sequence }
        }
    }
    private fun listenFields(current: NativeListening): TtsFrame = mapOf(
        "activation_id" to current.activationId, "scope" to current.scope.wire(), "page_id" to current.pageId,
        "include_muted" to current.includeMuted, "boundary" to (checkpoint ?: current.boundary),
        "excluded_event_ids" to consumedEvents.toList(), "final_boundary" to current.finalBoundary,
        "ending_cursor" to current.endingCursor, "ordinary_after_id" to current.ordinaryAfterId,
        "pending_final_ending_ids" to current.pendingFinalEndingIds)
    private fun longOutage() = outageAt?.let { elapsedMs() - it >= 120000L } == true
    private fun recoverTimeline(fresh: Boolean = true) {
        val current = listening ?: return
        if (current.paused || stopSeq != null) return
        recoveryLostAt = null
        cancel(sendSkip = true)
        val next = if (fresh) current.copy(activationId = newId(), finalBoundary = current.boundary,
            endingCursor = 0, ordinaryAfterId = 0, pendingFinalEndingIds = emptyList())
            else current.copy(activationId = newId())
        listening = next; pendingCount = 0
        if (fresh) { checkpoint = null; checkpointEvents.clear(); consumedEvents.clear() }
        val notice = if (noticeDue) {
            reserve(null, null, "recovery", recoveryAnnouncement(), null, true)
            readingFields() + mapOf("text" to playback.text)
        } else null
        recoverySeq = queue("listen", listenFields(next) + mapOf("fresh" to fresh, "notice" to notice))
        deferred.clear()
    }
    private fun reconciled(state: TtsFrame) {
        if (stopSeq != null) return
        val reset = recoverySeq
        if (reset != null) {
            if (ack >= reset && (state["listening"] as? Map<*, *>)?.get("activation_id") == listening?.activationId) {
                recoverySeq = null
                val serverBoundary = (state["listening"] as? Map<*, *>)?.get("boundary") as? String
                if (serverBoundary != null) listening = listening?.copy(boundary = serverBoundary,
                    finalBoundary = ((state["listening"] as? Map<*, *>)?.get("final_boundary") as? String) ?: serverBoundary)
                val nextLongOutage = recoveryLostAt?.let { elapsedMs() - it >= 120000L } == true
                if (longOutage() && !longPolicyApplied || nextLongOutage) {
                    noticeDue = true; longPolicyApplied = true; recoverTimeline()
                } else if (noticeDue && playback.source != "recovery" && listening?.paused == false) {
                    // Retain the already accepted boundary when only notice playback needs recovery.
                    recoverTimeline(fresh = false)
                } else { outageAt = null; longPolicyApplied = false; checkpointReset = false; status = "connected" }
            }
        } else if (outageAt != null) {
            val fresh = longOutage()
            if (fresh && !longPolicyApplied) { noticeDue = true; longPolicyApplied = true }
            if (listening?.paused == false && (fresh || checkpointReset || noticeDue)) recoverTimeline(fresh)
            else if (!fresh) outageAt = null
        }
        if (recoverySeq != null) status = "reconnecting"
    }
    private fun reopenSession() {
        // Session retention is independent of listening intent and outage duration.
        if (!active || stopSeq != null || listening == null) return
        if (outageAt == null) outageAt = elapsedMs()
        cancel(); retained.clear(); deferred.clear(); recoverySeq = null; checkpointReset = true
        sessionId = null; secret = null; openingFrame = null; serverRevision = -1
        ack = 0; ready = false
        connect()
        listening?.takeUnless { it.paused }?.let {
            if (longOutage()) {
                noticeDue = true; longPolicyApplied = true
                recoverTimeline(fresh = true)
            } else queue("listen", listenFields(it))
        }
    }
    private fun readingFields(p: NativePlayback = playback): TtsFrame = mapOf("reading_id" to p.readingId, "generation" to p.generation)
    private fun connect() {
        ready = false; deferred.clear()
        val handshake = if (sessionId == null) {
            openingFrame ?: frame("open", mapOf("client_id" to clientId)).also { openingFrame = it }
        } else frame("resume", mapOf(
            "session_id" to sessionId, "resume_secret" to secret, "ack_seq" to ack,
            "commands" to retained.values.toList(), "current" to playback.takeIf { it.readingId != null }?.let {
                readingFields(it) + mapOf("audio_id" to it.audioId, "phase" to it.phase, "started" to it.started)
            }))
        handshakeSeq = (handshake["seq"] as Number).toLong()
        sentSeq = handshakeSeq
        status = if (sessionId == null && status != "reconnecting") "starting" else "reconnecting"
        effects += TtsEffect.Connect(handshake)
    }
    private fun activate() {
        if (!active) {
            active = true; stopSeq = null; stopEpoch = null; error = null; openingFrame = null
            if (tombstone != null) { sessionId = null; secret = null; retained.clear() }
            serverRevision = -1
            // Open reserves its sequence before the user's first command.
            effects += TtsEffect.RequireForeground
            connect()
        }
    }
    private fun finish(ok: Boolean, message: String? = null, aborted: Boolean = false, details: TtsFrame = emptyMap()) {
        val operationId = playback.operationId ?: if (playback.automatic && !ok && !aborted && playback.readingId != null)
            "automatic:${playback.readingId}:${playback.generation}" else return
        if (results.size == 64) results.removeFirst()
        val outcome = if (aborted) mapOf("ok" to false, "aborted" to true) else if (ok) mapOf("ok" to true) else mapOf("ok" to false, "error" to (message ?: "The audio stopped before the end")) +
            (if (message == null) mapOf("ui_message" to ttsUiMessage("session.speech.errors.cut")) else emptyMap())
        results.addLast(NativeTtsResult(operationId, playback.started,
            if (playback.started) mapOf("ok" to true) else outcome + details, outcome + details,
            playback.automatic, playback.readingId, playback.generation))
    }
    private fun cancel(sendSkip: Boolean = false, failure: String? = null, failureDetails: TtsFrame = emptyMap()) {
        val old = playback
        if (old.readingId != null) {
            if (sendSkip) queue("skip", readingFields(old))
            effects += TtsEffect.Cancel(old.readingId, old.generation)
            resource?.let { effects += TtsEffect.Release(it) }; resource = null
            finish(false, failure, aborted = failure == null, details = failureDetails)
        }
        playback = NativePlayback(generation = generation); playbackEvent = null; playbackFinalEndingId = null
    }
    private fun errorOwner(value: NativePlayback): TtsFrame = if (value.readingId == null) emptyMap() else
        mapOf("automatic" to value.automatic, "operation_id" to value.operationId, "reading_id" to value.readingId, "generation" to value.generation)
    private fun terminate(code: String, message: String, details: TtsFrame = emptyMap()) {
        val failedOwner = errorOwner(playback)
        cancel(failure = message, failureDetails = details); listening = null; pendingCount = 0; ready = false; active = false
        outageAt = null; recoverySeq = null; noticeDue = false
        retained.clear(); deferred.clear(); sessionId = null; secret = null; connectionId = null; serverRevision = -1
        stopSeq = null; stopEpoch = null; status = "error"; error = mapOf("code" to code, "message" to message) + details + failedOwner
        effects += TtsEffect.CloseConnection; effects += TtsEffect.StopService
    }
    private fun reserve(operationId: String?, identity: TtsFrame?, source: String?, text: String, configId: String?, automatic: Boolean) {
        val readingId = newId(); generation++ // Reserve before cancelling asynchronous automatic preparation.
        cancel()
        playback = NativePlayback(readingId, generation, operationId, identity, source, text, configId, "loading", "loading", automatic)
    }
    @Suppress("UNCHECKED_CAST")
    fun dispatch(command: TtsCommand): List<TtsEffect> {
        effects = mutableListOf()
        when (command) {
            // The service cache owns the page revision and handles acknowledgements.
            is TtsCommand.Acknowledge -> return emptyList()
            is TtsCommand.AttachDocument -> {
                if (command.owner.instanceId != instanceId || (owner != null && command.owner.generation < owner!!.generation)) return emptyList()
                owner = command.owner
            }
            is TtsCommand.Manual -> {
                if (!valid(command.owner) || stopSeq != null) return emptyList()
                // During an outage cancel old speech locally; never queue a fresh manual request.
                if (active && !ready && status != "starting") {
                    cancel(sendSkip = true)
                    results.addLast(NativeTtsResult(command.operationId, false, mapOf("ok" to false, "error" to "Could not reach TwiCC", "ui_message" to ttsUiMessage("session.speech.errors.unreachable")),
                        mapOf("ok" to false, "error" to "Could not reach TwiCC", "ui_message" to ttsUiMessage("session.speech.errors.unreachable"))))
                    while (results.size > 64) results.removeFirst()
                    error = mapOf("code" to "unreachable", "message" to "Could not reach TwiCC", "ui_message" to ttsUiMessage("session.speech.errors.unreachable"))
                } else {
                    activate(); reserve(command.operationId, command.request["identity"] as? TtsFrame,
                        if (command.request["kind"] == "summary") "summary" else command.request["source"] as? String,
                        command.request["text"] as? String ?: "", command.request["config_id"] as? String, false)
                    queue("manual", readingFields() + mapOf("request" to command.request))
                }
            }
            is TtsCommand.Listen -> {
                if (!valid(command.owner) || stopSeq != null) return emptyList()
                activate()
                listening?.let { queue("disable", mapOf("activation_id" to it.activationId)); if (playback.automatic) cancel() }
                val next = NativeListening(newId(), command.pageId, command.scope, command.boundary, command.includeMuted)
                listening = next; pendingCount = 0; checkpoint = null; checkpointEvents.clear(); consumedEvents.clear()
                outageAt = if (!ready) elapsedMs() else null; recoverySeq = null; noticeDue = false; longPolicyApplied = false
                // Only explicit user activation carries the collection epoch.
                // Recovery and notification resume rebuild listenFields without it.
                queue("listen", listenFields(next) + (command.telemetryEpoch?.let { mapOf("telemetry_epoch" to it) } ?: emptyMap()))
            }
            is TtsCommand.SetMuted -> {
                if (!valid(command.owner) || listening?.activationId != command.activationId) return emptyList()
                listening = listening!!.copy(includeMuted = command.includeMuted)
                if (listening?.paused != true)
                    queue("set_muted", mapOf("activation_id" to command.activationId, "include_muted" to command.includeMuted))
            }
            is TtsCommand.Disable -> {
                if (!valid(command.owner) || listening?.activationId != command.activationId) return emptyList()
                listening = null; pendingCount = 0; outageAt = null; noticeDue = false; recoverySeq = null
                if (playback.automatic) cancel()
                queue("disable", mapOf("activation_id" to command.activationId))
            }
            is TtsCommand.StopReading -> {
                if (!valid(command.owner) || playback.readingId != command.readingId || playback.identity != command.identity) return emptyList()
                cancel(sendSkip = true)
            }
            TtsCommand.Skip -> {
                if (playback.source == "recovery") noticeDue = false
                cancel(sendSkip = true)
            }
            is TtsCommand.PauseTimeline -> {
                val current = listening ?: return emptyList()
                if (stopSeq != null || current.paused || current.activationId != command.activationId) return emptyList()
                listening = current.copy(paused = true); pendingCount = 0
                cancel(sendSkip = true)
                queue("disable", mapOf("activation_id" to current.activationId))
            }
            is TtsCommand.ResumeTimeline -> {
                val current = listening ?: return emptyList()
                if (stopSeq != null || !current.paused || current.activationId != command.activationId) return emptyList()
                val next = current.copy(activationId = newId(), boundary = command.boundary, paused = false,
                    finalBoundary = command.boundary, endingCursor = 0, ordinaryAfterId = 0, pendingFinalEndingIds = emptyList())
                listening = next; pendingCount = 0; checkpoint = null; checkpointEvents.clear(); consumedEvents.clear()
                if (longOutage()) noticeDue = true
                if (ready && noticeDue) { longPolicyApplied = true; recoverTimeline() }
                else queue("listen", listenFields(next))
            }
            TtsCommand.StopAll -> {
                listening = null; pendingCount = 0; outageAt = null; noticeDue = false; recoverySeq = null; generation++; cancel()
                status = "stopped"
                if (active && stopSeq == null) {
                    stopSeq = queue("stop_all"); stopEpoch = newId()
                    effects += TtsEffect.StopAfter(stopEpoch!!)
                } else if (!active) { effects += TtsEffect.CloseConnection; effects += TtsEffect.StopService }
            }
            is TtsCommand.StopTimeout -> {
                if (command.epoch != stopEpoch) return emptyList()
                tombstone = sessionId to retained.values.toList()
                active = false; ready = false; connectionId = null; stopSeq = null; stopEpoch = null; status = "stopped"
                effects += TtsEffect.CloseConnection; effects += TtsEffect.StopService
            }
            TtsCommand.Reconnect -> { if (!active || (stopSeq != null && sessionId == null)) return emptyList(); connect() }
            TtsCommand.Heartbeat -> { if (!ready) return emptyList(); queue("ping") }
            is TtsCommand.TransportOpened -> { if (!active) return emptyList(); connectionId = command.connectionId }
            is TtsCommand.TransportLost -> {
                if (command.connectionId != connectionId) return emptyList()
                if (recoverySeq != null && recoveryLostAt == null) recoveryLostAt = elapsedMs()
                connectionId = null; ready = false
                if (active) { if (outageAt == null) outageAt = elapsedMs(); status = "reconnecting" }
                // A streaming resource cannot seek. Cancel it instead of waiting on a stalled read.
                if (listening?.paused == false && (playback.automatic || resource != null)) {
                    checkpointReset = playback.automatic
                    cancel(sendSkip = true)
                }
            }
            is TtsCommand.ServerFrame -> {
                if (command.connectionId != connectionId || command.frame["protocol"] != 1 && command.frame["protocol"] != 1L) return emptyList()
                val f = command.frame
                if (f["type"] in setOf("audio", "candidate") && !ready && stopSeq == null) {
                    if (deferred.size < 64) deferred.addLast(command.connectionId to f)
                    return emptyList()
                }
                val incomingAck = (f["ack_seq"] as? Number)?.toLong()
                if (incomingAck != null && incomingAck <= sentSeq && incomingAck >= ack) {
                    ack = incomingAck; retained.keys.removeAll { it <= ack }
                }
                if (stopSeq != null && ack >= stopSeq!!) {
                    active = false; ready = false; retained.clear(); sessionId = null; secret = null; connectionId = null
                    stopSeq = null; stopEpoch = null; status = "stopped"; effects += TtsEffect.CloseConnection; effects += TtsEffect.StopService
                } else when (f["type"]) {
                    "opened_session" -> {
                        sessionId = f["session_id"] as? String; secret = f["resume_secret"] as? String
                        if (sessionId == null || secret == null) terminate("invalid_protocol", "Speech could not start. Reload the page and try again.", mapOf("ui_message" to ttsUiMessage("session.speech.errors.startFailed")))
                        else { ready = true; status = "connected"; flushPending(); reconciled(f) }
                    }
                    "state" -> {
                        val incoming = (f["revision"] as? Number)?.toLong() ?: -1
                        if (incoming > serverRevision) {
                            serverRevision = incoming
                            if (ack >= handshakeSeq) {
                                ready = true
                                if (stopSeq == null) status = "connected"
                                flushPending()
                                retainListeningSnapshot(f)
                                reconciled(f)
                            }
                            // Update backend metadata after acknowledgement. Listening intent follows commands and owned errors.
                            // A null backend listener can precede its activation and cannot clear that intent.
                            if (retained.isEmpty()) {
                                pendingCount = if (listening?.paused == true) 0 else (f["pending_count"] as? Number)?.toInt() ?: 0
                                configuration = f["configuration_available"] as? Boolean
                                val incomingError = f["error"] as? TtsFrame
                                if (incomingError == null) error = null
                                // Reading errors from an older selection cannot own a replacement.
                                else if (!obsoleteListeningError(incomingError) &&
                                    (incomingError["reading_id"] == null || matches(incomingError))) error = incomingError
                            }
                            val serverError = f["error"] as? TtsFrame
                            if (serverError != null) handleError(serverError)
                        }
                    }
                    "selection_rejected" -> if (matches(f) && playback.automatic && playback.phase == "loading" &&
                        playback.audioId == null && resource == null) cancel()
                    "candidate" -> if (ready && recoverySeq == null && stopSeq == null && playback.readingId == null && listening?.paused == false && listening?.activationId == f["activation_id"]) {
                        reserve(null, f["identity"] as? TtsFrame, "message", f["text"] as? String ?: "", null, true)
                        playbackFinalEndingId = (f["final_ending_id"] as? Number)?.toLong()
                        playbackEvent = (f["event_id"] as? Number)?.toLong()?.let { id ->
                            (f["occurred_at"] as? String)?.let { id to it }
                        }
                        queue("select", readingFields() + mapOf("event_id" to f["event_id"],
                            "silence_reset" to (audibleEndedAt?.let { elapsedMs() - it > 180000L } ?: false)))
                    }
                    "audio" -> if (ready && stopSeq == null && matches(f) && resource == null && sessionId != null) {
                        val audioId = f["audio_id"] as? String
                        val contentType = f["content_type"] as? String
                        if (audioId != null && contentType != null) {
                            val value = AudioResource(instanceId, sessionId!!, secret!!, playback.readingId!!, playback.generation, audioId, contentType)
                            resource = value
                            playback = playback.copy(audioId = audioId, phase = "buffering", identity = f["identity"] as? TtsFrame,
                                source = f["source"] as? String ?: playback.source, text = f["text"] as? String ?: playback.text,
                                configId = f["config_id"] as? String ?: playback.configId)
                            effects += TtsEffect.Prepare(value)
                            effects += TtsEffect.Play(value.readingId, value.generation)
                        }
                    }
                    "error" -> handleError(f)
                }
            }
            is TtsCommand.PlayerFeedback -> {
                if (command.readingId != playback.readingId || command.generation != playback.generation) return emptyList()
                when (val signal = command.signal) {
                    PlayerSignal.Buffering -> playback = playback.copy(phase = "buffering", status = "loading")
                    PlayerSignal.Started -> if (!playback.started) {
                        playback = playback.copy(started = true, phase = "playing", status = "playing")
                        if (playback.source == "recovery") noticeDue = false
                        playbackEvent?.let { (id, occurred) ->
                            consumedEvents.add(id)
                            if (playbackFinalEndingId == null) {
                                checkpointEvents[id] = java.time.Instant.parse(occurred)
                                val latest = checkpointEvents.values.maxOrNull()!!
                                // The listener admits late arrivals within five seconds of its watermark.
                                val floor = maxOf(latest.minusSeconds(5).minusNanos(1000), java.time.Instant.parse(listening!!.boundary))
                                checkpoint = java.time.format.DateTimeFormatterBuilder().appendInstant(6).toFormatter().format(floor)
                                checkpointEvents.entries.removeAll { it.value <= floor }
                            }
                        }
                        queue("started", readingFields())
                    }
                    PlayerSignal.Resumed -> if (playback.started) playback = playback.copy(phase = "playing", status = "playing")
                    PlayerSignal.Settling -> {
                        if (playback.started && playback.phase != "settling") audibleEndedAt = elapsedMs()
                        playback = playback.copy(phase = "settling")
                    }
                    PlayerSignal.AuthorizationLost -> terminate("unauthorized", "Authentication expired. Open TwiCC and sign in again.", mapOf("ui_message" to ttsUiMessage("backend.tts.authenticationExpired")))
                    is PlayerSignal.Ended -> {
                        if (playback.source == "recovery") noticeDue = false
                        finish(signal.ok, signal.error, details = signal.uiMessage?.let { mapOf("ui_message" to it) } ?: emptyMap())
                        queue("finished", readingFields() + mapOf("outcome" to if (signal.ok) "complete" else "failed", "error" to signal.error))
                        resource?.let { effects += TtsEffect.Release(it) }; resource = null
                        playback = NativePlayback(generation = generation)
                    }
                }
            }
            is TtsCommand.SwitchInstance -> {
                if (command.instanceId == instanceId) return emptyList()
                terminate("instance_changed", "The active instance changed", mapOf("ui_message" to ttsUiMessage("android.speech.errors.instanceChanged")))
                instanceId = command.instanceId; owner = null; status = "idle"; error = null; ack = 0
            }
            is TtsCommand.AuthorizationLost -> terminate("unauthorized", command.message, command.uiMessage?.let { mapOf("ui_message" to it) } ?: emptyMap())
        }
        if (active && stopSeq == null && listening == null && playback.readingId == null) {
            stopSeq = queue("stop_all"); stopEpoch = newId(); status = "stopped"
            effects += TtsEffect.StopAfter(stopEpoch!!)
        }
        revision++
        effects += TtsEffect.UpdateNotification
        val result = effects.toList()
        // The server sends a resumed audio reference before its final acknowledged state.
        // Drain only after reconciliation; native reading generations still reject cancelled work.
        if (ready && deferred.isNotEmpty()) {
            val frames = deferred.toList(); deferred.clear()
            return result + frames.flatMap { (id, frame) -> dispatch(TtsCommand.ServerFrame(id, frame)) }
        }
        return result
    }
    /** Boundary, cursors, obligations and consumed IDs come from one acknowledged activation. */
    private fun retainListeningSnapshot(frame: TtsFrame) {
        val current = listening ?: return
        if (current.paused || retained.isNotEmpty()) return
        val state = frame["listening"] as? Map<*, *> ?: return
        if (state["activation_id"] != current.activationId) return
        val boundary = state["boundary"] as? String ?: current.boundary
        listening = current.copy(boundary = boundary,
            finalBoundary = state["final_boundary"] as? String ?: current.finalBoundary,
            endingCursor = (state["ending_cursor"] as? Number)?.toLong() ?: current.endingCursor,
            ordinaryAfterId = (state["ordinary_after_id"] as? Number)?.toLong() ?: current.ordinaryAfterId,
            pendingFinalEndingIds = (state["pending_final_ending_ids"] as? List<*>)?.mapNotNull {
                (it as? Number)?.toLong()
            } ?: current.pendingFinalEndingIds)
        (state["excluded_event_ids"] as? List<*>)?.forEach {
            (it as? Number)?.toLong()?.let(consumedEvents::add)
        }
    }

    private fun matches(f: TtsFrame) = playback.readingId != null && f["reading_id"] == playback.readingId &&
        (f["generation"] as? Number)?.toLong() == playback.generation
    private fun obsoleteListeningError(f: TtsFrame): Boolean = f["reading_id"] == null &&
        f["code"] in setOf("catchup_incomplete", "scope_unavailable", "tts_unavailable") &&
        (listening == null || listening!!.let { it.paused || (f["activation_id"] != null && f["activation_id"] != it.activationId) })
    private fun handleError(f: TtsFrame) {
        if (obsoleteListeningError(f)) return
        val code = f["code"] as? String ?: "failed"
        val message = f["message"] as? String ?: "Speech failed"
        val presentation = f["ui_message"]?.let { mapOf("ui_message" to it) }
            ?: if (f["message"] !is String) mapOf("ui_message" to ttsUiMessage("android.speech.errors.failed")) else emptyMap()
        if (code == "session_lost" && sessionId != null && active && listening != null && stopSeq == null) reopenSession()
        else if (code in setOf("unauthorized", "session_lost", "invalid_command", "invalid_protocol")) terminate(code, message, presentation)
        else if (code in setOf("catchup_incomplete", "scope_unavailable", "tts_unavailable")) {
            listening?.let { queue("disable", mapOf("activation_id" to it.activationId)) }
            listening = null; pendingCount = 0; if (playback.automatic) cancel(); error = mapOf("code" to code, "message" to message) + presentation
        } else if (f["reading_id"] == null || matches(f)) {
            if (playback.source == "recovery") noticeDue = false
            val failedOwner = errorOwner(playback)
            val details = (if (f["summary_changed"] == true) mapOf("summaryChanged" to true, "savedId" to f["saved_id"]) else emptyMap()) + presentation
            if (playback.readingId != null) { finish(false, message, details = details); playback = playback.copy(operationId = null, automatic = false); cancel() }
            error = f.filterKeys { it in setOf("code", "message", "status", "saved_id", "summary_changed", "reading_id", "generation", "ui_message") } +
                mapOf("message" to message) + presentation + failedOwner
        }
    }
}
