package com.twidi.twicc

import android.app.Notification
import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import android.os.SystemClock
import androidx.test.platform.app.InstrumentationRegistry
import com.twidi.twicc.push.AndroidPush
import com.twidi.twicc.push.AndroidPushService
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.unifiedpush.android.connector.PushService
import org.unifiedpush.android.connector.data.PushMessage
import java.time.Instant
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

/** Real notifications and decrypted push handling; no browser Activity or JavaScript is running. */
class PushSessionDismissalTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private val notifications get() = context.getSystemService(NotificationManager::class.java)

    @Test fun dismissesOnlyTheSessionAndInstanceAndRejectsLateNotificationsWithoutErasingNewWork() {
        instrumentation.uiAutomation.executeShellCommand("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS").close()
        val names = listOf("instances", "android-push", "android-push-dismissals")
        val backups = names.associateWith { context.getSharedPreferences(it, Context.MODE_PRIVATE).all.toMap() }
        val receiver = CompletableFuture<PushService>()
        val connection = object : ServiceConnection {
            override fun onServiceConnected(name: ComponentName, binder: IBinder) { receiver.complete((binder as PushService.PushBinder).getService()) }
            override fun onServiceDisconnected(name: ComponentName) {}
        }
        context.bindService(Intent(context, AndroidPushService::class.java), connection, Context.BIND_AUTO_CREATE)
        val service = receiver.get(10, TimeUnit.SECONDS)
        try {
            val store = InstanceStore(context)
            val one = store.save(null, "Dismiss one", "https://dismiss-one.example")
            val two = store.save(null, "Dismiss two", "https://dismiss-two.example")
            val choices = JSONObject().put("user_turn", true).put("pending_request", true).put("extra_usage", true).put("peer", true)
            listOf(one, two).forEach { instance -> AndroidPush.update(context, instance.id) { it.put("preferences", choices) } }
            val before = Instant.now().minusSeconds(3)
            val a = post(service, one.id, "a", "user_turn", before)
            val question = post(service, one.id, "a", "pending_request", before)
            val b = post(service, one.id, "b", "user_turn", before)
            val other = post(service, two.id, "a", "user_turn", before)
            val peer = post(service, one.id, null, "peer", before)
            val speech = Notification.Builder(context, AndroidPush.channel("user_turn")).setSmallIcon(R.drawable.ic_notification)
                .setContentTitle("Speech fixture").build()
            notifications.notify("twicc-speech-fixture", 123, speech)
            waitFor { listOf(a, question, b, other, peer).all { notification(it) != null } }

            // Dismissal does not depend on the current notification choices.
            AndroidPush.update(context, one.id) { it.put("preferences", JSONObject()) }
            val cutoff = Instant.now().minusSeconds(1)
            val dismissal = JSONObject().put("type", "twicc.dismiss_sessions").put("id", UUID.randomUUID().toString())
                .put("sent_at", cutoff.toString()).put("session_ids", JSONArray(listOf("a")))
            receive(service, one.id, dismissal, decrypted = false)
            assertNotNull(notification(a))
            receive(service, one.id, dismissal)
            waitFor { notification(a) == null && notification(question) == null }
            listOf(b, other, peer).forEach { assertNotNull(notification(it)) }
            assertTrue(notifications.activeNotifications.any { it.tag == "twicc-speech-fixture" })

            AndroidPush.update(context, one.id) { it.put("preferences", choices) }
            val late = post(service, one.id, "a", "user_turn", before)
            SystemClock.sleep(200)
            assertNull("An older push must not resurrect the dismissed notification", notification(late))
            val fresh = post(service, one.id, "a", "user_turn", Instant.now())
            waitFor { notification(fresh) != null }
            receive(service, one.id, dismissal)
            assertNotNull("A delayed dismissal must preserve newer work", notification(fresh))

            val preferences = context.getSharedPreferences("android-push-dismissals", Context.MODE_PRIVATE)
            preferences.edit().putLong("${one.id}:expired", Instant.now().minusSeconds(601).toEpochMilli()).commit()
            post(service, one.id, "b", "user_turn", Instant.now())
            assertFalse("Expired delivery cutoffs must be removed", preferences.contains("${one.id}:expired"))
        } finally {
            context.unbindService(connection)
            notifications.activeNotifications.filter { it.tag?.startsWith("twicc-push:") == true || it.tag == "twicc-speech-fixture" }
                .forEach { notifications.cancel(it.tag, it.id) }
            backups.forEach { (name, values) -> context.getSharedPreferences(name, Context.MODE_PRIVATE).edit().clear().apply {
                values.forEach { (key, value) -> when (value) {
                    is String -> putString(key, value)
                    is Long -> putLong(key, value)
                    is Boolean -> putBoolean(key, value)
                    is Set<*> -> putStringSet(key, value.filterIsInstance<String>().toSet())
                } }
            }.commit() }
        }
    }

    private fun receive(service: PushService, instance: String, payload: JSONObject, decrypted: Boolean = true) {
        instrumentation.runOnMainSync { service.onMessage(PushMessage(payload.toString().toByteArray(), decrypted), instance) }
        instrumentation.waitForIdleSync()
    }

    private fun post(service: PushService, instance: String, session: String?, kind: String, sentAt: Instant): String {
        val id = UUID.randomUUID().toString()
        val payload = JSONObject().put("type", "twicc.notification").put("id", id).put("sent_at", sentAt.toString())
            .put("kind", kind).put("version", 2)
            .put("title_ui_message", JSONObject().put("key", "session.notifications.finished")
                .put("params", JSONObject().put("provider", id)))
            .put("body_ui_message", JSONObject().put("key", "android.push.genericBody"))
        session?.let { payload.put("path", "/project/p/session/$it") }
        receive(service, instance, payload)
        return id
    }
    private fun notification(id: String) = notifications.activeNotifications.singleOrNull { it.notification.extras.getString("android.title")?.contains(id) == true }
    private fun waitFor(condition: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 10_000
        while (!condition()) { if (SystemClock.elapsedRealtime() > deadline) fail("Timed out waiting for notifications"); SystemClock.sleep(50) }
    }
}
