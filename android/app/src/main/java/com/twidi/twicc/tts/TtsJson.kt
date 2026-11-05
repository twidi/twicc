package com.twidi.twicc.tts

import org.json.JSONArray
import org.json.JSONObject

/** Android's existing JSON implementation. The pure controller uses maps and never parses JSON. */
object TtsJson {
    private const val MAX_BYTES = 12 * 1024 * 1024
    fun encode(frame: TtsFrame): String = toJson(frame).toString().also {
        require(it.toByteArray(Charsets.UTF_8).size <= MAX_BYTES) { "TTS command is too large" }
    }
    fun decode(text: String): TtsFrame {
        require(text.toByteArray(Charsets.UTF_8).size <= MAX_BYTES) { "TTS frame is too large" }
        return fromObject(JSONObject(text))
    }
    private fun toJson(value: Any?): Any = when (value) {
        null -> JSONObject.NULL
        is Map<*, *> -> JSONObject().apply { value.forEach { (key, item) -> require(key is String); put(key, toJson(item)) } }
        is List<*> -> JSONArray().apply { value.forEach { put(toJson(it)) } }
        is String, is Number, is Boolean -> value
        else -> error("Not a JSON value")
    }
    private fun fromObject(value: JSONObject): TtsFrame = value.keys().asSequence().associateWith { fromJson(value.get(it)) }
    private fun fromJson(value: Any?): Any? = when (value) {
        JSONObject.NULL -> null
        is JSONObject -> fromObject(value)
        is JSONArray -> (0 until value.length()).map { fromJson(value.get(it)) }
        else -> value
    }
}
