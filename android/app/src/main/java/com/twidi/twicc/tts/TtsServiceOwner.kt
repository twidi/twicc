package com.twidi.twicc.tts

import java.util.UUID
import java.util.logging.Level
import java.util.logging.Logger

/** Action names are native UI actions, never credentials or Android Intent payloads. */
data class TtsActionError(val code: String, val message: String, val action: String? = null, val uiMessage: TtsFrame? = null)
data class Acceptance(val accepted: Boolean, val operationId: String? = null, val error: TtsActionError? = null)
class TtsActivationSource(val instanceId: String, val name: String, val origin: String,
    val exportCookies: (List<String>, (Result<List<TtsCookie>>) -> Unit) -> Unit)

interface TtsServicePlatform {
    fun notificationError(): TtsActionError?
    fun foreground(snapshot: NativeTtsSnapshot, epoch: String)
    fun update(snapshot: NativeTtsSnapshot, epoch: String)
    fun wakeLock(held: Boolean)
    fun stop()
    fun enqueue(block: () -> Unit)
    fun after(delayMillis: Long, block: () -> Unit)
    fun worker(block: () -> Unit)
}
/** The adapter wraps the existing native transport. It does not own another network client. */
interface TtsTransport {
    var event: (TtsConnectionEvent) -> Unit
    val connection: TtsConnection?
    fun connect(handshake: TtsFrame): String
    fun send(frame: TtsFrame): Boolean
    fun reconciled(connectionId: String)
    fun cancelAudio(resource: AudioResource)
    fun release(resource: AudioResource)
    fun close()
}
/** Task 7 implements this common output. Feedback carries the original reading's identity. */
interface TtsPlaybackOutput {
    fun prepare(resource: AudioResource, connection: TtsTransport, feedback: (PlayerSignal) -> Unit)
    fun play(readingId: String, generation: Long)
    fun cancel(readingId: String, generation: Long)
    fun close()
}
class NativeTtsTransport(credentials: TtsCredentials) : TtsTransport {
    override val connection = TtsConnection(credentials)
    private val identity = TtsCredentials(credentials.instanceId, credentials.origin, emptyList())
    override var event: (TtsConnectionEvent) -> Unit = {}
        set(value) { field = value; connection.onEvent = value }
    override fun connect(handshake: TtsFrame) = connection.connect(identity, handshake)
    override fun send(frame: TtsFrame) = connection.send(frame)
    override fun reconciled(connectionId: String) = connection.reconciled(connectionId)
    override fun cancelAudio(resource: AudioResource) = connection.cancelAudio(resource)
    override fun release(resource: AudioResource) = connection.release(resource)
    override fun close() = connection.close()
}
class UnavailableTtsOutput : TtsPlaybackOutput {
    override fun prepare(resource: AudioResource, connection: TtsTransport, feedback: (PlayerSignal) -> Unit) {
        feedback(PlayerSignal.Ended(false, "Speech is unavailable. Update the Android app and try again.", uiMessage = ttsUiMessage("android.speech.errors.updateRequired")))
    }
    override fun play(readingId: String, generation: Long) {}
    override fun cancel(readingId: String, generation: Long) {}
    override fun close() {}
}

/** Only safe terminal data survives service destruction. Nothing here can resume a backend session. */
data class ConsumedTtsOutcomes(val operationIds: Set<String>, val error: TtsFrame?)

