package com.twidi.twicc

import android.app.job.JobScheduler
import androidx.test.core.app.ActivityScenario
import androidx.lifecycle.Lifecycle
import com.twidi.twicc.push.PushRegistrationRequest
import android.content.Context
import android.content.Intent
import android.content.ComponentName
import android.content.ServiceConnection
import android.os.IBinder
import androidx.test.platform.app.InstrumentationRegistry
import com.twidi.twicc.push.AndroidPush
import com.twidi.twicc.push.AndroidPushService
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.unifiedpush.android.connector.FailedReason
import org.unifiedpush.android.connector.PushService
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

class AndroidPushRegistrationTest {
    @Test fun apkFirstServerLaterReconfirmsOnNativeResume() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = InstanceStore(context)
        val instances = (1..2).map { store.save(null, "Upgrade fixture $it", "https://upgrade-$it-${java.util.UUID.randomUUID()}.example") }
        val terminal = listOf("login", "deleted").map { status ->
            store.save(null, "Terminal $status", "https://$status-${java.util.UUID.randomUUID()}.example").also { saved ->
                AndroidPush.update(context, saved.id) {
                    it.put("token", "grant").put("status", status).put("deleted", status == "deleted").put("dirty", false)
                }
            }
        }
        val terminalRevisions = terminal.associate { it.id to AndroidPush.read(context, it.id).getString("revision") }
        val scheduler = context.getSystemService(JobScheduler::class.java)
        try {
            instances.forEach { instance ->
                AndroidPush.update(context, instance.id) {
                    it.put("token", "grant").put("address", instance.address).put("status", "ready")
                        .put("enabled", AndroidPush.allowed(context)).put("subscription", JSONObject().put("endpoint", "old"))
                        .put("preferences", JSONObject().put("user_turn", true).put("pending_request", false).put("extra_usage", false).put("peer", false))
                }
            }
            ActivityScenario.launch<MainActivity>(Intent(context, MainActivity::class.java).putExtra("manage", true)).use { scenario ->
                scheduler.cancel(820) // Inspect work without a live network or instance page.
                instances.forEach { instance ->
                    val snapshot = AndroidPush.pending(context).getValue(instance.id)
                    val request = PushRegistrationRequest.build(instance.id, snapshot)
                    val buffer = okio.Buffer()
                    request.body!!.writeTo(buffer)
                    assertEquals(setOf("device_id", "subscription", "enabled", "preferences", "notification_payload_version"),
                        JSONObject(buffer.readUtf8()).keys().asSequence().toSet())
                    AndroidPush.complete(context, instance.id, snapshot, "ready", false,
                        PushRegistrationRequest.confirmedPayloadVersion("""{"ok":true}"""))
                    assertFalse(AndroidPush.read(context, instance.id).optBoolean("dirty"))
                    assertEquals("update_server", AndroidPush.state(context, instance).getString("status"))
                }
                scenario.onActivity { AndroidPush.reconcilePayloadSupport(it) }
                assertTrue("Old-server success must not continuously poll", AndroidPush.pending(context).keys.intersect(instances.map { it.id }.toSet()).isEmpty())
                // Activity recreation and activity switches belong to the same foreground session.
                scenario.recreate()
                scheduler.cancel(820)
                assertTrue(AndroidPush.pending(context).keys.intersect(instances.map { it.id }.toSet()).isEmpty())
                ActivityScenario.launch<MainActivity>(Intent(context, MainActivity::class.java).putExtra("manage", true)).use {
                    scheduler.cancel(820)
                    assertTrue(AndroidPush.pending(context).keys.intersect(instances.map { it.id }.toSet()).isEmpty())
                }
                assertTrue(AndroidPush.pending(context).keys.intersect(instances.map { it.id }.toSet()).isEmpty())
                scenario.moveToState(Lifecycle.State.CREATED)
                scenario.moveToState(Lifecycle.State.RESUMED)
                scheduler.cancel(820)
                instances.forEach { instance ->
                    val oldEndpoint = AndroidPush.pending(context).getValue(instance.id)
                    AndroidPush.update(context, instance.id) { it.put("subscription", JSONObject().put("endpoint", "rotated")) }
                    AndroidPush.complete(context, instance.id, oldEndpoint, "ready", false, 2)
                    assertTrue("Stale success cannot clear endpoint rotation", AndroidPush.read(context, instance.id).getBoolean("dirty"))
                    assertFalse(AndroidPush.read(context, instance.id).has("notification_payload_version"))
                    val snapshot = AndroidPush.pending(context).getValue(instance.id)
                    assertEquals("rotated", snapshot.getJSONObject("subscription").getString("endpoint"))
                    AndroidPush.complete(context, instance.id, snapshot, "ready", false,
                        PushRegistrationRequest.confirmedPayloadVersion("""{"ok":true,"notification_payload_version":2}"""))
                    assertEquals(2, AndroidPush.read(context, instance.id).getInt("notification_payload_version"))
                    assertEquals("ready", AndroidPush.state(context, instance).getString("status"))
                    val confirmed = AndroidPush.read(context, instance.id)
                    AndroidPush.complete(context, instance.id, confirmed, "ready", false)
                    assertEquals(2, AndroidPush.read(context, instance.id).getInt("notification_payload_version"))
                    assertFalse(AndroidPush.read(context, instance.id).has("locale"))
                    assertFalse(AndroidPush.read(context, instance.id).has("language"))
                }
                terminal.forEach { saved ->
                    assertEquals("Reconciliation preserves 401 and deletion state", terminalRevisions[saved.id],
                        AndroidPush.read(context, saved.id).getString("revision"))
                }
            }
        } finally {
            scheduler.cancel(820)
            (instances + terminal).forEach { instance ->
                context.getSharedPreferences("android-push", Context.MODE_PRIVATE).edit().remove(instance.id).commit()
                store.remove(instance)
            }
            context.getSharedPreferences("instances", Context.MODE_PRIVATE).edit().commit()
        }
    }

    @Test fun failedEndpointRegistrationSchedulesNativeRetryAndStaleUploadCannotClearNewChanges() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val store = InstanceStore(context)
        val saved = store.save(null, "Push retry fixture", "https://push-retry-${java.util.UUID.randomUUID()}.example")
        val receiver = CompletableFuture<PushService>()
        val connection = object : ServiceConnection {
            override fun onServiceConnected(name: ComponentName, binder: IBinder) { receiver.complete((binder as PushService.PushBinder).getService()) }
            override fun onServiceDisconnected(name: ComponentName) {}
        }
        context.bindService(Intent(context, AndroidPushService::class.java), connection, Context.BIND_AUTO_CREATE)
        try {
            AndroidPush.update(context, saved.id) { it.put("token", "test-grant").put("address", saved.address).put("subscription", JSONObject().put("endpoint", "old-endpoint")) }
            receiver.get(10, TimeUnit.SECONDS).onRegistrationFailed(FailedReason.INTERNAL_ERROR, saved.id)
            val pending = AndroidPush.pending(context)
            assertTrue("Registration must retry without page JavaScript", pending.containsKey(saved.id))
            assertTrue(AndroidPush.read(context, saved.id).optBoolean("needsEndpoint"))
            val job = context.getSystemService(JobScheduler::class.java).getPendingJob(820)
            assertNotNull("An offline registration needs persisted native work", job)
            assertTrue("Avoid a tight distributor failure loop", job!!.minLatencyMillis >= 30_000)
            val old = pending.getValue(saved.id)
            AndroidPush.update(context, saved.id) { it.put("dirty", true).put("preferences", JSONObject().put("peer", true)) }
            AndroidPush.complete(context, saved.id, old, "ready", false)
            assertTrue(AndroidPush.read(context, saved.id).optBoolean("dirty"))
            val latest = AndroidPush.read(context, saved.id)
            AndroidPush.complete(context, saved.id, latest, "ready", false)
            assertFalse(AndroidPush.read(context, saved.id).optBoolean("dirty"))
            // A permission result must not erase an endpoint retry that has no subscription yet.
            AndroidPush.update(context, saved.id) {
                it.remove("subscription")
                it.put("dirty", true).put("needsEndpoint", true).put("enabled", !AndroidPush.allowed(context))
            }
            AndroidPush.state(context, saved)
            assertTrue("Permission changes preserve pending endpoint work", AndroidPush.read(context, saved.id).optBoolean("dirty"))
        } finally {
            context.unbindService(connection)
            context.getSystemService(JobScheduler::class.java).cancel(820)
            context.getSharedPreferences("android-push", Context.MODE_PRIVATE).edit().remove(saved.id).commit()
            store.remove(saved)
            // Flush the preceding asynchronous store removal before the runner exits.
            context.getSharedPreferences("instances", Context.MODE_PRIVATE).edit().commit()
        }
    }
}
