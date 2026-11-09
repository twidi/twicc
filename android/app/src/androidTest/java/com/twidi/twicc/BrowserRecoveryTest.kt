package com.twidi.twicc

import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.view.View
import android.widget.TextView
import android.widget.PopupMenu
import com.twidi.twicc.i18n.AndroidLanguageStore
import com.twidi.twicc.i18n.NativeMessages
import com.twidi.twicc.i18n.UiMessage
import org.json.JSONObject
import androidx.lifecycle.Lifecycle
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import com.twidi.twicc.browser.BrowserEvents
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

/** Real Gecko documents exercise the HTML guard and the Activity's engine failure boundary. */
class BrowserRecoveryTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext

    private inner class Site(val failure: String = "none") : AutoCloseable {
        val requests = CopyOnWriteArrayList<String>()
        val ready = CopyOnWriteArrayList<String>()
        val fallback = CopyOnWriteArrayList<String>()
        val featureFallback = CopyOnWriteArrayList<String>()
        val server = MockWebServer()
        init {
            val assets = instrumentation.context.assets
            val certificate = HeldCertificate.decode(assets.open("tts-cert.pem").bufferedReader().readText() +
                assets.open("tts-server-key.fixture").bufferedReader().readText())
            server.useHttps(HandshakeCertificates.Builder().heldCertificate(certificate).build().sslSocketFactory(), false)
            val guard = assets.open("index.html").bufferedReader().readText()
                .substringAfter("<script data-twicc-recovery>").substringBefore("</script>")
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val url = request.requestUrl!!
                    // WebView also requests this icon. It is not a document reload.
                    if (url.encodedPath == "/favicon.ico") return MockResponse().setResponseCode(404)
                    if (url.encodedPath == "/ready") { ready += url.queryParameter("state") ?: ""; return MockResponse() }
                    if (url.encodedPath == "/fallback") { fallback += "shown"; return MockResponse() }
                    if (url.encodedPath == "/feature-fallback") { featureFallback += "handled"; return MockResponse() }
                    if (url.encodedPath == "/static/assets/runtime-error.js") return MockResponse()
                        .setHeader("Content-Type", "text/javascript").setBody("throw new TypeError('Editor initialization failed');")
                    if (url.encodedPath.startsWith("/static/")) return MockResponse().setResponseCode(404)
                    requests += request.path!!
                    val broken = failure == "always" || (failure != "none" && url.queryParameter("__twicc_reload") == null)
                    val asset = when {
                        failure == "bare" || failure == "runtime" -> {
                            val module = if (failure == "bare") "@codemirror/legacy-modes/mode/toml" else "/static/assets/runtime-error.js"
                            """<script type=module>
                                window.dispatchEvent(new Event('twicc:assets-ready'));
                                import('$module').catch(error=>{
                                    // Vite forwards both import resolution and module evaluation errors.
                                    const event=new Event('vite:preloadError',{cancelable:true});
                                    event.payload=error;window.dispatchEvent(event);
                                    if(!event.defaultPrevented) fetch('/feature-fallback');
                                });
                            </script>"""
                        }
                        !broken -> "<script type=module>window.dispatchEvent(new Event('twicc:assets-ready'));fetch('/ready?state='+sessionStorage.getItem('login-fixture'));</script>"
                        failure == "css" -> "<link rel=stylesheet href=/static/assets/old.css>"
                        else -> "<script type=module src=/static/assets/old.js></script>"
                    }
                    return MockResponse().setHeader("Content-Type", "text/html").setBody("""<!doctype html><head>
                        <script>sessionStorage.setItem('login-fixture','preserved');$guard</script>$asset
                        </head><body><div id=app>Recovery fixture</div><script>
                        addEventListener('DOMContentLoaded',()=>{
                            if(document.querySelector('section[role=alert] button')) fetch('/fallback');
                        });
                        </script></body>""")
                }
            }
            server.start()
        }
        override fun close() = server.shutdown()
    }

    private fun withBrowser(site: Site, test: (ActivityScenario<BrowserActivity>) -> Unit) {
        prepareBrowserTestProfile()
        val preferences = context.getSharedPreferences("instances", Context.MODE_PRIVATE)
        val backup = preferences.all.toMap()
        try {
            val store = InstanceStore(context)
            val instance = store.save(null, "Recovery fixture", site.server.url("/").toString())
            store.rememberPage(instance, "${instance.address}/project/p/session/s?workspace=w#item")
            ActivityScenario.launch<BrowserActivity>(Intent(context, BrowserActivity::class.java)
                .putExtra("instance", instance.id)).use(test)
        } finally {
            val edit = preferences.edit().clear()
            backup.forEach { (key, value) -> when (value) {
                is String -> edit.putString(key, value)
                is Set<*> -> edit.putStringSet(key, value.filterIsInstance<String>().toSet())
            } }
            edit.commit()
        }
    }

    @Test fun missingJsAndCssRecoverWithoutLosingTheSessionOrLogin() {
        for (failure in listOf("js", "css")) Site(failure).use { site -> withBrowser(site) {
            waitFor { site.ready.isNotEmpty() }
            assertEquals(listOf("preserved"), site.ready.toList())
            assertEquals(2, site.requests.size)
            assertTrue(site.requests[1].startsWith("/project/p/session/s?workspace=w&__twicc_reload="))
        } }
    }

    @Test fun repeatedMissingAssetsShowRetryInsteadOfReloadingForever() {
        Site("always").use { site -> withBrowser(site) {
            waitFor { site.fallback.isNotEmpty() }
            Thread.sleep(500)
            assertEquals(2, site.requests.size)
            assertEquals(1, site.fallback.size)
        } }
    }

    @Test fun optionalModuleErrorsStayWithTheFeatureInsteadOfReplacingThePage() {
        for (failure in listOf("bare", "runtime")) Site(failure).use { site -> withBrowser(site) {
            waitFor { site.featureFallback.isNotEmpty() }
            assertEquals(1, site.requests.size)
            assertTrue(site.fallback.isEmpty())
        } }
    }

    @Test fun rendererRecoveryWaitsForForegroundAndStopsAfterASecondFailure() {
        Site().use { site -> withBrowser(site) { scenario ->
            waitFor { site.ready.size == 1 }
            lateinit var activity: BrowserActivity
            lateinit var events: BrowserEvents
            scenario.onActivity {
                activity = it
                events = BrowserActivity::class.java.getDeclaredMethod("events").apply { isAccessible = true }
                    .invoke(it) as BrowserEvents
            }
            scenario.moveToState(Lifecycle.State.CREATED)
            instrumentation.runOnMainSync { events.onRendererStopped(crashed = false) }
            Thread.sleep(300)
            assertEquals(1, site.ready.size)
            scenario.moveToState(Lifecycle.State.RESUMED)
            waitFor { site.ready.size == 2 }
            instrumentation.runOnMainSync { events.onRendererStopped(crashed = true) }
            Thread.sleep(500)
            assertEquals(2, site.ready.size)
            instrumentation.runOnMainSync {
                val field = BrowserActivity::class.java.getDeclaredField("errorPanel").apply { isAccessible = true }
                assertEquals(View.VISIBLE, (field.get(activity) as View).visibility)
            }
        } }
    }

    @Test fun idleSpeechCategoryFollowsBrowserLanguageChangesWithoutAFrontendHandshake() {
        val language = AndroidLanguageStore(context)
        val original = language.read().locale
        val manager = context.getSystemService(android.app.NotificationManager::class.java)
        try {
            assertTrue(language.select("fr", language.read().revision).accepted)
            com.twidi.twicc.tts.TtsNotification(context)
            val before = manager.getNotificationChannel("tts_playback")
            val active = manager.activeNotifications.map { it.key }.toSet()
            assertTrue(language.select("en", language.read().revision).accepted)
            Site().use { site -> withBrowser(site) { scenario ->
                waitFor { site.ready.size == 1 }
                assertEquals("Timeline speech", manager.getNotificationChannel("tts_playback").name.toString())
                for ((locale, name) in listOf("fr" to "Lecture de la chronologie", "en" to "Timeline speech")) {
                    assertTrue(language.select(locale, language.read().revision).accepted)
                    instrumentation.waitForIdleSync()
                    assertEquals(name, manager.getNotificationChannel("tts_playback").name.toString())
                    val after = manager.getNotificationChannel("tts_playback")
                    assertEquals(before.sound, after.sound)
                    assertEquals(before.importance, after.importance)
                    assertEquals(active, manager.activeNotifications.map { it.key }.toSet())
                }
                assertEquals(1, site.requests.size)
            } }
        } finally { language.select(original, language.read().revision) }
    }

    @Test fun engineFailuresUseDescriptors() {
        Site().use { site -> withBrowser(site) { scenario ->
            waitFor { site.ready.size == 1 }
            val language = AndroidLanguageStore(context)
            val original = language.read().locale
            val message = UiMessage("android.browser.errors.httpStatus", mapOf("status" to 503))
            var originalPanel: android.view.ViewGroup? = null
            scenario.onActivity { activity ->
                fieldEvents(activity).onFailure(message)
                originalPanel = field(activity, "errorPanel") as android.view.ViewGroup
            }
            try {
                for (locale in listOf("en", "de", "es", "fr", "pt-BR")) {
                    assertTrue(language.select(locale, language.read().revision).accepted)
                    instrumentation.waitForIdleSync()
                    scenario.onActivity { activity ->
                        val panel = field(activity, "errorPanel") as android.view.ViewGroup
                        assertSame(originalPanel, panel)
                        assertEquals(NativeMessages.text(activity, message), (panel.getChildAt(0) as TextView).text.toString())
                    }
                }
                assertEquals(1, site.requests.size)
            } finally { language.select(original, language.read().revision) }
        } }
    }

    @Test fun nativeMenuUsesSharedWebKeys() {
        Site().use { site -> withBrowser(site) { scenario ->
            waitFor { site.ready.size == 1 }
            val language = AndroidLanguageStore(context)
            val original = language.read().locale
            var popup: PopupMenu? = null
            var browser: Any? = null
            scenario.onActivity { activity ->
                browser = field(activity, "browser")
                BrowserActivity::class.java.getDeclaredMethod("showMenu").apply { isAccessible = true }.invoke(activity)
                popup = field(activity, "nativeMenu") as PopupMenu
            }
            try {
                for (locale in listOf("en", "de", "es", "fr", "pt-BR")) {
                    language.select(locale, language.read().revision)
                    instrumentation.waitForIdleSync()
                    scenario.onActivity { activity ->
                        assertSame(popup, field(activity, "nativeMenu"))
                        assertSame(browser, field(activity, "browser"))
                        for ((id, key) in mapOf(R.id.browser_menu_manage to "navigation.android.manage",
                            R.id.browser_menu_reload to "navigation.android.reload", R.id.browser_menu_force_refresh to "navigation.android.forceRefresh",
                            R.id.browser_menu_home to "navigation.android.serverHome", R.id.browser_menu_external to "navigation.android.browser")) {
                            assertEquals(NativeMessages.text(activity, UiMessage(key)), popup!!.menu.findItem(id).title.toString())
                        }
                    }
                }
                assertEquals(1, site.requests.size)
            } finally {
                instrumentation.runOnMainSync { popup?.dismiss() }
                language.select(original, language.read().revision)
            }
        } }
    }

    @Test fun refreshConfirmationKeepsItsPendingActionWhileLanguageChanges() {
        Site().use { site -> withBrowser(site) { scenario ->
            waitFor { site.ready.size == 1 }
            val language = AndroidLanguageStore(context)
            val original = language.read().locale
            var dialog: android.app.AlertDialog? = null
            scenario.onActivity { activity ->
                BrowserActivity::class.java.getDeclaredMethod("showRefreshConfirmation").apply { isAccessible = true }.invoke(activity)
                dialog = field(activity, "refreshConfirmation") as android.app.AlertDialog
            }
            try {
                for (locale in listOf("en", "de", "es", "fr", "pt-BR")) {
                    language.select(locale, language.read().revision)
                    instrumentation.waitForIdleSync()
                    scenario.onActivity { activity ->
                        assertSame(dialog, field(activity, "refreshConfirmation"))
                        assertTrue(dialog!!.isShowing)
                        assertEquals(NativeMessages.text(activity, UiMessage("navigation.android.refresh")),
                            dialog!!.getButton(android.app.AlertDialog.BUTTON_POSITIVE).text.toString())
                    }
                }
                assertEquals(1, site.requests.size)
                instrumentation.runOnMainSync { dialog!!.getButton(android.app.AlertDialog.BUTTON_NEGATIVE).performClick() }
                assertEquals(1, site.requests.size)
            } finally {
                instrumentation.runOnMainSync { dialog?.dismiss() }
                language.select(original, language.read().revision)
            }
        } }
    }

    @Test fun speechSettingsErrorRefreshesThroughActivityLanguageObserverAndResume() {
        Site().use { site -> withBrowser(site) { scenario ->
            waitFor { site.ready.size == 1 }
            val language = AndroidLanguageStore(context)
            val original = language.read().locale
            val message = UiMessage("android.speech.notificationsRequired")
            val error = com.twidi.twicc.tts.TtsActionError("notifications_blocked", "External fallback", "notification_settings",
                com.twidi.twicc.tts.ttsUiMessage(message.key))
            var dialog: android.app.AlertDialog? = null
            scenario.onActivity { activity ->
                BrowserActivity::class.java.getDeclaredMethod("showSpeechSettings", com.twidi.twicc.tts.TtsActionError::class.java)
                    .apply { isAccessible = true }.invoke(activity, error)
                dialog = field(activity, "speechSettingsDialog") as android.app.AlertDialog
            }
            try {
                for (locale in listOf("en", "de", "es", "fr", "pt-BR")) {
                    assertTrue(language.select(locale, language.read().revision).accepted)
                    instrumentation.waitForIdleSync()
                    scenario.onActivity { activity ->
                        assertSame(dialog, field(activity, "speechSettingsDialog"))
                        assertSame(error, field(activity, "speechSettingsError"))
                        assertTrue(dialog!!.isShowing)
                        fun labels(view: View): List<String> = when (view) {
                            is TextView -> listOf(view.text.toString())
                            is android.view.ViewGroup -> (0 until view.childCount).flatMap { labels(view.getChildAt(it)) }
                            else -> emptyList()
                        }
                        assertTrue(labels(dialog!!.window!!.decorView).contains(
                            NativeMessages.text(activity, UiMessage("android.speech.settingsTitle"))))
                        assertEquals(NativeMessages.text(activity, message),
                            dialog!!.findViewById<TextView>(android.R.id.message).text.toString())
                        assertEquals(NativeMessages.text(activity, UiMessage("common.actions.cancel")),
                            dialog!!.getButton(android.app.AlertDialog.BUTTON_NEGATIVE).text.toString())
                        assertEquals(NativeMessages.text(activity, UiMessage("android.speech.openSettings")),
                            dialog!!.getButton(android.app.AlertDialog.BUTTON_POSITIVE).text.toString())
                    }
                }
                scenario.moveToState(Lifecycle.State.CREATED)
                assertTrue(language.select("de", language.read().revision).accepted)
                scenario.moveToState(Lifecycle.State.RESUMED)
                scenario.onActivity { activity ->
                    assertSame(dialog, field(activity, "speechSettingsDialog"))
                    assertEquals(NativeMessages.text(activity, message),
                        dialog!!.findViewById<TextView>(android.R.id.message).text.toString())
                }
                val monitor = instrumentation.addMonitor(android.content.IntentFilter(android.provider.Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS),
                    android.app.Instrumentation.ActivityResult(android.app.Activity.RESULT_OK, null), true)
                try {
                    instrumentation.runOnMainSync { dialog!!.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick() }
                    instrumentation.waitForIdleSync()
                    assertEquals(1, monitor.hits)
                    scenario.onActivity { activity ->
                        assertNull(field(activity, "speechSettingsDialog"))
                        assertNull(field(activity, "speechSettingsError"))
                    }
                } finally { instrumentation.removeMonitor(monitor) }
                assertEquals("notifications_blocked", error.code)
                assertEquals("notification_settings", error.action)
                assertEquals(1, site.requests.size)
            } finally {
                instrumentation.runOnMainSync { dialog?.dismiss() }
                language.select(original, language.read().revision)
            }
        } }
    }

    @Test fun oldFrontendKeepsFallbackMenuUntilTheCurrentDocumentReadsLanguage() {
        Site().use { site -> withBrowser(site) { scenario ->
            waitFor { site.ready.size == 1 }
            scenario.onActivity { activity ->
                val events = fieldEvents(activity)
                val browser = field(activity, "browser")
                fun request(command: String) = JSONObject(events.onAppMessage(
                    JSONObject().put("protocol", 1).put("id", "fixture").put("command", command)
                        .put("owner", "fixture").put("visible", true).toString()))
                assertTrue(request("menuVisibility").getBoolean("ok"))
                request("getState") // A state read does not prove that the document synchronizes language.
                val button = field(activity, "menuButton") as View
                assertEquals(View.VISIBLE, button.visibility)
                BrowserActivity::class.java.getDeclaredMethod("showMenu").apply { isAccessible = true }.invoke(activity)
                val popup = field(activity, "nativeMenu") as PopupMenu
                assertEquals(NativeMessages.text(activity, UiMessage("android.language.errors.updateServer")),
                    popup.menu.findItem(R.id.browser_menu_language_limitation).title.toString())
                popup.dismiss()
                val invalid = JSONObject(events.onAppMessage("""{"protocol":1,"id":"","command":"locale.get"}"""))
                assertFalse(invalid.getBoolean("ok"))
                assertEquals(View.VISIBLE, button.visibility)
                assertTrue(request("locale.get").getBoolean("ok"))
                assertEquals(View.GONE, button.visibility)
                assertSame(browser, field(activity, "browser"))
                events.onPageStarted(site.server.url("/next").toString())
                request("menuVisibility")
                assertEquals(View.VISIBLE, button.visibility)
                assertSame(browser, field(activity, "browser"))
            }
            assertEquals(1, site.requests.size)
        } }
    }

    private fun field(activity: BrowserActivity, name: String): Any? = BrowserActivity::class.java
        .getDeclaredField(name).apply { isAccessible = true }.get(activity)
    private fun fieldEvents(activity: BrowserActivity): BrowserEvents = BrowserActivity::class.java
        .getDeclaredMethod("events").apply { isAccessible = true }.invoke(activity) as BrowserEvents

    private fun waitFor(check: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 20_000
        while (!check()) {
            if (SystemClock.elapsedRealtime() > deadline) fail("Timed out waiting for browser recovery")
            Thread.sleep(50)
        }
    }
}
