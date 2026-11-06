package com.twidi.twicc

import android.app.job.JobScheduler
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
