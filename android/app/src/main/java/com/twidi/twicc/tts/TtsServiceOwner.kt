package com.twidi.twicc.tts

import java.util.UUID

/** Action names are native UI actions, never credentials or Android Intent payloads. */
data class TtsActionError(val code: String, val message: String, val action: String? = null)
data class Acceptance(val accepted: Boolean, val operationId: String? = null, val error: TtsActionError? = null)
class TtsActivationSource(val instanceId: String, val name: String, val origin: String,
    val exportCookies: (List<String>, (Result<List<TtsCookie>>) -> Unit) -> Unit)

interface TtsServicePlatform {
    fun notificationError(): TtsActionError?
    fun batteryExempt(): Boolean
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
        feedback(PlayerSignal.Ended(false, "Native speech output is not available in this build."))
    }
    override fun play(readingId: String, generation: Long) {}
    override fun cancel(readingId: String, generation: Long) {}
    override fun close() {}
}

/** Only safe terminal data survives service destruction. Nothing here can resume a backend session. */
class TtsSnapshotCache {
    private var revision = 0L
    private var last: NativeTtsSnapshot? = null
    private val results = linkedMapOf<String, Pair<String, NativeTtsResult>>()
    fun publish(value: NativeTtsSnapshot): NativeTtsSnapshot {
        value.results.forEach { results[it.operationId] = value.instanceId to it }
        while (results.size > 64) results.remove(results.keys.first())
        return value.copy(revision = ++revision,
            results = results.values.filter { it.first == value.instanceId }.map { it.second }).also { last = it }
    }
    fun snapshot(instanceId: String): NativeTtsSnapshot = last?.takeIf { it.instanceId == instanceId }
        ?: NativeTtsSnapshot(revision = revision, instanceId = instanceId, sessionStatus = "idle",
            playback = NativePlayback(), listening = null, pendingCount = 0, error = null,
            configurationAvailable = null, results = results.values.filter { it.first == instanceId }.map { it.second })
    fun stopped(value: NativeTtsSnapshot) = publish(value.copy(playback = NativePlayback(), listening = null, pendingCount = 0,
        sessionStatus = if (value.sessionStatus == "error") "error" else "stopped"))
}

