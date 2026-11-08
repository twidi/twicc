package com.twidi.twicc

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import androidx.test.platform.app.InstrumentationRegistry
import com.twidi.twicc.i18n.AndroidLanguageStore
import com.twidi.twicc.i18n.AppLanguage
import com.twidi.twicc.i18n.NativeMessages
import com.twidi.twicc.i18n.UiMessage
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.junit.After
import org.junit.Assert.*
import org.junit.Test

class AppLanguagePersistenceTest {
    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private val preferenceFiles = mutableListOf<String>()

    @After fun removeFixtureFiles() {
        preferenceFiles.forEach { context.deleteSharedPreferences(it) }
    }

    // Each test owns its backing file. The production API still requests only app-language.
    private fun fixture(base: Context = context): Pair<SharedPreferences, Context> {
        val name = "app-language-test-${java.util.UUID.randomUUID()}"
        preferenceFiles += name
        val preferences = base.getSharedPreferences(name, Context.MODE_PRIVATE)
        return preferences to using(preferences, base)
    }

    private fun using(preferences: SharedPreferences, base: Context = context): Context = object : ContextWrapper(base) {
        override fun getSharedPreferences(name: String, mode: Int): SharedPreferences =
            if (name == "app-language") preferences else super.getSharedPreferences(name, mode)
    }

    @Test fun defaultsToEnglishOnFrenchSystem() {
        val configuration = android.content.res.Configuration(context.resources.configuration)
        configuration.setLocales(android.os.LocaleList.forLanguageTags("fr"))
        val frenchContext = context.createConfigurationContext(configuration)
        val (_, isolated) = fixture(frenchContext)
        assertEquals("fr", isolated.resources.configuration.locales[0].language)
        assertEquals(AppLanguage("en", 0), AndroidLanguageStore(isolated).read())
    }

    @Test fun survivesInstanceRemovalAndProcessRecreation() {
        val (preferences, isolated) = fixture()
        val store = AndroidLanguageStore(isolated)
        val instances = InstanceStore(isolated)
        val instance = instances.save(null, "Language fixture", "https://language-${java.util.UUID.randomUUID()}.example")
        try {
            assertEquals(AppLanguage("fr", 1), store.select("fr", 0).state)
            instances.remove(instance)
            // A distinct facade creates a fresh owner, simulating process state loss.
            val reopenedPreferences = object : SharedPreferences by preferences {}
            val reopenedContext = using(reopenedPreferences)
            assertEquals(AppLanguage("fr", 1), AndroidLanguageStore(reopenedContext).read())
            // No activity initialization or locale-provider override precedes this call.
            assertEquals("Annuler", NativeMessages.text(reopenedContext, UiMessage("common.actions.cancel")))
            assertTrue(AndroidLanguageStore(reopenedContext).select("de", 1).accepted)
            assertEquals("Abbrechen", NativeMessages.text(reopenedContext, UiMessage("common.actions.cancel")))
        } finally {
            instances.remove(instance)
        }
    }

    @Test fun observersSeeCommittedChangesAcrossStoresAndCloseStopsDelivery() {
        val (preferences, isolated) = fixture()
        val first = AndroidLanguageStore(isolated)
        val second = AndroidLanguageStore(isolated)
        val changes = mutableListOf<AppLanguage>()
        val subscription = first.observe {
            assertEquals(it.locale, preferences.getString("locale", null))
            assertEquals(it.revision, preferences.getLong("revision", -1))
            changes += it
        }
        assertTrue(second.select("fr", 0).accepted)
        assertEquals(AppLanguage("fr", 1), first.read())
        assertTrue(second.select("fr", 1).accepted)
        assertFalse(second.select("de", 0).accepted)
        assertFalse(second.select("it", 1).accepted)
        assertEquals(listOf(AppLanguage("fr", 1)), changes)
        subscription.close()
        subscription.close()
        assertTrue(second.select("de", 1).accepted)
        assertEquals(1, changes.size)
    }

