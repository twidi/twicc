package com.twidi.twicc.tts

/** JSON values only. Maps cross the service boundary without credentials entering UI snapshots. */
typealias TtsFrame = Map<String, Any?>

/** Semantic presentation data stays language-neutral through retained speech state. */
fun ttsUiMessage(key: String, params: Map<String, Any?> = emptyMap()): TtsFrame =
    mapOf("key" to key, "params" to params)
data class DocumentOwner(val instanceId: String, val pageId: String, val generation: Long)
data class ListeningScope(val kind: String, val id: String?, val label: String? = null) {
    init { require(kind in setOf("global", "project", "workspace", "worktree")); require((kind == "global") == (id == null)) }
    fun wire(): TtsFrame = mapOf("kind" to kind, "id" to id)
}
data class NativeListening(val activationId: String, val pageId: String, val scope: ListeningScope,
    val boundary: String, val includeMuted: Boolean, val paused: Boolean = false)
data class NativePlayback(val readingId: String? = null, val generation: Long = 0, val operationId: String? = null,
    val identity: TtsFrame? = null, val source: String? = null, val text: String = "", val configId: String? = null,
    val status: String = "idle", val phase: String = "idle", val automatic: Boolean = false,
    val started: Boolean = false, val audioId: String? = null)
data class NativeTtsResult(val operationId: String, val started: Boolean, val start: TtsFrame, val ended: TtsFrame,
    val automatic: Boolean = false, val readingId: String? = null, val generation: Long = 0)
data class NativeTtsSnapshot(val version: Int = 1, val revision: Long, val instanceId: String,
    val sessionStatus: String, val playback: NativePlayback, val listening: NativeListening?, val pendingCount: Int,
    val error: TtsFrame?, val configurationAvailable: Boolean?, val results: List<NativeTtsResult>)

/** Internal resource handle. The service must never serialize this value into page state. */
class AudioResource(val instanceId: String, val sessionId: String, val resumeSecret: String,
    val readingId: String, val generation: Long, val audioId: String, val contentType: String) {
    override fun toString() = "AudioResource(readingId=$readingId, generation=$generation, audioId=$audioId)"
}
sealed interface PlayerSignal {
    data object Buffering : PlayerSignal
    data object Started : PlayerSignal
    data object Resumed : PlayerSignal
    data object Settling : PlayerSignal
    data object AuthorizationLost : PlayerSignal
    data class Ended(val ok: Boolean, val error: String? = null, val uiMessage: TtsFrame? = null) : PlayerSignal
}
sealed interface TtsCommand {
    data class AttachDocument(val owner: DocumentOwner) : TtsCommand
    data class Acknowledge(val owner: DocumentOwner, val revision: Long) : TtsCommand
    data class Manual(val owner: DocumentOwner, val operationId: String, val request: TtsFrame) : TtsCommand
    data class Listen(val owner: DocumentOwner, val scope: ListeningScope, val includeMuted: Boolean, val boundary: String, val pageId: String = owner.pageId) : TtsCommand
    data class SetMuted(val owner: DocumentOwner, val activationId: String, val includeMuted: Boolean) : TtsCommand
    data class Disable(val owner: DocumentOwner, val activationId: String) : TtsCommand
    data class PauseTimeline(val activationId: String) : TtsCommand
    data class ResumeTimeline(val activationId: String, val boundary: String) : TtsCommand
    data class StopReading(val owner: DocumentOwner, val readingId: String, val identity: TtsFrame?) : TtsCommand
    data object Skip : TtsCommand
    data object StopAll : TtsCommand
    data class StopTimeout(val epoch: String) : TtsCommand
    data object Reconnect : TtsCommand
    data object Heartbeat : TtsCommand
    data class TransportOpened(val connectionId: String) : TtsCommand
    data class TransportLost(val connectionId: String) : TtsCommand
    data class ServerFrame(val connectionId: String, val frame: TtsFrame) : TtsCommand
    data class PlayerFeedback(val readingId: String, val generation: Long, val signal: PlayerSignal) : TtsCommand
    data class SwitchInstance(val instanceId: String) : TtsCommand
    data class AuthorizationLost(val message: String, val uiMessage: TtsFrame? = null) : TtsCommand
}
sealed interface TtsEffect {
    data object RequireForeground : TtsEffect
    data class Connect(val handshake: TtsFrame) : TtsEffect
    data class Send(val frame: TtsFrame) : TtsEffect
    data class Prepare(val resource: AudioResource) : TtsEffect
    data class Play(val readingId: String, val generation: Long) : TtsEffect
    data class Cancel(val readingId: String, val generation: Long) : TtsEffect
    data class Release(val resource: AudioResource) : TtsEffect
    data object UpdateNotification : TtsEffect
    data object CloseConnection : TtsEffect
    data class StopAfter(val epoch: String, val delayMillis: Long = 5000) : TtsEffect
    data object StopService : TtsEffect
}

