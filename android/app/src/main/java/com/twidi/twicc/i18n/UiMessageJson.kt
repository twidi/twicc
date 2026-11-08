package com.twidi.twicc.i18n

import org.json.JSONException
import org.json.JSONObject

/** JSON is an app boundary. The policy module only receives typed data. */
object UiMessageJson {
    fun decode(value: JSONObject): UiMessage? = try {
        var nodes = 0
        fun decodeNode(value: JSONObject, depth: Int): UiMessage {
            require(depth <= 4 && ++nodes <= 64)
            val key = value.get("key")
            require(key is String && key.isNotEmpty())
            val count = if (value.has("count")) {
                val number = value.get("count")
                require(number is Number && number.toDouble().isFinite())
                number.toDouble()
            } else null
            val rawParams = if (value.has("params")) value.get("params") else JSONObject()
            require(rawParams is JSONObject)
            val params = rawParams.keys().asSequence().associateWith { name ->
                when (val parameter = rawParams.get(name)) {
                    JSONObject.NULL -> null
                    is JSONObject -> decodeNode(parameter, depth + 1)
                    is String, is Boolean -> parameter
                    is Number -> parameter.also { require(it.toDouble().isFinite()) }
                    else -> throw IllegalArgumentException("Unsupported message parameter")
                }
            }
            return UiMessage(key, params, count)
        }
        decodeNode(value, 1)
    } catch (_: IllegalArgumentException) {
        null
    } catch (_: JSONException) {
        null
    }

    /** Invalid local descriptors fail at the boundary instead of producing partial wire data. */
    fun encode(message: UiMessage): JSONObject {
        var nodes = 0
        fun encodeNode(message: UiMessage, depth: Int): JSONObject {
            require(depth <= 4 && ++nodes <= 64 && message.key.isNotEmpty())
            require(message.count?.isFinite() != false)
            val params = JSONObject()
            for ((name, value) in message.params) {
                params.put(name, when (value) {
                    null -> JSONObject.NULL
                    is UiMessage -> encodeNode(value, depth + 1)
                    is String, is Boolean -> value
                    is Number -> value.also { require(it.toDouble().isFinite()) }
                    else -> throw IllegalArgumentException("Unsupported message parameter")
                })
            }
            return JSONObject().put("key", message.key).put("params", params).also {
                if (message.count != null) it.put("count", message.count)
            }
        }
        return encodeNode(message, 1)
    }

    fun decodeCatalog(value: JSONObject): MessageCatalog {
        require(value.getInt("format") == 1)
        val locales = value.getJSONObject("locales")
        return locales.keys().asSequence().associateWith { locale ->
            val messages = locales.getJSONObject(locale)
            messages.keys().asSequence().associateWith { key ->
                val message = messages.getJSONObject(key)
                val forms = message.getJSONArray("forms")
                require(forms.length() in 1..3)
                val formats = message.getJSONObject("numberFormats")
                CompiledMessage((0 until forms.length()).map { index ->
                    val form = forms.getJSONObject(index)
                    val tokens = form.getJSONArray("tokens")
                    val parameters = form.getJSONArray("parameters")
                    CompiledForm((0 until tokens.length()).map { position ->
                        val token = tokens.getJSONObject(position)
                        when (token.getString("type")) {
                            "text" -> MessageToken.Text(token.getString("value"))
                            "named" -> MessageToken.Named(token.getString("name"))
                            else -> throw IllegalArgumentException("Unsupported message token")
                        }
                    }, (0 until parameters.length()).map { parameters.getString(it) }.toSet())
                }, formats.keys().asSequence().associateWith { name ->
                    formats.getString(name).also { require(it == "decimal") }
                })
            }
        }
    }
}