class TtsSnapshotCache(private val elapsedMs: () -> Long = { System.nanoTime() / 1_000_000 }) {
    private data class Pending<T>(val instanceId: String, val value: T, val revision: Long, val createdAt: Long)
    private var revision = 0L
    private var last: NativeTtsSnapshot? = null
    private val results = linkedMapOf<String, Pending<NativeTtsResult>>()
    private val errors = mutableMapOf<String, Pending<TtsFrame>>()
    private val terminalStates = mutableMapOf<String, Pair<String, TtsFrame?>>()
    fun beginActivation(instanceId: String) { terminalStates.remove(instanceId) }
    fun publish(value: NativeTtsSnapshot, mirrorOnly: Boolean = false): NativeTtsSnapshot {
        val nextRevision = ++revision
        value.results.forEach {
            results.putIfAbsent(it.operationId, Pending(value.instanceId, it, nextRevision, elapsedMs()))
        }
        while (results.size > 64) results.remove(results.keys.first())
        val terminal = terminalStates[value.instanceId].takeIf { mirrorOnly }
        val error = if (terminal != null) terminal.second else value.error
        if (error == null) errors.remove(value.instanceId)
        else if (errors[value.instanceId]?.value != error)
            errors[value.instanceId] = Pending(value.instanceId, error, nextRevision, elapsedMs())
        return value.copy(revision = nextRevision,
            sessionStatus = terminal?.first ?: value.sessionStatus,
            error = error,
            results = pendingResults(value.instanceId)).also { last = it }
    }
    private fun pendingResults(instanceId: String) = results.values.filter { it.instanceId == instanceId }.map { it.value }
    fun snapshot(instanceId: String): NativeTtsSnapshot = last?.takeIf { it.instanceId == instanceId }
        ?: NativeTtsSnapshot(revision = revision, instanceId = instanceId,
            sessionStatus = terminalStates[instanceId]?.first ?: "idle",
            playback = NativePlayback(), listening = null, pendingCount = 0, error = terminalStates[instanceId]?.second,
            configurationAvailable = null, results = pendingResults(instanceId))
    fun acknowledge(instanceId: String, throughRevision: Long): ConsumedTtsOutcomes {
        require(throughRevision in 0..revision)
        return consume(instanceId) { it.revision <= throughRevision }
    }
    // A late return should show the current controls, not a collection of old failures.
    fun expire(instanceId: String): ConsumedTtsOutcomes = consume(instanceId) { elapsedMs() - it.createdAt >= 10 * 60 * 1000L }
    private fun consume(instanceId: String, matches: (Pending<*>) -> Boolean): ConsumedTtsOutcomes {
        val ids = results.filterValues { it.instanceId == instanceId && matches(it) }.keys.toSet()
        ids.forEach { results.remove(it) }
        val error = errors[instanceId]?.takeIf(matches)?.value
        if (error != null) {
            errors.remove(instanceId)
            terminalStates[instanceId]?.let { terminalStates[instanceId] = it.first to null }
        }
        if (ids.isNotEmpty() || error != null) {
            revision++
            last?.takeIf { it.instanceId == instanceId }?.let {
                last = it.copy(revision = revision, results = pendingResults(instanceId), error = if (error != null) null else it.error)
            }
        }
        return ConsumedTtsOutcomes(ids, error)
    }
    fun stopped(value: NativeTtsSnapshot): NativeTtsSnapshot {
        val status = if (value.sessionStatus == "error") "error" else "stopped"
        terminalStates[value.instanceId] = status to value.error
        return publish(value.copy(playback = NativePlayback(), listening = null, pendingCount = 0, sessionStatus = status))
    }
}

