package com.twidi.twicc

import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import com.twidi.twicc.push.AndroidPushService
import com.twidi.twicc.push.AndroidPush
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.unifiedpush.android.connector.PushService
import org.unifiedpush.android.connector.data.PushMessage
import java.time.Instant
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.atomic.AtomicBoolean

/** Real notification PendingIntent, Activity stack, browser document, and shared app bridge. */
class PushNotificationNavigationTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext

    private inner class PageServer : AutoCloseable {
        val loads = CopyOnWriteArrayList<String>()
        val documents = CopyOnWriteArrayList<String>()
        val routes = CopyOnWriteArrayList<String>()
        val nextRoute = AtomicReference<String?>(null)
        val sessionReady = AtomicBoolean(true)
        val lastSessionReport = AtomicReference<String?>(null)
        val navigationPolling = AtomicBoolean(true)
        val observedNavigationPolling = AtomicBoolean(true)
        val server = MockWebServer()
        init {
            val assets = instrumentation.context.assets
            val certificate = HeldCertificate.decode(assets.open("tts-cert.pem").bufferedReader().readText() +
                assets.open("tts-server-key.fixture").bufferedReader().readText())
            server.useHttps(HandshakeCertificates.Builder().heldCertificate(certificate).build().sslSocketFactory(), false)
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val path = request.requestUrl!!.encodedPath
                    if (path == "/session-ready") return MockResponse().setBody(sessionReady.get().toString())
                    if (path == "/navigation-polling") {
                        val enabled = navigationPolling.get()
                        observedNavigationPolling.set(enabled)
                        return MockResponse().setBody(enabled.toString())
                    }
                    if (path == "/reported") {
                        lastSessionReport.set(request.requestUrl!!.queryParameter("session").orEmpty())
                        return MockResponse().setResponseCode(204)
                    }
                    if (path == "/next-route") return nextRoute.getAndSet(null)?.let { MockResponse().setBody(it) }
                        ?: MockResponse().setResponseCode(204)
                    if (path == "/androidAppClient.js") return MockResponse().setHeader("Content-Type", "text/javascript")
                        .setBody(assets.open("androidAppClient.js").bufferedReader().readText())
                    if (path == "/boot") documents += request.requestUrl!!.queryParameter("id")!!
                    if (path == "/route") routes += request.requestUrl!!.queryParameter("path")!!
                    if (path == "/boot" || path == "/route") return MockResponse().setResponseCode(204)
                    if (!path.startsWith("/project")) return MockResponse().setResponseCode(404)
                    loads += path
                    return MockResponse().setHeader("Content-Type", "text/html").setBody("""<!doctype html><body>Push routing fixture<script type="module">
                        import {createAndroidAppClient} from '/androidAppClient.js';
                        const client = createAndroidAppClient(window, {timeoutMs:5000});
                        async function navigate(state) {
                            if (!state?.navigation) return;
                            history.pushState({}, '', state.navigation.path);
                            await fetch('/route?path=' + encodeURIComponent(state.navigation.path));
                            await client.request('navigation.acknowledge', {navigationId:state.navigation.id});
                        }
                        client.onStateChanged?.(() => {
                            client.request('getState').then(navigate);
                        });
                        const id = crypto.randomUUID();
                        await fetch('/boot?id=' + id);
                        let busy = false;
                        setInterval(async () => {
                            if (busy) return;
                            busy = true;
                            try {
                                const route = await fetch('/next-route');
                                if (route.status === 200) {
                                    const path = await route.text();
                                    history.pushState({}, '', path);
                                    await fetch('/route?path=' + encodeURIComponent(path));
                                }
                                const ready = await (await fetch('/session-ready')).text() === 'true';
                                const sessionId = ready ? (location.pathname.match(/\/session\/([A-Za-z0-9_-]+)/)?.[1] ?? null) : null;
                                const state = await client.request('menuVisibility', {owner:'fixture',visible:true,sessionId});
                                await fetch('/reported?session=' + encodeURIComponent(sessionId ?? ''));
                                if (await (await fetch('/navigation-polling')).text() === 'true') await navigate(state);
                            } catch {} finally { busy = false; }
                        }, 100);
                    </script>""")
                }
            }
            server.start()
        }
        val address get() = server.url("/").toString().removeSuffix("/")
        override fun close() = server.shutdown()
    }

    @Test fun notificationReusesTheDocumentRoutesSessionsAndLoadsTheCorrectInstanceWhenClosed() {
        prepareBrowserTestProfile()
        instrumentation.uiAutomation.executeShellCommand("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS").close()
        val preferences = context.getSharedPreferences("instances", Context.MODE_PRIVATE)
        val backup = preferences.all.toMap()
        val pushPreferences = context.getSharedPreferences("android-push", Context.MODE_PRIVATE)
        val pushBackup = pushPreferences.all.toMap()
        val receiver = CompletableFuture<PushService>()
        val connection = object : ServiceConnection {
            override fun onServiceConnected(name: ComponentName, binder: IBinder) { receiver.complete((binder as PushService.PushBinder).getService()) }
            override fun onServiceDisconnected(name: ComponentName) {}
        }
        context.bindService(Intent(context, AndroidPushService::class.java), connection, Context.BIND_AUTO_CREATE)
        val service = receiver.get(10, TimeUnit.SECONDS)
        try {
            PageServer().use { first -> PageServer().use { second ->
                val store = InstanceStore(context)
                val one = store.save(null, "Push one", first.address)
                val two = store.save(null, "Push two", second.address)
                listOf(one, two).forEach { instance -> AndroidPush.update(context, instance.id) {
                    it.put("preferences", JSONObject().put("user_turn", true).put("pending_request", true).put("extra_usage", true).put("peer", true))
                } }
                val a = post(service, one.id, "/project/p/session/one")
                val b = post(service, one.id, "/project/p/session/two")
                assertNotNull("Session A must survive a notification of the same category from B", notification(a))
                assertNotNull(notification(b))
                first.sessionReady.set(false)
                store.rememberPage(one, "${one.address}/project/p/session/one")
                instrumentation.startActivitySync(Intent(context, BrowserActivity::class.java)
                    .putExtra("instance", one.id).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                waitFor { first.documents.size == 1 }
                val original = resumedBrowser()
                waitFor { first.lastSessionReport.get() == "" }
                assertNotNull("Loaded HTML without loaded session content must retain A", notification(a))
                val loading = post(service, one.id, "/project/p/session/one")
                assertNotNull(notification(loading))
                first.sessionReady.set(true)
                waitFor { notification(loading) == null }
                waitFor { notification(a) == null }
                assertNotNull("Opening A must retain B", notification(b))
                val foreground = post(service, one.id, "/project/p/session/one", expectNotification = false)
                assertNull("The displayed session must never post a new notification", notification(foreground))
                val foregroundQuestion = post(service, one.id, "/project/p/session/one", "pending_request", expectNotification = false)
                val otherInstance = post(service, two.id, "/project/p/session/one")
                assertNotNull("The same session ID in another instance must remain visible", notification(otherInstance))
                assertNull(notification(foreground)); assertNull(notification(foregroundQuestion))

                first.sessionReady.set(false)
                waitFor { first.lastSessionReport.get() == "" }
                val failedContent = post(service, one.id, "/project/p/session/one")
                assertNotNull("A SPA loading error must not suppress its session", notification(failedContent))
                first.sessionReady.set(true)
                waitFor { notification(failedContent) == null }

                tap(service, one.id, null)
                waitFor { resumedBrowser() === original }
                Thread.sleep(500)
                assertEquals("Instance-only tap must keep the loaded document", 1, first.loads.size)

                // A foreground tap must reach the page without any navigation heartbeat.
                first.navigationPolling.set(false)
                waitFor { !first.observedNavigationPolling.get() }
                val immediate = post(service, one.id, "/project/p/session/two")
                val started = SystemClock.elapsedRealtime()
                notification(immediate)!!.notification.contentIntent.send()
                waitFor { first.routes.contains("/project/p/session/two") }
                android.util.Log.i("PushNavigationTest", "Foreground tap routed in ${SystemClock.elapsedRealtime() - started} ms")
                first.navigationPolling.set(true)
                assertSame(original, resumedBrowser())
                assertEquals("Session routing must not reload HTML", 1, first.loads.size)
                waitFor { store.page(one)?.endsWith("/project/p/session/two") == true }

                waitFor { notification(b) == null }
                val backToA = post(service, one.id, "/project/p/session/one")
                assertNotNull(notification(backToA))
                first.nextRoute.set("/projects/p/session/one/files/root/example.txt")
                waitFor { store.page(one)?.endsWith("/projects/p/session/one/files/root/example.txt") == true }
                waitFor { notification(backToA) == null }
                assertEquals("SPA navigation must not reload HTML", 1, first.documents.size)

                first.nextRoute.set("/login")
                waitFor { store.page(one)?.endsWith("/login") == true }
                val login = post(service, one.id, "/project/p/session/one")
                assertNotNull("A login screen must not suppress the previous session", notification(login))
                first.nextRoute.set("/project/p/session/two")
                waitFor { store.page(one)?.endsWith("/project/p/session/two") == true }

                instrumentation.runOnMainSync { original!!.moveTaskToBack(true) }
                waitFor { resumedBrowser() == null }
                val backgroundQuestion = post(service, one.id, "/project/p/session/two", "pending_request")
                assertNotNull("A background session must still notify", notification(backgroundQuestion))

                tap(service, one.id, "/project/p/session/two")
                Thread.sleep(500)
                assertSame(original, resumedBrowser())
                assertEquals(1, first.documents.size)
                waitFor { notification(backgroundQuestion) == null }

                instrumentation.uiAutomation.executeShellCommand("input keyevent 223").close()
                waitFor { !context.getSystemService(PowerManager::class.java).isInteractive }
                val asleep = post(service, one.id, "/project/p/session/two")
                assertNotNull("A screen-off session must notify", notification(asleep))
                instrumentation.uiAutomation.executeShellCommand("input keyevent 224").close()
                instrumentation.uiAutomation.executeShellCommand("wm dismiss-keyguard").close()
                waitFor { resumedBrowser() === original }
                waitFor { notification(asleep) == null }

                tap(service, two.id, "/project/p/session/three")
                waitFor { second.documents.size == 1 }
                assertNotSame(original, resumedBrowser())
                assertEquals(listOf("/project/p/session/three"), second.loads.toList())
                assertEquals(two.id, store.last()!!.id)

                instrumentation.runOnMainSync { resumedBrowser()!!.finish() }
                instrumentation.startActivitySync(Intent(context, MainActivity::class.java)
                    .putExtra("manage", true).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                tap(service, one.id, "/project/p/session/four")
                waitFor { first.documents.size == 2 }
                assertEquals("/project/p/session/four", first.loads.last())
                assertEquals(one.id, store.last()!!.id)
                val removeA = post(service, one.id, "/project/p/session/a")
                val removeB = post(service, one.id, "/project/p/session/b")
                assertNotNull(notification(removeA)); assertNotNull(notification(removeB))
                AndroidPush.remove(context, one.id)
                assertNull(notification(removeA)); assertNull(notification(removeB))
                assertNotNull("Removing an instance must retain other instances' notifications", notification(otherInstance))
            } }
        } finally {
            instrumentation.uiAutomation.executeShellCommand("input keyevent 224").close()
            instrumentation.uiAutomation.executeShellCommand("wm dismiss-keyguard").close()
            instrumentation.runOnMainSync {
                // ActivityScenario tracks the activity class across replacements. This fixture replaces
                // BrowserActivity itself, so finish the actual live activities instead.
                listOf(Stage.RESUMED, Stage.PAUSED, Stage.STARTED, Stage.STOPPED, Stage.CREATED)
                    .flatMap { ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(it) }
                    .filter { it is BrowserActivity || it is MainActivity }.distinct().forEach { it.finish() }
            }
            instrumentation.waitForIdleSync()
            context.unbindService(connection)
            context.getSystemService(NotificationManager::class.java).activeNotifications
                .filter { it.tag?.startsWith("twicc-push:") == true }
                .forEach { context.getSystemService(NotificationManager::class.java).cancel(it.tag, it.id) }
            val edit = preferences.edit().clear()
            backup.forEach { (key, value) -> when (value) {
                is String -> edit.putString(key, value)
                is Set<*> -> edit.putStringSet(key, value.filterIsInstance<String>().toSet())
            } }
            edit.commit()
            val restorePush = pushPreferences.edit().clear()
            pushBackup.forEach { (key, value) -> if (value is String) restorePush.putString(key, value) }
            restorePush.commit()
        }
    }

    private fun post(service: PushService, instance: String, path: String?, kind: String = "user_turn", expectNotification: Boolean = true): String {
        val id = UUID.randomUUID().toString()
        val payload = JSONObject().put("type", "twicc.notification").put("id", id).put("sent_at", Instant.now().toString()).put("kind", kind).put("title", "Finished").put("body", id)
        if (path != null) payload.put("path", path)
        // The transport was tested on the phone. This test starts after successful decryption.
        instrumentation.runOnMainSync { service.onMessage(PushMessage(payload.toString().toByteArray(), true), instance) }
        instrumentation.waitForIdleSync()
        if (expectNotification) waitFor { notification(id) != null }
        return id
    }

    private fun notification(id: String) = context.getSystemService(NotificationManager::class.java).activeNotifications
        .singleOrNull { it.notification.extras.getString("android.text") == id }

    private fun tap(service: PushService, instance: String, path: String?) {
        val id = post(service, instance, path)
        waitFor { notification(id) != null }
        notification(id)!!.notification.contentIntent.send()
    }

    private fun resumedBrowser(): BrowserActivity? {
        var activity: BrowserActivity? = null
        val read = { activity = ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED).filterIsInstance<BrowserActivity>().singleOrNull() }
        if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) read() else instrumentation.runOnMainSync(read)
        return activity
    }

    private fun waitFor(check: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 20_000
        while (!check()) {
            if (SystemClock.elapsedRealtime() > deadline) fail("Timed out waiting for notification navigation")
            Thread.sleep(50)
        }
    }
}
