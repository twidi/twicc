package com.twidi.twicc.i18n

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class NativeMessagesTest {
    @Test fun matchesVueOracleFixtures() {
        val fixture = JSONObject(checkNotNull(javaClass.getResource("/i18n/render-cases.json")).readText())
        val renderer = MessageRenderer(UiMessageJson.decodeCatalog(fixture.getJSONObject("catalog")), NativeMessages::formatNumber)
        val cases = fixture.getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            val message = checkNotNull(UiMessageJson.decode(case.getJSONObject("message")))
            assertEquals("case $i ${case.getString("locale")} ${message.key}", case.getString("expected"),
                renderer.render(case.getString("locale"), message))
        }
    }

    @Test fun preservesRecursiveJsonAndRejectsInvalidValues() {
        val message = UiMessage("wrapper", mapOf("body" to UiMessage("cancel"), "optional" to null), 2.0)
        assertEquals(message, UiMessageJson.decode(UiMessageJson.encode(message)))
        for (source in listOf("{}", "{\"key\":4}", "{\"key\":\"x\",\"params\":null}",
            "{\"key\":\"x\",\"params\":[]}", "{\"key\":\"x\",\"count\":null}",
            "{\"key\":\"x\",\"count\":\"2\"}", "{\"key\":\"x\",\"params\":{\"x\":[]}}")) {
            assertNull(source, UiMessageJson.decode(JSONObject(source)))
        }
        var nested = JSONObject().put("key", "cancel")
        repeat(3) { nested = JSONObject().put("key", "wrapper").put("params", JSONObject().put("body", nested)) }
        assertNotNull(UiMessageJson.decode(nested))
        assertNull(UiMessageJson.decode(JSONObject().put("key", "wrapper").put("params", JSONObject().put("body", nested))))
        val params = JSONObject()
        repeat(63) { params.put("$it", JSONObject().put("key", "cancel")) }
        assertNotNull(UiMessageJson.decode(JSONObject().put("key", "cancel").put("params", params)))
        params.put("64", JSONObject().put("key", "cancel"))
        assertNull(UiMessageJson.decode(JSONObject().put("key", "cancel").put("params", params)))
    }

    @Test fun formatsOnlyDeclaredNumbersWithoutLosingDecimalPrecision() {
        assertEquals("12\u202f345\u202f678\u202f901\u202f234\u202f567\u202f890,6700",
            NativeMessages.formatNumber("fr", "12345678901234567890.6700"))
        assertEquals("-0,00100", NativeMessages.formatNumber("fr", "-0.00100"))
        assertNull(NativeMessages.formatNumber("fr", "12,34"))
        assertNull(NativeMessages.formatNumber("fr", Double.NaN))
        val renderer = MessageRenderer(UiMessageJson.decodeCatalog(JSONObject(
            checkNotNull(javaClass.getResource("/i18n/messages.json")).readText())), NativeMessages::formatNumber)
        assertEquals("The server returned HTTP 12345. Check your tunnel or server, then retry.", renderer.render("en", UiMessage("android.browser.errors.httpStatus", mapOf("status" to 12345))))
        assertFalse(checkNotNull(renderer.render("fr", UiMessage("session.notifications.creditsUsed", mapOf("used" to "12345.6700")))).contains("12345.6700"))
    }
    @Test fun replacesIncompatibleIdentityWithPackagedGenericMessage() {
        val renderer = MessageRenderer(UiMessageJson.decodeCatalog(JSONObject(
            checkNotNull(javaClass.getResource("/i18n/messages.json")).readText())), NativeMessages::formatNumber)
        assertEquals(renderer.render("fr", UiMessage("common.android.errors.actionFailed")),
            NativeMessages.text(renderer, "fr", UiMessage("unknown.key")))
    }

}
