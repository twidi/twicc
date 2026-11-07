package com.twidi.twicc.tts

import org.json.JSONArray
import org.json.JSONObject
import java.util.logging.Level
import java.util.logging.Logger

/** Common bridge mapping. Both browser engines call this after validating the top-level document. */
class NativeTtsBridge(
    private val enabled: () -> Boolean,
    private val owner: () -> DocumentOwner,
    private val snapshot: () -> NativeTtsSnapshot,
    private val documentToken: () -> String?,
    private val dispatch: (TtsCommand) -> Acceptance,
) {
    fun receive(command: String, args: JSONObject): JSONObject {
        if (!enabled()) return acceptance(Acceptance(false, error = TtsActionError("unsupported", "Speech is unavailable. Update the Android app and try again.")))
        val expected = documentToken() ?: return acceptance(Acceptance(false, error = TtsActionError("initializing", "Speech is getting ready. Try again in a moment.")))
        if (args.opt("documentToken") != expected) return acceptance(Acceptance(false,
            error = TtsActionError("stale_document", "Reload this page, then try speech again.")))
        return try {
            val document = owner()
            val control = when (command) {
                "tts.state" -> null
                "tts.attach" -> TtsCommand.AttachDocument(document)
                "tts.acknowledge" -> TtsCommand.Acknowledge(document, args.getLong("revision").also {
                    require(it in 0..9007199254740991L)
                })
                "tts.manual" -> TtsCommand.Manual(document, args.string("operationId", 96), args.getJSONObject("request").frame().also {
                    require(it["kind"] in setOf("text", "summary") && it["text"] is String && it["speech"] is Map<*, *>)
                })
                "tts.listen" -> {
                    val scope = args.getJSONObject("scope")
                    val kind = scope.string("kind", 32)
                    val id = if (scope.isNull("id")) null else scope.string("id")
                    val label = if (scope.isNull("label")) null else scope.string("label")
                    val boundary = args.string("boundary", 40)
                    require(Regex("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{6}Z").matches(boundary))
                    TtsCommand.Listen(document, ListeningScope(kind, id, label), args.boolean("includeMuted"), boundary, args.string("pageId"))
                }
                "tts.set_muted" -> TtsCommand.SetMuted(document, args.string("activationId"), args.boolean("includeMuted"))
                "tts.disable" -> TtsCommand.Disable(document, args.string("activationId"))
                "tts.skip" -> if (args.has("operationId") && !args.has("readingId")) {
                    val current = snapshot().playback
                    require(current.operationId == args.string("operationId"))
                    val readingId = requireNotNull(current.readingId)
                    TtsCommand.StopReading(document, readingId, current.identity)
                } else if (args.has("readingId")) TtsCommand.StopReading(document, args.string("readingId"),
                    if (args.isNull("identity")) null else args.getJSONObject("identity").frame()) else TtsCommand.Skip
                "tts.stop_all" -> TtsCommand.StopAll
                else -> throw IllegalArgumentException("Unknown speech command")
            }
            if (control == null) JSONObject().put("snapshot", snapshot().json())
            else acceptance(dispatch(control)).put("snapshot", snapshot().json())
        } catch (error: Exception) {
            Logger.getLogger("TwiCC.NativeTtsBridge").log(Level.WARNING, "Speech request failed", error)
            acceptance(Acceptance(false, error = TtsActionError("invalid_request", "Speech could not start. Reload the page and try again.")))
        }
    }
    private fun acceptance(value: Acceptance) = JSONObject().put("accepted", value.accepted)
        .put("operationId", value.operationId ?: JSONObject.NULL).apply {
            value.error?.let { error ->
                put("error", JSONObject().put("code", error.code).put("message", error.message)
                    .put("action", error.action ?: JSONObject.NULL).apply {
                        error.uiMessage?.let { put("ui_message", JSONObject(it)) }
                    })
            }
        }
}
private fun JSONObject.string(key: String, limit: Int = 4096): String = (get(key) as? String)?.also {
    require(it.isNotEmpty() && it.length <= limit)
} ?: throw IllegalArgumentException(key)
private fun JSONObject.boolean(key: String): Boolean = get(key) as? Boolean ?: throw IllegalArgumentException(key)
private fun JSONObject.frame(): TtsFrame = keys().asSequence().associateWith { jsonValue(get(it)) }
private fun jsonValue(value: Any?): Any? = when (value) {
    null, JSONObject.NULL -> null
    is JSONObject -> value.frame()
    is JSONArray -> (0 until value.length()).map { jsonValue(value.get(it)) }
    else -> value
}
private fun NativeTtsSnapshot.json(): JSONObject = JSONObject().put("version", version).put("revision", revision)
    .put("instanceId", instanceId).put("sessionStatus", sessionStatus).put("pendingCount", pendingCount)
    .put("configurationAvailable", configurationAvailable ?: JSONObject.NULL).put("error", error?.let { JSONObject(it) } ?: JSONObject.NULL)
    .put("playback", JSONObject().put("readingId", playback.readingId ?: JSONObject.NULL).put("generation", playback.generation)
        .put("operationId", playback.operationId ?: JSONObject.NULL).put("identity", playback.identity?.let { JSONObject(it) } ?: JSONObject.NULL)
        .put("source", playback.source ?: JSONObject.NULL).put("text", playback.text).put("configId", playback.configId ?: JSONObject.NULL)
        .put("status", playback.status).put("phase", playback.phase).put("automatic", playback.automatic).put("started", playback.started))
    .put("listening", listening?.let { value -> JSONObject().put("activationId", value.activationId).put("pageId", value.pageId)
        .put("scope", JSONObject().put("kind", value.scope.kind).put("id", value.scope.id ?: JSONObject.NULL).put("label", value.scope.label ?: JSONObject.NULL))
        .put("boundary", value.boundary).put("includeMuted", value.includeMuted).put("paused", value.paused) } ?: JSONObject.NULL)
    .put("results", JSONArray().apply { results.forEach { result -> put(JSONObject().put("operationId", result.operationId)
        .put("started", result.started).put("automatic", result.automatic).put("readingId", result.readingId ?: JSONObject.NULL)
        .put("generation", result.generation).put("start", JSONObject(result.start)).put("ended", JSONObject(result.ended))) } })
