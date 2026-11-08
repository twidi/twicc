package com.twidi.twicc.i18n

import org.junit.Assert.*
import org.junit.Test

class MessageRendererTest {
    private val text = CompiledForm(listOf(MessageToken.Text("Cancel")))
    private val wrapper = CompiledForm(listOf(MessageToken.Named("body")), setOf("body"))
    private val catalog = mapOf("en" to mapOf(
        "cancel" to CompiledMessage(listOf(text)),
        "wrapper" to CompiledMessage(listOf(wrapper)),
        "subset" to CompiledMessage(listOf(text, wrapper, text)),
    ))
    private val renderer = MessageRenderer(catalog) { _, value -> value.toString() }

    @Test fun rejectsBrokenDescriptors() {
        assertNull(renderer.render("en", UiMessage("unknown")))
        assertNull(renderer.render("en", UiMessage("wrapper")))
        assertNull(renderer.render("en", UiMessage("wrapper", mapOf("body" to null))))
        assertNull(renderer.render("en", UiMessage("cancel", count = Double.POSITIVE_INFINITY)))
        assertNull(renderer.render("en", UiMessage("cancel", mapOf("bad" to Double.NaN))))
        assertNull(renderer.render("en", UiMessage("cancel", mapOf("bad" to listOf("x")))))
        var nested = UiMessage("cancel")
        repeat(3) { nested = UiMessage("wrapper", mapOf("body" to nested)) }
        assertEquals("Cancel", renderer.render("en", nested))
        nested = UiMessage("wrapper", mapOf("body" to nested))
        assertNull(renderer.render("en", nested))
        assertEquals("Cancel", renderer.render("en", UiMessage("cancel", (1..63).associate { "$it" to UiMessage("cancel") })))
        assertNull(renderer.render("en", UiMessage("cancel", (1..64).associate { "$it" to UiMessage("cancel") })))
    }

    @Test fun fallsBackToEnglishAndRequiresOnlySelectedFormParameters() {
        assertEquals("Cancel", renderer.render("de", UiMessage("cancel")))
        assertEquals("Cancel", renderer.render("en", UiMessage("subset", count = 0.0)))
        assertNull(renderer.render("en", UiMessage("subset", count = 1.0)))
        assertNull(renderer.render("en", UiMessage("subset", count = 0.5)))
    }
    @Test fun preservesNestedDescriptorsAndFormatsOnlyDeclaredNumbers() {
        val form = CompiledForm(listOf(MessageToken.Named("amount"), MessageToken.Text(" / "),
            MessageToken.Named("status"), MessageToken.Text(" / "), MessageToken.Named("body")),
            setOf("amount", "status", "body"))
        val definitions = mapOf("wrapper" to CompiledMessage(listOf(form), mapOf("amount" to "decimal")))
        val renderer = MessageRenderer(mapOf(
            "en" to definitions + ("cancel" to CompiledMessage(listOf(text))),
            "fr" to definitions + ("cancel" to CompiledMessage(listOf(CompiledForm(listOf(MessageToken.Text("Annuler")))))),
        )) { locale, original ->
            assertEquals("12345.6700", original)
            if (locale == "fr") "12 345,6700" else "12,345.6700"
        }
        val message = UiMessage("wrapper", mapOf("amount" to "12345.6700", "status" to 12345, "body" to UiMessage("cancel")))
        assertEquals("12,345.6700 / 12345 / Cancel", renderer.render("en", message))
        assertEquals("12 345,6700 / 12345 / Annuler", renderer.render("fr", message))
    }

}
