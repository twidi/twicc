package com.twidi.twicc.i18n

import kotlin.math.abs
import kotlin.math.min

/** Select compiled forms and interpolate tokens, without Android or Vue dependencies. */
class MessageRenderer(
    private val catalog: MessageCatalog,
    private val numberFormatter: (locale: String, value: Any) -> String?,
) {
    fun render(locale: String, message: UiMessage): String? {
        var nodes = 0
        fun renderNode(message: UiMessage, depth: Int): String? {
            if (depth > 4 || ++nodes > 64 || message.count?.isFinite() == false) return null
            val language = if (catalog[locale]?.containsKey(message.key) == true) locale else "en"
            val definition = catalog[language]?.get(message.key) ?: return null
            if (definition.forms.size !in 1..3) return null
            val params = mutableMapOf<String, Any?>()
            for ((name, value) in message.params) {
                params[name] = when (value) {
                    null, is String, is Boolean -> value
                    is Number -> if (value.toDouble().isFinite()) value else return null
                    is UiMessage -> renderNode(value, depth + 1) ?: return null
                    else -> return null
                }
            }
            // Vue gives named count/n priority over the explicit plural argument.
            val choice = (params["count"] as? Number)?.toDouble()
                ?: (params["n"] as? Number)?.toDouble() ?: message.count ?: -1.0
            val absolute = abs(choice)
            val index = when (definition.forms.size) {
                1 -> 0.0
                2 -> if (language == "fr") {
                    if (absolute < 2) 0.0 else 1.0
                } else if (absolute == 1.0) 0.0 else 1.0
                else -> if (language == "fr") {
                    if (choice == 0.0) 0.0 else if (absolute == 1.0) 1.0 else 2.0
                } else min(absolute, 2.0)
            }
            if (index % 1.0 != 0.0) return null
            val form = definition.forms.getOrNull(index.toInt()) ?: return null
            if (message.count != null) {
                params.putIfAbsent("count", message.count)
                params.putIfAbsent("n", message.count)
            }
            if (form.parameters.any { params[it] == null }) return null
            return buildString {
                for (token in form.tokens) {
                    when (token) {
                        is MessageToken.Text -> append(token.value)
                        is MessageToken.Named -> {
                            val value = params[token.name] ?: return null
                            append(if (definition.numberFormats[token.name] == "decimal") {
                                numberFormatter(locale, value) ?: return null
                            } else if (value is Double && value % 1.0 == 0.0 && abs(value) < 1e21) {
                                // JSON and Vue show integer-valued numbers without a .0 suffix.
                                java.math.BigDecimal.valueOf(value).stripTrailingZeros().toPlainString()
                            } else value.toString())
                        }
                    }
                }
            }
        }
        return renderNode(message, 1)
    }
}
