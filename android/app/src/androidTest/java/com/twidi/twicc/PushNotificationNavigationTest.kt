package com.twidi.twicc

import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
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

/** Real notification PendingIntent, Activity stack, browser document, and shared app bridge. */
class PushNotificationNavigationTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext

    private inner class PageServer : AutoCloseable {
        val loads = CopyOnWriteArrayList<String>()
        val documents = CopyOnWriteArrayList<String>()
        val routes = CopyOnWriteArrayList<String>()
        val server = MockWebServer()
        init {
            val assets = instrumentation.context.assets
            val certificate = HeldCertificate.decode(assets.open("tts-cert.pem").bufferedReader().readText() +
                assets.open("tts-server-key.fixture").bufferedReader().readText())
            server.useHttps(HandshakeCertificates.Builder().heldCertificate(certificate).build().sslSocketFactory(), false)
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val path = request.requestUrl!!.encodedPath
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
                        const id = crypto.randomUUID();
                        await fetch('/boot?id=' + id);
                        let busy = false;
                        setInterval(async () => {
                            if (busy) return;
                            busy = true;
                            try {
                                const state = await client.request('menuVisibility', {owner:'fixture',visible:true});
                                if (state?.navigation) {
                                    history.pushState({}, '', state.navigation.path);
                                    await fetch('/route?path=' + encodeURIComponent(state.navigation.path));
                                    await client.request('navigation.acknowledge', {navigationId:state.navigation.id});
                                }
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
                store.rememberPage(one, "${one.address}/project/p/session/one")
                instrumentation.startActivitySync(Intent(context, BrowserActivity::class.java)
                    .putExtra("instance", one.id).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                waitFor { first.documents.size == 1 }
                val original = resumedBrowser()

                tap(service, one.id, null)
                waitFor { resumedBrowser() === original }
                Thread.sleep(500)
                assertEquals("Instance-only tap must keep the loaded document", 1, first.loads.size)

                tap(service, one.id, "/project/p/session/two")
                waitFor { first.routes.contains("/project/p/session/two") }
                assertSame(original, resumedBrowser())
                assertEquals("Session routing must not reload HTML", 1, first.loads.size)
                waitFor { store.page(one)?.endsWith("/project/p/session/two") == true }

                tap(service, one.id, "/project/p/session/two")
                Thread.sleep(500)
                assertSame(original, resumedBrowser())
                assertEquals(1, first.documents.size)

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
            } }
        } finally {
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

    private fun tap(service: PushService, instance: String, path: String?) {
        val id = UUID.randomUUID().toString()
        val payload = JSONObject().put("type", "twicc.notification").put("id", id).put("sent_at", Instant.now().toString()).put("kind", "user_turn").put("title", "Finished").put("body", "Session example")
        if (path != null) payload.put("path", path)
        // The transport was tested on the phone. This test starts after successful decryption.
        val notifications = context.getSystemService(NotificationManager::class.java)
        val tag = "twicc-push:$instance:user_turn"
        notifications.cancel(tag, 1)
        instrumentation.runOnMainSync { service.onMessage(PushMessage(payload.toString().toByteArray(), true), instance) }
        waitFor { notifications.activeNotifications.any { it.tag == tag } }
        notifications.activeNotifications.single { it.tag == tag }.notification.contentIntent.send()
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
