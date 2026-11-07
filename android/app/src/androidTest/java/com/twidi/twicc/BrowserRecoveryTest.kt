package com.twidi.twicc

import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.view.View
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

    private fun waitFor(check: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 20_000
        while (!check()) {
            if (SystemClock.elapsedRealtime() > deadline) fail("Timed out waiting for browser recovery")
            Thread.sleep(50)
        }
    }
}
