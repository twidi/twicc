package com.twidi.twicc

import com.twidi.twicc.i18n.AppLanguage
import com.twidi.twicc.tts.TtsDocumentLease
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AppLanguageBridgeTest {
    private class Fixture {
        var persisted = AppLanguage.INITIAL
        var writes = 0
        val lease = TtsDocumentLease()
        private val language = AppLanguageBridge({ persisted }, { lease.token }) { locale, revision ->
            persisted.select(locale, revision).also {
                if (it.accepted && it.state != persisted) { persisted = it.state; writes++ }
            }
        }
        val bridge = AndroidAppBridge({ JSONObject() }, { _, _ -> }, { _, _ -> null }, appLanguage = language::receive)
        fun request(command: String, token: String? = null, revision: Any = 0, locale: Any = "fr"): JSONObject =
            JSONObject(bridge.receive(JSONObject().put("protocol", 1).put("id", "language").put("command", command)
                .put("locale", locale).put("expectedRevision", revision).put("documentToken", token).toString()))
        fun commit(token: String) { assertTrue(lease.commit(lease.begin(), token)) }
    }

    @Test fun readsLanguageWithoutLoginOrTts() {
        val f = Fixture()
        val response = f.request("locale.get")
        assertTrue(response.getBoolean("ok"))
        val state = response.getJSONObject("state")
        assertEquals("en", state.getString("locale"))
        assertEquals(0L, state.getLong("revision"))
        val supportedLocales = state.getJSONArray("supportedLocales")
        assertEquals(listOf("en", "de", "es", "fr", "pt-BR"),
            (0 until supportedLocales.length()).map { supportedLocales.getString(it) })
        val full = f.request("getState").getJSONObject("state")
        assertEquals(1, full.getJSONObject("capabilities").getInt("appLanguage"))
        assertEquals(state.toString(), full.getJSONObject("appLanguage").toString())
        assertEquals(0, f.writes)
    }

    @Test fun acceptsCurrentDocumentSelection() {
        val f = Fixture(); f.commit("current")
        val response = f.request("locale.set", "current")
        assertTrue(response.getBoolean("ok"))
        assertEquals(AppLanguage("fr", 1), f.persisted)
        assertEquals(1L, response.getJSONObject("state").getLong("revision"))
        assertEquals(1, f.writes)
        assertFalse(f.request("locale.set", "current", 0).getBoolean("ok"))
        assertEquals(AppLanguage("fr", 1), f.persisted)
        assertTrue(f.request("locale.set", "current", 1).getBoolean("ok"))
        assertEquals(1, f.writes)
    }

    @Test fun rejectsRetiredDocument() {
        val f = Fixture(); f.commit("old"); f.commit("new")
        assertRejected(f, f.request("locale.set", "old"), "android.language.errors.staleDocument")
        f.lease.begin()
        assertRejected(f, f.request("locale.set", "new"), "android.language.errors.staleDocument")
    }

    @Test fun rejectsStaleRevision() {
        val f = Fixture(); f.commit("current")
        f.persisted = AppLanguage("de", 4)
        assertRejected(f, f.request("locale.set", "current", 3), "android.language.errors.changed")
        assertEquals(AppLanguage("de", 4), f.persisted)
    }

    @Test fun rejectsSubframeAndForeignOrigin() {
        val f = Fixture(); f.commit("current")
        val instance = InstanceUrl.parse("https://twicc.example")
        for ((origin, topLevel) in listOf("https://twicc.example" to false, "https://foreign.example" to true)) {
            if (AndroidAppPolicy.acceptsFrame(instance, origin, topLevel)) f.request("locale.set", "current")
        }
        assertEquals(AppLanguage.INITIAL, f.persisted)
        assertEquals(0, f.writes)
    }

    @Test fun rejectsUnsupportedLocalesAndMalformedRevisions() {
        val f = Fixture(); f.commit("current")
        assertRejected(f, f.request("locale.set", "current", locale = "it"), "android.language.errors.unsupported")
        for (revision in listOf<Any>(-1, 0.5, "0", true, AppLanguage.MAX_REVISION + 1)) {
            assertRejected(f, f.request("locale.set", "current", revision), "android.language.errors.invalidRevision")
        }
    }

    @Test fun failedPersistenceReturnsCurrentStateWithoutSuccessfulReply() {
        val language = AppLanguageBridge({ AppLanguage.INITIAL }, { "current" }) { _, _ ->
            com.twidi.twicc.i18n.LanguageSelection(AppLanguage.INITIAL, false,
                com.twidi.twicc.i18n.UiMessage("android.language.errors.saveFailed"))
        }
        val bridge = AndroidAppBridge({ JSONObject() }, { _, _ -> }, { _, _ -> null }, appLanguage = language::receive)
        val response = JSONObject(bridge.receive("""{"protocol":1,"id":"save","command":"locale.set","documentToken":"current","locale":"fr","expectedRevision":0}"""))
        assertFalse(response.getBoolean("ok"))
        assertEquals("en", response.getJSONObject("state").getString("locale"))
        assertEquals(0L, response.getJSONObject("state").getLong("revision"))
        assertEquals("android.language.errors.saveFailed", response.getJSONObject("ui_message").getString("key"))
    }

    @Test fun applicationActionFailuresKeepSemanticIdentity() {
        val message = com.twidi.twicc.i18n.UiMessage("android.browser.errors.instanceRemoved")
        val bridge = AndroidAppBridge({ JSONObject() }, { _, _ -> }, { _, _ -> message })
        val response = JSONObject(bridge.receive("""{"protocol":1,"id":"action","command":"switchInstance","instanceId":"gone"}"""))
        assertFalse(response.getBoolean("ok"))
        assertEquals(message.key, response.getJSONObject("ui_message").getString("key"))
        assertFalse(response.has("error"))
    }

    private fun assertRejected(f: Fixture, response: JSONObject, key: String) {
        assertFalse(response.getBoolean("ok"))
        assertEquals(f.persisted.locale, response.getJSONObject("state").getString("locale"))
        assertEquals(f.persisted.revision, response.getJSONObject("state").getLong("revision"))
        assertEquals(key, response.getJSONObject("ui_message").getString("key"))
        assertEquals(0, f.writes)
    }
}