    @Test fun concurrentStoresAcceptOnlyOneCompareAndSet() {
        val (_, isolated) = fixture()
        val executor = Executors.newFixedThreadPool(2)
        val stores = listOf(AndroidLanguageStore(isolated), AndroidLanguageStore(isolated))
        try {
            val results = executor.invokeAll(stores.zip(listOf("fr", "de")).map { (store, locale) ->
                Callable { store.select(locale, 0) }
            }, 5, TimeUnit.SECONDS).map { it.get(5, TimeUnit.SECONDS) }
            assertEquals(1, results.count { it.accepted })
            assertEquals(UiMessage("android.language.errors.changed"), results.single { !it.accepted }.error)
            assertEquals(1L, AndroidLanguageStore(isolated).read().revision)
        } finally {
            executor.shutdownNow()
        }
    }

    @Test fun failedCommitPreservesAcceptedStateAndDoesNotNotify() {
        val (preferences, _) = fixture()
        var fail = false
        val failingPreferences = object : SharedPreferences by preferences {
            var failedCache: MutableMap<String, Any?>? = null
            override fun getAll(): MutableMap<String, *> = failedCache ?: preferences.all
            override fun edit(): SharedPreferences.Editor {
                val editor = preferences.edit()
                val changes = mutableMapOf<String, Any?>()
                return object : SharedPreferences.Editor by editor {
                    override fun putString(key: String, value: String?): SharedPreferences.Editor {
                        editor.putString(key, value)
                        changes[key] = value
                        return this
                    }
                    override fun putLong(key: String, value: Long): SharedPreferences.Editor {
                        editor.putLong(key, value)
                        changes[key] = value
                        return this
                    }
                    override fun commit(): Boolean {
                        if (!fail) {
                            failedCache = null
                            return editor.commit()
                        }
                        // Android updates its memory cache even when disk persistence fails.
                        failedCache = mutableMapOf<String, Any?>().apply {
                            putAll(preferences.all)
                            putAll(changes)
                        }
                        return false
                    }
                }
            }
        }
        val isolated = using(failingPreferences)
        val store = AndroidLanguageStore(isolated)
        assertTrue(store.select("fr", 0).accepted)
        val changes = mutableListOf<AppLanguage>()
        store.observe { changes += it }.use {
            fail = true
            val rejected = store.select("de", 1)
            assertFalse(rejected.accepted)
            assertEquals(UiMessage("android.language.errors.saveFailed"), rejected.error)
            assertEquals(AppLanguage("fr", 1), rejected.state)
            assertEquals(AppLanguage("fr", 1), AndroidLanguageStore(isolated).read())
            assertEquals("Annuler", NativeMessages.text(isolated, UiMessage("common.actions.cancel")))
            assertTrue(changes.isEmpty())
            assertEquals("de", failingPreferences.getAll()["locale"])
            assertEquals("fr", preferences.getString("locale", null))
            fail = false
            assertEquals(AppLanguage("de", 2), store.select("de", 1).state)
        }
    }

    @Test fun malformedPreferencesResetToEnglish() {
        val (preferences, isolated) = fixture()
        assertTrue(preferences.edit().putString("locale", "fr").putString("revision", "1").commit())
        assertEquals(AppLanguage("en", 0), AndroidLanguageStore(isolated).read())
        assertEquals(AppLanguage("fr", 1), AndroidLanguageStore(isolated).select("fr", 0).state)
    }

    @Test fun reentrantObserversReceiveChangesInRevisionOrder() {
        val (_, isolated) = fixture()
        val store = AndroidLanguageStore(isolated)
        val changes = mutableListOf<Long>()
        store.observe { if (it.revision == 1L) store.select("de", 1) }.use {
            store.observe { changes += it.revision }.use {
                assertTrue(store.select("fr", 0).accepted)
            }
        }
        assertEquals(listOf(1L, 2L), changes)
    }
}