/** The service main looper is the sole state owner. Binding and document replacement never drive lifetime. */
class TtsServiceOwner(private val platform: TtsServicePlatform,
    private val transportFactory: (TtsCredentials) -> TtsTransport = ::NativeTtsTransport,
    private val outputFactory: () -> TtsPlaybackOutput = ::UnavailableTtsOutput,
    private val cache: TtsSnapshotCache = TtsSnapshotCache()) {
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
            controller = TtsController(value.instanceId); instanceId = value.instanceId; document = null
        }
        source = value; instanceName = value.name
        return true
    }
    fun detachSource(value: TtsActivationSource) { if (source === value) source = null }
    fun snapshot(): NativeTtsSnapshot = cache.snapshot(instanceId)
    private fun rejected(code: String, message: String, action: String? = null) = Acceptance(false, error = TtsActionError(code, message, action))
    private fun needsWork(value: NativeTtsSnapshot) = value.playback.readingId != null || value.listening != null

    /** Public binder commands exclude transport, timer, and player callbacks. */
    fun dispatch(command: TtsCommand): Acceptance {
        if (command is TtsCommand.SwitchInstance && command.instanceId != instanceId) {
            if (active) internal(TtsCommand.AuthorizationLost("The active instance changed."))
            controller = TtsController(command.instanceId); instanceId = command.instanceId
            source = null; document = null
            cache.publish(controller!!.snapshot())
            return Acceptance(true)
        }
        val policy = controller ?: return rejected("not_attached", "Open a saved instance before starting speech.")
        val owner = when (command) {
            is TtsCommand.Manual -> command.owner
            is TtsCommand.Listen -> command.owner
            is TtsCommand.SetMuted -> command.owner
            is TtsCommand.Disable -> command.owner
            is TtsCommand.StopReading -> command.owner
            else -> null
        }
        if (owner != null && owner != document) return rejected("stale_document", "This page no longer owns speech controls.")
        if (command is TtsCommand.AttachDocument) {
            if (command.owner.instanceId != instanceId || (document != null && command.owner.generation < document!!.generation))
                return rejected("stale_document", "This page no longer owns speech controls.")
            document = command.owner
        } else if (command !is TtsCommand.Manual && command !is TtsCommand.Listen && command !is TtsCommand.SetMuted &&
            command !is TtsCommand.Disable && command !is TtsCommand.StopReading && command != TtsCommand.Skip &&
            command != TtsCommand.StopAll && command !is TtsCommand.SwitchInstance && command !is TtsCommand.AuthorizationLost)
            return rejected("invalid_command", "This speech command is internal.")
        if (command is TtsCommand.Manual || command is TtsCommand.Listen) {
            platform.notificationError()?.let { return Acceptance(false, error = it) }
            if (command is TtsCommand.Listen && !platform.batteryExempt())
                return rejected("battery_restricted", "Allow unrestricted battery use for screen-off timeline listening.", "battery_settings")
            if (!active && source == null) return rejected("credentials_unavailable", "Reopen this instance, then start speech again.")
            if (terminal) return rejected("stopping", "Speech is stopping. Start again after cleanup.")
        }
        val effects = policy.dispatch(command)
        if (effects.isEmpty()) return rejected("stale_command", "This speech command no longer matches the active reading.")
        if (command == TtsCommand.StopAll) { terminal = true; exporting = false }
        if (command is TtsCommand.SwitchInstance) { source = null; document = null }
        try { process(effects) } catch (_: Exception) {
            internal(TtsCommand.AuthorizationLost("Native speech could not start. Reopen TwiCC and try again."))
            return rejected("activation_failed", "Native speech could not start. Reopen TwiCC and try again.")
        }
        val value = snapshot()
        if (command is TtsCommand.Manual && value.playback.operationId != command.operationId)
            return rejected("unreachable", value.error?.get("message") as? String ?: "Could not reach TwiCC")
        return Acceptance(true, when (command) {
            is TtsCommand.Manual -> command.operationId
            is TtsCommand.Listen -> value.listening?.activationId
            else -> null
        })
    }
    fun notificationCommand(epoch: String, command: TtsCommand): Acceptance {
        if (!active || epoch != sessionEpoch || (command != TtsCommand.Skip && command != TtsCommand.StopAll))
            return rejected("stale_session", "This notification belongs to an old speech session.")
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
        internal(TtsCommand.AuthorizationLost(error.message))
        return false
    }
    private fun process(effects: List<TtsEffect>) {
        for (effect in effects) when (effect) {
            TtsEffect.RequireForeground -> {
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
                val value = cache.publish(controller!!.snapshot())
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
        val activation = source ?: run { internal(TtsCommand.AuthorizationLost("Reopen this instance and start speech again.")); return }
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
                } catch (_: Exception) { internal(TtsCommand.AuthorizationLost("Could not open the native speech connection. Reopen TwiCC and sign in again.")) }
            }, { error -> internal(TtsCommand.AuthorizationLost(error.message ?: "Could not export browser credentials. Reopen TwiCC and sign in again.")) })
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
            is TtsConnectionEvent.AuthorizationLost -> internal(TtsCommand.AuthorizationLost("Authentication expired. Open TwiCC and sign in again."))
            is TtsConnectionEvent.HeartbeatDue -> internal(TtsCommand.Heartbeat)
        }
    }
    private fun closeTransport() {
        exporting = false; connectionId = null
        transport?.event = {}; transport?.close(); transport = null
    }
    private fun stop() {
        closeTransport(); output?.close(); output = null; resource = null
        active = false; terminal = false; sessionEpoch = null
        controller?.snapshot()?.let { cache.stopped(it) }
        platform.wakeLock(false); platform.stop()
    }
    fun destroy() {
        if (active) process(controller?.dispatch(TtsCommand.AuthorizationLost("Android stopped native speech. Start again to continue.")).orEmpty())
        else { closeTransport(); output?.close(); output = null; platform.wakeLock(false) }
        source = null
    }
}
