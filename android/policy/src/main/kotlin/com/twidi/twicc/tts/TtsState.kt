package com.twidi.twicc.tts

import java.util.UUID

/** All calls run on the service's single state owner. Nothing survives process death. */
class TtsController(private var instanceId: String, private val newId: () -> String = { UUID.randomUUID().toString() }) {
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
    private var listening: NativeListening? = null
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
    private fun valid(value: DocumentOwner) = value == owner && value.instanceId == instanceId
    private fun nextSeq(): Long { check(seq < 9007199254740991L); return ++seq }
    private fun frame(type: String, fields: TtsFrame = emptyMap()): TtsFrame =
        mapOf("protocol" to 1, "type" to type, "seq" to nextSeq()) + fields
    private fun queue(type: String, fields: TtsFrame = emptyMap()): Long {
        // A full replay lane must fail closed instead of dropping cancellation intent.
        if (retained.size >= 256) { terminate("replay_overflow", "Reconnect before starting more speech"); return seq }
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
    private fun finish(ok: Boolean, message: String? = null) {
        playback.operationId?.let {
            if (results.size == 64) results.removeFirst()
            val outcome = if (ok) mapOf("ok" to true) else mapOf("ok" to false, "error" to (message ?: "The audio stopped before the end"))
            results.addLast(NativeTtsResult(it, playback.started,
                if (playback.started) mapOf("ok" to true) else mapOf("ok" to false, "error" to (message ?: "The audio stopped before the end")), outcome))
        }
    }
    private fun cancel(sendSkip: Boolean = false) {
        val old = playback
        if (old.readingId != null) {
            if (sendSkip) queue("skip", readingFields(old))
            effects += TtsEffect.Cancel(old.readingId, old.generation)
            resource?.let { effects += TtsEffect.Release(it) }; resource = null
            finish(false)
        }
        playback = NativePlayback(generation = generation)
    }
    private fun terminate(code: String, message: String) {
        cancel(); listening = null; pendingCount = 0; ready = false; active = false
        retained.clear(); deferred.clear(); sessionId = null; secret = null; connectionId = null; serverRevision = -1
        stopSeq = null; stopEpoch = null; status = "error"; error = mapOf("code" to code, "message" to message)
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
            is TtsCommand.AttachDocument -> {
                if (command.owner.instanceId != instanceId || (owner != null && command.owner.generation < owner!!.generation)) return emptyList()
                owner = command.owner
            }
            is TtsCommand.Manual -> {
                if (!valid(command.owner) || stopSeq != null) return emptyList()
                // During an outage cancel old speech locally; never queue a fresh manual request.
                if (active && !ready && status != "starting") {
                    cancel(sendSkip = true)
                    results.addLast(NativeTtsResult(command.operationId, false, mapOf("ok" to false, "error" to "Could not reach TwiCC"),
                        mapOf("ok" to false, "error" to "Could not reach TwiCC")))
                    while (results.size > 64) results.removeFirst()
                    error = mapOf("code" to "unreachable", "message" to "Could not reach TwiCC")
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
                val next = NativeListening(newId(), command.owner.pageId, command.scope, command.boundary, command.includeMuted)
                listening = next; pendingCount = 0
                queue("listen", mapOf("activation_id" to next.activationId, "scope" to next.scope.wire(),
                    "page_id" to next.pageId, "include_muted" to next.includeMuted, "boundary" to next.boundary))
            }
            is TtsCommand.SetMuted -> {
                if (!valid(command.owner) || listening?.activationId != command.activationId) return emptyList()
                listening = listening!!.copy(includeMuted = command.includeMuted)
                queue("set_muted", mapOf("activation_id" to command.activationId, "include_muted" to command.includeMuted))
            }
            is TtsCommand.Disable -> {
                if (!valid(command.owner) || listening?.activationId != command.activationId) return emptyList()
                listening = null; pendingCount = 0
                if (playback.automatic) cancel()
                queue("disable", mapOf("activation_id" to command.activationId))
            }
            is TtsCommand.StopReading -> {
                if (!valid(command.owner) || playback.readingId != command.readingId || playback.identity != command.identity) return emptyList()
                cancel(sendSkip = true)
            }
            TtsCommand.Skip -> cancel(sendSkip = true)
            TtsCommand.StopAll -> {
                listening = null; pendingCount = 0; generation++; cancel()
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
                connectionId = null; ready = false; if (active) status = "reconnecting"
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
                        if (sessionId == null || secret == null) terminate("invalid_protocol", "Invalid native session")
                        else { ready = true; status = "connected"; flushPending() }
                    }
                    "state" -> {
                        val incoming = (f["revision"] as? Number)?.toLong() ?: -1
                        if (incoming > serverRevision) {
                            serverRevision = incoming
                            if (ack >= handshakeSeq) {
                                ready = true
                                if (stopSeq == null) status = "connected"
                                flushPending()
                            }
                            // Local reading and listening intent stay authoritative until acknowledged.
                            if (retained.isEmpty()) {
                                pendingCount = (f["pending_count"] as? Number)?.toInt() ?: 0
                                configuration = f["configuration_available"] as? Boolean
                                error = f["error"] as? TtsFrame
                                if (f.containsKey("listening") && f["listening"] == null) listening = null
                            }
                            val serverError = f["error"] as? TtsFrame
                            if (serverError != null) handleError(serverError)
                        }
                    }
                    "candidate" -> if (ready && stopSeq == null && playback.readingId == null && listening?.activationId == f["activation_id"]) {
                        reserve(null, f["identity"] as? TtsFrame, "message", f["text"] as? String ?: "", null, true)
                        queue("select", readingFields() + mapOf("event_id" to f["event_id"]))
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
                    PlayerSignal.Buffering -> playback = playback.copy(phase = "buffering")
                    PlayerSignal.Started -> if (!playback.started) { playback = playback.copy(started = true, phase = "playing", status = "playing"); queue("started", readingFields()) }
                    PlayerSignal.Settling -> playback = playback.copy(phase = "settling")
                    is PlayerSignal.Ended -> {
                        finish(signal.ok, signal.error)
                        queue("finished", readingFields() + mapOf("outcome" to if (signal.ok) "complete" else "failed", "error" to signal.error))
                        resource?.let { effects += TtsEffect.Release(it) }; resource = null
                        playback = NativePlayback(generation = generation)
                    }
                }
            }
            is TtsCommand.SwitchInstance -> {
                if (command.instanceId == instanceId) return emptyList()
                terminate("instance_changed", "The active instance changed")
                instanceId = command.instanceId; owner = null; status = "idle"; error = null; ack = 0
            }
            is TtsCommand.AuthorizationLost -> terminate("unauthorized", command.message)
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
    private fun matches(f: TtsFrame) = playback.readingId != null && f["reading_id"] == playback.readingId &&
        (f["generation"] as? Number)?.toLong() == playback.generation
    private fun handleError(f: TtsFrame) {
        val code = f["code"] as? String ?: "failed"
        val message = f["message"] as? String ?: "Speech failed"
        if (code in setOf("unauthorized", "session_lost")) terminate(code, message)
        else if (code in setOf("catchup_incomplete", "scope_unavailable")) {
            listening?.let { queue("disable", mapOf("activation_id" to it.activationId)) }
            listening = null; pendingCount = 0; if (playback.automatic) cancel(); error = mapOf("code" to code, "message" to message)
        } else if (f["reading_id"] == null || matches(f)) {
            if (playback.readingId != null) { finish(false, message); playback = playback.copy(operationId = null); cancel() }
            error = f.filterKeys { it in setOf("code", "message", "status", "saved_id", "summary_changed", "reading_id", "generation") }
        }
    }
}
