package com.twidi.twicc.i18n

/** Semantic presentation data. Keep this descriptor until the text is displayed. */
data class UiMessage(
    val key: String,
    val params: Map<String, Any?> = emptyMap(),
    val count: Double? = null,
)

sealed interface MessageToken {
    data class Text(val value: String) : MessageToken
    data class Named(val name: String) : MessageToken
}

data class CompiledForm(val tokens: List<MessageToken>, val parameters: Set<String> = emptySet())
data class CompiledMessage(
    val forms: List<CompiledForm>,
    val numberFormats: Map<String, String> = emptyMap(),
)

typealias MessageCatalog = Map<String, Map<String, CompiledMessage>>
