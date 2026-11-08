package com.twidi.twicc

import android.app.Notification
import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.content.ServiceConnection
import android.content.SharedPreferences
import android.os.Build
import android.os.IBinder
import android.os.Process
import android.os.SystemClock
import androidx.test.platform.app.InstrumentationRegistry
import com.twidi.twicc.i18n.AndroidLanguageStore
import com.twidi.twicc.i18n.NativeMessages
import com.twidi.twicc.push.AndroidPush
import com.twidi.twicc.push.AndroidPushService
import com.twidi.twicc.push.PushPayload
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.unifiedpush.android.connector.PushService
import org.unifiedpush.android.connector.data.PushMessage
import java.time.Instant
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

/** Real packaged catalogs, persisted installation language, bound receiver, and system notifications. */
class PushLocalizationTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private val notifications get() = context.getSystemService(NotificationManager::class.java)

    private fun select(locale: String, target: Context = context) {
        val store = AndroidLanguageStore(target)
        assertTrue(store.select(locale, store.read().revision).accepted)
    }

    private fun grantPermission() {
        if (Build.VERSION.SDK_INT >= 33) {
            instrumentation.uiAutomation.executeShellCommand(
                "pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS"
            ).use { android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes() }
        }
    }

    private fun withService(block: (PushService) -> Unit) {
        val receiver = CompletableFuture<PushService>()
        val connection = object : ServiceConnection {
            override fun onServiceConnected(name: ComponentName, binder: IBinder) {
                receiver.complete((binder as PushService.PushBinder).getService())
            }
            override fun onServiceDisconnected(name: ComponentName) {}
        }
        assertTrue(context.bindService(Intent(context, AndroidPushService::class.java), connection, Context.BIND_AUTO_CREATE))
        try { block(receiver.get(10, TimeUnit.SECONDS)) }
        finally { context.unbindService(connection) }
    }

    private fun createInstance(): Instance {
        val instance = InstanceStore(context).save(null, "Localization", "https://push-${UUID.randomUUID()}.example")
        AndroidPush.update(context, instance.id) {
            it.put("preferences", JSONObject().put("user_turn", true).put("pending_request", true)
                .put("extra_usage", true).put("peer", true))
        }
        return instance
    }

    private fun removeInstance(instance: Instance) {
        notifications.activeNotifications.filter { it.tag?.startsWith("twicc-push:${instance.id}:") == true }
            .forEach { notifications.cancel(it.tag, it.id) }
        context.getSharedPreferences("android-push", Context.MODE_PRIVATE).edit().remove(instance.id).commit()
        InstanceStore(context).remove(instance)
    }

    private fun fixture(block: (PushService, Instance) -> Unit) {
        grantPermission()
        val previousLocale = AndroidLanguageStore(context).read().locale
        val instance = createInstance()
        try { withService { block(it, instance) } }
        finally {
            removeInstance(instance)
            select(previousLocale)
            AndroidPush.manager(context)
        }
    }

    private fun payload(): JSONObject = JSONObject().put("type", "twicc.notification").put("version", 2)
        .put("id", UUID.randomUUID().toString()).put("sent_at", Instant.now().toString()).put("kind", "user_turn")
        .put("title_ui_message", JSONObject().put("key", "session.notifications.finished")
            .put("params", JSONObject().put("provider", "Codex")))
        .put("body_ui_message", JSONObject().put("key", "session.notifications.body")
            .put("params", JSONObject().put("project", "Project").put("session", "Session")))
        .put("path", "/project/p/session/s${UUID.randomUUID().toString().replace("-", "")}")

    private fun receive(service: PushService, instance: Instance, value: JSONObject,
        expectedTitle: String? = null): android.service.notification.StatusBarNotification {
        instrumentation.runOnMainSync {
            service.onMessage(PushMessage(value.toString().toByteArray(), true), instance.id)
        }
        val deadline = SystemClock.elapsedRealtime() + 10_000
        val session = PushPayload.parse(value.toString().toByteArray())!!.sessionId
        val tag = "twicc-push:${instance.id}:user_turn:$session"
        while (true) {
            notifications.activeNotifications.singleOrNull { it.tag == tag &&
                (expectedTitle == null || it.notification.extras.getString(Notification.EXTRA_TITLE) == expectedTitle)
            }?.let { return it }
            if (SystemClock.elapsedRealtime() > deadline) throw AssertionError("No localized notification: $tag")
            SystemClock.sleep(50)
        }
    }

    @Test fun legacyPayloadUsesLocalizedGenericText() = fixture { service, instance ->
        select("fr")
        val legacy = payload().apply {
            remove("version"); remove("title_ui_message"); remove("body_ui_message")
            put("title", "Old English title"); put("body", "Old English body")
        }
        val posted = receive(service, instance, legacy).notification
        assertEquals("Notification TwiCC · Localization", posted.extras.getString(Notification.EXTRA_TITLE))
        assertEquals("Ouvrez TwiCC pour consulter cette notification.", posted.extras.getString(Notification.EXTRA_TEXT))
        assertNotNull(posted.contentIntent)
    }

    @Test fun unknownKeyOrParamsUsesGenericWithSameTap() = fixture { service, instance ->
        select("de")
        for ((field, descriptor) in listOf(
            "title_ui_message" to JSONObject().put("key", "unknown.future.message"),
            "body_ui_message" to JSONObject().put("key", "unknown.future.message"),
            "title_ui_message" to JSONObject().put("key", "session.notifications.finished"),
            "body_ui_message" to JSONObject().put("key", "session.notifications.body").put("params", "invalid")
        )) {
            val value = payload()
            val first = receive(service, instance, value).notification.contentIntent
            value.put(field, descriptor)
            val posted = receive(service, instance, value, "TwiCC-Benachrichtigung · Localization").notification
            assertEquals("TwiCC-Benachrichtigung · Localization", posted.extras.getString(Notification.EXTRA_TITLE))
            assertEquals("Öffne TwiCC, um diese Benachrichtigung anzusehen.", posted.extras.getString(Notification.EXTRA_TEXT))
            assertEquals(first, posted.contentIntent)
        }
    }

    @Test fun languageChangeKeepsSubscriptionAndChannelSettings() = fixture { service, instance ->
        AndroidPush.update(context, instance.id) {
            it.put("subscription", JSONObject().put("endpoint", "https://push.example/subscription"))
                .put("token", "fixture-token").put("dirty", false)
        }
        val registration = AndroidPush.read(context, instance.id).toString()
        val originalChannels = AndroidPush.manager(context).notificationChannels.associateBy { it.id }
        select("fr")
        val first = receive(service, instance, payload())
        val firstTitle = first.notification.extras.getString(Notification.EXTRA_TITLE)
        val firstBody = first.notification.extras.getString(Notification.EXTRA_TEXT)
        assertEquals("Codex a terminé son travail · Localization", firstTitle)
        assertEquals("Projet : Project\nSession : Session", firstBody)
        assertEquals("Travail terminé", notifications.getNotificationChannel("twicc-user_turn").name.toString())
        select("de")
        AndroidPush.manager(context)
        val unchanged = notifications.activeNotifications.single { it.key == first.key }
        assertEquals(first.postTime, unchanged.postTime)
        assertEquals(firstTitle, unchanged.notification.extras.getString(Notification.EXTRA_TITLE))
        assertEquals(firstBody, unchanged.notification.extras.getString(Notification.EXTRA_TEXT))
        val second = receive(service, instance, payload()).notification
        assertEquals("Codex hat die Arbeit beendet · Localization", second.extras.getString(Notification.EXTRA_TITLE))
        assertEquals("Projekt: Project\nSitzung: Session", second.extras.getString(Notification.EXTRA_TEXT))
        assertEquals("Abgeschlossene Arbeit", notifications.getNotificationChannel("twicc-user_turn").name.toString())
        assertEquals(registration, AndroidPush.read(context, instance.id).toString())
        for ((id, before) in originalChannels) {
            val after = notifications.getNotificationChannel(id)
            assertEquals(before.importance, after.importance)
            assertEquals(before.sound, after.sound)
            assertArrayEquals(before.vibrationPattern, after.vibrationPattern)
            assertEquals(before.shouldShowLights(), after.shouldShowLights())
            assertEquals(before.canShowBadge(), after.canShowBadge())
        }
    }

    @Test fun sameEventUsesIndependentInstallationLanguagesWithoutAPage() {
        val files = listOf("push-language-fr-${UUID.randomUUID()}", "push-language-de-${UUID.randomUUID()}")
        fun device(name: String): Context = object : ContextWrapper(context) {
            override fun getSharedPreferences(file: String, mode: Int): SharedPreferences =
                super.getSharedPreferences(if (file == "app-language") name else file, mode)
        }
        try {
            val french = device(files[0])
            val german = device(files[1])
            select("fr", french); select("de", german)
            val event = PushPayload.parse(payload().toString().toByteArray())!!
            assertEquals("Codex a terminé son travail", NativeMessages.render(french, event.title))
            assertEquals("Projet : Project\nSession : Session", NativeMessages.render(french, event.body))
            assertEquals("Codex hat die Arbeit beendet", NativeMessages.render(german, event.title))
            assertEquals("Projekt: Project\nSitzung: Session", NativeMessages.render(german, event.body))
            assertEquals("fr", AndroidLanguageStore(french).read().locale)
            assertEquals("de", AndroidLanguageStore(german).read().locale)
        } finally { files.forEach { context.deleteSharedPreferences(it) } }
    }

    /** Run twice with pushProcessPhase=prepare/verify, force-stopping the target between runs.
     * A missing phase skips this test; ordinary instrumentation never pretends to recreate a process.
     */
    @Test fun rendersVersion2AfterProcessRecreation() {
        val phase = InstrumentationRegistry.getArguments().getString("pushProcessPhase")
        assumeTrue("Requires the documented two-invocation process-death check", phase in listOf("prepare", "verify"))
        val state = context.getSharedPreferences("push-localization-process-test", Context.MODE_PRIVATE)
        if (phase == "prepare") {
            grantPermission()
            assertFalse("Finish or clean the previous process fixture first", state.contains("instance"))
            val previous = AndroidLanguageStore(context).read().locale
            val instance = createInstance()
            select("fr")
            assertTrue(state.edit().putString("instance", instance.id).putString("previousLocale", previous)
                .putInt("pid", Process.myPid()).commit())
            return
        }
        assertTrue("Run the prepare phase first", state.contains("instance"))
        assertNotEquals("The target process must actually restart", state.getInt("pid", -1), Process.myPid())
        val instance = InstanceStore(context).find(state.getString("instance", null))!!
        try {
            // Do not select a language or initialize an Activity in this fresh process.
            assertEquals("fr", AndroidLanguageStore(context).read().locale)
            withService { service ->
                val posted = receive(service, instance, payload()).notification
                assertEquals("Codex a terminé son travail · Localization", posted.extras.getString(Notification.EXTRA_TITLE))
                assertEquals("Projet : Project\nSession : Session", posted.extras.getString(Notification.EXTRA_TEXT))
                assertNotNull(posted.contentIntent)
            }
        } finally {
            removeInstance(instance)
            select(state.getString("previousLocale", "en")!!)
            state.edit().clear().commit()
            AndroidPush.manager(context)
        }
    }
}