/** The service main looper is the sole state owner. Binding and document replacement never drive lifetime. */
class TtsServiceOwner(private val platform: TtsServicePlatform,
    private val transportFactory: (TtsCredentials) -> TtsTransport = ::NativeTtsTransport,
    private val outputFactory: () -> TtsPlaybackOutput = ::UnavailableTtsOutput,
    private val cache: TtsSnapshotCache = TtsSnapshotCache(),
    private val elapsedMs: () -> Long = { System.nanoTime() / 1_000_000 }) {
    private var controller: TtsController? = null
    private var source: TtsActivationSource? = null
    private var instanceId = ""
    var instanceName: String = "TwiCC"
        private set
    var sessionEpoch: String? = null
        private set
    private var transport: TtsTransport? = null
    private var connectionId: String? = null
    private var output: TtsPlaybackOutput? = null
    private var resource: AudioResource? = null
    private var document: DocumentOwner? = null
    private var active = false
    private var terminal = false
    private var exporting = false

    fun configure(value: TtsActivationSource): Boolean {
        if (active && instanceId != value.instanceId) return false
        if (instanceId != value.instanceId || controller == null) {
            controller = TtsController(value.instanceId, elapsedMs = elapsedMs); instanceId = value.instanceId; document = null
        }
        source = value; instanceName = value.name
        return true
    }
    fun detachSource(value: TtsActivationSource) { if (source === value) source = null }
    private fun forget(consumed: ConsumedTtsOutcomes) = controller?.forgetOutcomes(consumed.operationIds, consumed.error)
    fun snapshot(): NativeTtsSnapshot {
        forget(cache.expire(instanceId))
        return cache.snapshot(instanceId)
    }
    private fun rejected(code: String, message: String, action: String? = null, uiMessage: TtsFrame? = null) =
        Acceptance(false, error = TtsActionError(code, message, action, uiMessage))
    private fun needsWork(value: NativeTtsSnapshot) = value.playback.readingId != null || value.listening != null

    /** Public binder commands exclude transport, timer, and player callbacks. */
    @Suppress("UNCHECKED_CAST")
    fun dispatch(command: TtsCommand): Acceptance {
        if (command is TtsCommand.SwitchInstance && command.instanceId != instanceId) {
            if (active) internal(TtsCommand.AuthorizationLost("The active instance changed.", uiMessage = ttsUiMessage("android.speech.errors.instanceChanged")))
            controller = TtsController(command.instanceId, elapsedMs = elapsedMs); instanceId = command.instanceId
            source = null; document = null
            cache.publish(controller!!.snapshot(), mirrorOnly = true)
            return Acceptance(true)
        }
        val policy = controller ?: return rejected("not_attached", "Open a saved instance before starting speech.", uiMessage = ttsUiMessage("android.speech.errors.instanceRequired"))
        // An idle bound service has no speech session to lose. Do not recreate a consumed failure.
        if (command is TtsCommand.AuthorizationLost && !active) return Acceptance(true)
        val owner = when (command) {
            is TtsCommand.Acknowledge -> command.owner
            is TtsCommand.Manual -> command.owner
            is TtsCommand.Listen -> command.owner
            is TtsCommand.SetMuted -> command.owner
            is TtsCommand.Disable -> command.owner
            is TtsCommand.StopReading -> command.owner
            else -> null
        }
        if (owner != null && owner != document) return rejected("stale_document", "Reload this page, then try speech again.", uiMessage = ttsUiMessage("android.speech.errors.staleDocument"))
        if (command is TtsCommand.Acknowledge) {
            if (command.revision !in 0..snapshot().revision) return rejected("stale_command", "Speech playback changed. Try again.", uiMessage = ttsUiMessage("android.speech.errors.playbackChanged"))
            forget(cache.acknowledge(instanceId, command.revision))
            return Acceptance(true)
        }
        if (command is TtsCommand.AttachDocument) {
            if (command.owner.instanceId != instanceId || (document != null && command.owner.generation < document!!.generation))
                return rejected("stale_document", "Reload this page, then try speech again.", uiMessage = ttsUiMessage("android.speech.errors.staleDocument"))
            document = command.owner
        } else if (command !is TtsCommand.Manual && command !is TtsCommand.Listen && command !is TtsCommand.SetMuted &&
            command !is TtsCommand.Disable && command !is TtsCommand.StopReading && command != TtsCommand.Skip &&
            command !is TtsCommand.PauseTimeline && command !is TtsCommand.ResumeTimeline &&
            command != TtsCommand.StopAll && command !is TtsCommand.SwitchInstance && command !is TtsCommand.AuthorizationLost)
            return rejected("invalid_command", "Could not change speech playback. Reload the page and try again.", uiMessage = ttsUiMessage("android.speech.errors.changeFailed"))
        if (command is TtsCommand.Manual || command is TtsCommand.Listen) {
            platform.notificationError()?.let { return Acceptance(false, error = it) }
            if (!active && source == null) return rejected("credentials_unavailable", "Reopen this instance, then start speech again.", uiMessage = ttsUiMessage("android.speech.errors.credentialsUnavailable"))
            if (terminal) return rejected("stopping", "Speech is stopping. Wait a moment, then try again.", uiMessage = ttsUiMessage("android.speech.errors.stopping"))
        }
        if (command is TtsCommand.ResumeTimeline)
            platform.notificationError()?.let { return Acceptance(false, error = it) }
        val effects = policy.dispatch(command)
        if (effects.isEmpty()) return rejected("stale_command", "Speech playback changed. Try again.", uiMessage = ttsUiMessage("android.speech.errors.playbackChanged"))
        if (command == TtsCommand.StopAll) { terminal = true; exporting = false }
        if (command is TtsCommand.SwitchInstance) { source = null; document = null }
        try { process(effects) } catch (error: Exception) {
            Logger.getLogger("TwiCC.TtsServiceOwner").log(Level.WARNING, "Speech activation failed", error)
            internal(TtsCommand.AuthorizationLost("Speech could not start. Reload the page and try again.", uiMessage = ttsUiMessage("session.speech.errors.startFailed")))
            return rejected("activation_failed", "Speech could not start. Reload the page and try again.", uiMessage = ttsUiMessage("session.speech.errors.startFailed"))
        }
        val value = snapshot()
        if (command is TtsCommand.Manual && value.playback.operationId != command.operationId)
            return rejected("unreachable", value.error?.get("message") as? String ?: "Could not reach TwiCC",
                uiMessage = value.error?.get("ui_message") as? TtsFrame
                    ?: if (value.error?.get("message") !is String) ttsUiMessage("session.speech.errors.unreachable") else null)
        return Acceptance(true, when (command) {
            is TtsCommand.Manual -> command.operationId
            is TtsCommand.Listen -> value.listening?.activationId
            else -> null
        })
    }
    fun notificationCommand(epoch: String, command: TtsCommand): Acceptance {
        if (!active || epoch != sessionEpoch || (command != TtsCommand.Skip && command != TtsCommand.StopAll &&
                command !is TtsCommand.PauseTimeline && command !is TtsCommand.ResumeTimeline))
            return rejected("stale_session", "This notification belongs to an old speech session.", uiMessage = ttsUiMessage("android.speech.errors.staleSession"))
        return dispatch(command)
    }
    fun feedback(readingId: String, generation: Long, signal: PlayerSignal) {
        if (active) internal(TtsCommand.PlayerFeedback(readingId, generation, signal))
    }
    private fun internal(command: TtsCommand) {
        if (!active && command !is TtsCommand.AuthorizationLost) return
        process(controller?.dispatch(command).orEmpty())
    }
    private fun controlsAvailable(): Boolean {
        val error = platform.notificationError() ?: return true
        internal(TtsCommand.AuthorizationLost(error.message, error.uiMessage))
        return false
    }
    private fun process(effects: List<TtsEffect>) {
        for (effect in effects) when (effect) {
            TtsEffect.RequireForeground -> {
                cache.beginActivation(instanceId)
                sessionEpoch = UUID.randomUUID().toString(); active = true; terminal = false
                output = outputFactory()
                platform.foreground(controller!!.snapshot(), sessionEpoch!!)
            }
            is TtsEffect.Connect -> connect(effect.handshake)
            is TtsEffect.Send -> {
                if (transport?.send(effect.frame) == false) connectionId?.let {
                    onEvent(transport!!, sessionEpoch!!, TtsConnectionEvent.Lost(it, 1000))
                }
            }
            is TtsEffect.Prepare -> {
                resource = effect.resource
                val epoch = sessionEpoch
                transport?.let { socket -> output?.prepare(effect.resource, socket) { signal -> platform.enqueue {
                    if (active && epoch == sessionEpoch) feedback(effect.resource.readingId, effect.resource.generation, signal)
                } } }
            }
            is TtsEffect.Play -> output?.play(effect.readingId, effect.generation)
            is TtsEffect.Cancel -> {
                output?.cancel(effect.readingId, effect.generation)
                resource?.takeIf { it.readingId == effect.readingId && it.generation == effect.generation }?.let { transport?.cancelAudio(it) }
                resource = null
            }
            is TtsEffect.Release -> {
                val socket = transport
                platform.worker { runCatching { socket?.release(effect.resource) } }
                if (resource === effect.resource) resource = null
            }
            TtsEffect.UpdateNotification -> {
                forget(cache.expire(instanceId))
                val value = cache.publish(controller!!.snapshot(), mirrorOnly = !active)
                if (active && controlsAvailable()) {
                    platform.wakeLock(needsWork(value))
                    platform.update(value, sessionEpoch!!)
                }
            }
            is TtsEffect.StopAfter -> {
                terminal = true
                val epoch = sessionEpoch
                platform.after(effect.delayMillis) {
                    if (active && epoch == sessionEpoch) internal(TtsCommand.StopTimeout(effect.epoch))
                }
            }
            TtsEffect.CloseConnection -> closeTransport()
            TtsEffect.StopService -> stop()
        }
    }
    private fun connect(handshake: TtsFrame) {
        val existing = transport
        if (existing != null) { connectionId = existing.connect(handshake); return }
        if (exporting || terminal) return
        val activation = source ?: run { internal(TtsCommand.AuthorizationLost("Reopen this instance and start speech again.", uiMessage = ttsUiMessage("android.speech.errors.credentialsUnavailable"))); return }
        source = null // Never retain an Activity/browser through the service lifetime.
        val epoch = sessionEpoch!!
        exporting = true
        activation.exportCookies(listOf("${activation.origin}/api/tts/", "${activation.origin}/ws/tts/")) { result -> platform.enqueue {
            if (!active || terminal || !exporting || sessionEpoch != epoch || instanceId != activation.instanceId) return@enqueue
            exporting = false
            if (!controlsAvailable()) return@enqueue
            result.fold({ cookies ->
                try {
                    val socket = transportFactory(TtsCredentials(activation.instanceId, activation.origin, cookies))
                    transport = socket
                    socket.event = { event -> platform.enqueue { onEvent(socket, epoch, event) } }
                    connectionId = socket.connect(handshake)
                } catch (error: Exception) {
                    Logger.getLogger("TwiCC.TtsServiceOwner").log(Level.WARNING, "Speech connection failed", error)
                    internal(TtsCommand.AuthorizationLost("Speech could not start. Reload the page and try again.", uiMessage = ttsUiMessage("session.speech.errors.startFailed")))
                }
            }, { error ->
                Logger.getLogger("TwiCC.TtsServiceOwner").log(Level.WARNING, "Browser credential export failed", error)
                internal(TtsCommand.AuthorizationLost("Speech could not start. Reload the page and try again.", uiMessage = ttsUiMessage("session.speech.errors.startFailed")))
            })
        } }
    }
    private fun onEvent(socket: TtsTransport, epoch: String, event: TtsConnectionEvent) {
        if (!active || socket !== transport || sessionEpoch != epoch || event.connectionId != connectionId) return
        if (!controlsAvailable()) return
        when (event) {
            is TtsConnectionEvent.Opened -> internal(TtsCommand.TransportOpened(event.connectionId))
            is TtsConnectionEvent.Frame -> {
                internal(TtsCommand.ServerFrame(event.connectionId, event.frame))
                if (transport === socket && event.frame["type"] in setOf("opened_session", "state") &&
                    snapshot().sessionStatus == "connected") socket.reconciled(event.connectionId)
            }
            is TtsConnectionEvent.Lost -> {
                internal(TtsCommand.TransportLost(event.connectionId))
                platform.after(event.retryAfterMillis) {
                    if (active && socket === transport && sessionEpoch == epoch && connectionId == event.connectionId)
                        internal(TtsCommand.Reconnect)
                }
            }
            is TtsConnectionEvent.AuthorizationLost -> internal(TtsCommand.AuthorizationLost("Authentication expired. Open TwiCC and sign in again.", uiMessage = ttsUiMessage("backend.tts.authenticationExpired")))
            is TtsConnectionEvent.HeartbeatDue -> internal(TtsCommand.Heartbeat)
        }
    }
    private fun closeTransport() {
        exporting = false; connectionId = null
        val socket = transport
        socket?.event = {}; transport = null
        // TLS socket closure can write close_notify. Keep it off the service main looper.
        platform.worker { socket?.close() }
    }
    private fun stop() {
        closeTransport(); output?.close(); output = null; resource = null
        active = false; terminal = false; sessionEpoch = null
        controller?.snapshot()?.let { cache.stopped(it) }
        platform.wakeLock(false); platform.stop()
    }
    fun destroy() {
        if (active) process(controller?.dispatch(TtsCommand.AuthorizationLost("Android stopped speech. Start it again.", uiMessage = ttsUiMessage("android.speech.errors.androidStopped"))).orEmpty())
        else { closeTransport(); output?.close(); output = null; platform.wakeLock(false) }
        source = null
    }
}
