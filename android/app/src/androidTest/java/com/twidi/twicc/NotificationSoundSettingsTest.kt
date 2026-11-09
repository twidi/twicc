package com.twidi.twicc

import android.app.Activity
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.net.Uri
import android.provider.Settings
import androidx.test.platform.app.InstrumentationRegistry
import com.twidi.twicc.i18n.AndroidLanguageStore
import com.twidi.twicc.i18n.NativeMessages
import com.twidi.twicc.i18n.UiMessage
import com.twidi.twicc.push.AndroidPush
import com.twidi.twicc.push.NotificationSettingsLauncher
import com.twidi.twicc.tts.TtsNotification
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.FixMethodOrder
import org.junit.Test
import org.junit.runners.MethodSorters

/** Disposable emulator only. Start with fresh app data; never reset a phone to run this fixture. */
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class NotificationSoundSettingsTest {
    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private val instance = Instance("sound-settings-test", "Sound settings fixture", "https://sounds.example")
    private val defaults = mapOf("user_turn" to "chime", "pending_request" to "signal",
        "extra_usage" to "sweep", "peer" to "ripple")

    @Test fun aExistingCustomSoundAndSilenceSurviveManagerAndLanguageRefresh() {
        val manager = context.getSystemService(NotificationManager::class.java)
        val custom = Uri.parse("content://${context.packageName}.notification-sounds/beep")
        val attributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION).build()
        // Seed real pre-existing channels. Android ignores app-side sound changes after channel creation.
        manager.createNotificationChannel(NotificationChannel("twicc-user_turn", "Custom fixture",
            NotificationManager.IMPORTANCE_LOW).apply { setSound(custom, attributes) })
        manager.createNotificationChannel(NotificationChannel("twicc-pending_request", "Silent fixture",
            NotificationManager.IMPORTANCE_DEFAULT).apply { setSound(null, null) })
        assertEquals("Use fresh disposable app data for this fixture", custom,
            manager.getNotificationChannel("twicc-user_turn").sound)
        assertNull("Use fresh disposable app data for this fixture", manager.getNotificationChannel("twicc-pending_request").sound)
        // Model the existing silent speech channel. The push manager must not modify it.
        manager.createNotificationChannel(NotificationChannel("tts_playback", "Speech fixture",
            NotificationManager.IMPORTANCE_LOW).apply { setSound(null, null) })
        TtsNotification(context)
        val language = AndroidLanguageStore(context)
        val previous = language.read().locale
        try {
            for (locale in listOf("en", "fr")) {
                assertTrue(language.select(locale, language.read().revision).accepted)
                AndroidPush.manager(context)
                val existing = manager.getNotificationChannel("twicc-user_turn")
                assertEquals(custom, existing.sound)
                assertEquals(NotificationManager.IMPORTANCE_LOW, existing.importance)
                assertEquals(NativeMessages.text(context, UiMessage("android.push.channels.finished")), existing.name.toString())
                assertNull(manager.getNotificationChannel("twicc-pending_request").sound)
                for ((kind, sound) in mapOf("extra_usage" to "sweep", "peer" to "ripple")) {
                    val channel = manager.getNotificationChannel("twicc-$kind")
                    assertEquals(Uri.parse("content://${context.packageName}.notification-sounds/$sound"), channel.sound)
                    assertEquals(AudioAttributes.USAGE_NOTIFICATION, channel.audioAttributes.usage)
                }
                val speech = manager.getNotificationChannel("tts_playback")
                assertEquals(NotificationManager.IMPORTANCE_LOW, speech.importance)
                assertNull(speech.sound)
                assertEquals(setOf("twicc-user_turn", "twicc-pending_request", "twicc-extra_usage", "twicc-peer", "tts_playback"),
                    manager.notificationChannels.map { it.id }.toSet())
            }
        } finally { language.select(previous, language.read().revision) }
    }

    @Test fun allNewCategoryDefaultsUseReadableProviderUrisAndNotificationAudio() {
        for ((kind, sound) in defaults) {
            // Do not persist these channels: the preservation fixture uses existing custom settings.
            val channel = AndroidPush.newNotificationChannel(context, kind)
            assertEquals("twicc-$kind", channel.id)
            assertEquals(NotificationManager.IMPORTANCE_DEFAULT, channel.importance)
            assertEquals(Uri.parse("content://${context.packageName}.notification-sounds/$sound"), channel.sound)
            assertEquals(AudioAttributes.USAGE_NOTIFICATION, channel.audioAttributes.usage)
            context.contentResolver.openInputStream(channel.sound)!!.use {
                val header = ByteArray(4)
                assertEquals(4, it.read(header))
                assertEquals("RIFF", String(header, Charsets.US_ASCII))
            }
        }
    }

    @Test fun eachCategoryOpensItsSettingsAndReturnsUnchangedPushState() = onMain {
        val activity = RecordingActivity(context)
        for (kind in defaults.keys) {
            val reply = dispatch(activity, "push.soundSettings", JSONObject().put("kind", kind))
            assertTrue(reply.getBoolean("ok"))
            assertPushState(reply.getJSONObject("state"))
            val intent = activity.intents.last()
            assertEquals(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS, intent.action)
            assertEquals(context.packageName, intent.getStringExtra(Settings.EXTRA_APP_PACKAGE))
            assertEquals("twicc-$kind", intent.getStringExtra(Settings.EXTRA_CHANNEL_ID))
        }
        assertEquals(4, activity.intents.size)
    }

    @Test fun invalidKindsNeverOpenSettingsAndReturnLocalizedFailure() = onMain {
        val activity = RecordingActivity(context)
        for (kind in listOf<Any>("tts_playback", "push-probe", "unknown", "user_turn/extra", "", 42, JSONObject.NULL)) {
            val reply = dispatch(activity, "push.soundSettings", JSONObject().put("kind", kind))
            assertFailure(reply)
        }
        assertFailure(dispatch(activity, "push.soundSettings"))
        assertTrue(activity.intents.isEmpty())
    }

    @Test fun unavailableCategorySettingsFallBackToAppSettings() = onMain {
        val activity = RecordingActivity(context, setOf(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS))
        NotificationSettingsLauncher.open(activity, "peer")
        assertEquals(listOf(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS, Settings.ACTION_APP_NOTIFICATION_SETTINGS),
            activity.intents.map { it.action })
        assertEquals(context.packageName, activity.intents.last().getStringExtra(Settings.EXTRA_APP_PACKAGE))
    }

    @Test fun unavailableCategoryAndAppSettingsReturnLocalizedFailure() = onMain {
        val activity = RecordingActivity(context, setOf(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS, Settings.ACTION_APP_NOTIFICATION_SETTINGS))
        assertFailure(dispatch(activity, "push.soundSettings", JSONObject().put("kind", "peer")))
        assertEquals(2, activity.intents.size)
    }

    @Test fun soundPublicationDoesNotRequestPermissionChangeChoicesOrChangeChannelSounds() = onMain {
        val activity = RecordingActivity(context)
        val preferences = context.getSharedPreferences("android-push", Context.MODE_PRIVATE)
        val permissionAsked = preferences.all["permissionAsked"]
        val choices = JSONObject().put("user_turn", false).put("pending_request", false).put("extra_usage", false).put("peer", false)
        AndroidPush.update(context, instance.id) { it.put("preferences", choices).put("status", "idle") }
        val before = AndroidPush.read(context, instance.id).toString()
        val manager = AndroidPush.manager(context)
        val sounds = defaults.keys.associateWith { manager.getNotificationChannel("twicc-$it").sound }
        try {
            val reply = dispatch(activity, "push.prepareSounds")
            assertTrue(reply.getBoolean("ok"))
            assertPushState(reply.getJSONObject("state"))
            assertEquals(before, AndroidPush.read(context, instance.id).toString())
            assertEquals(permissionAsked, preferences.all["permissionAsked"])
            assertTrue(activity.intents.isEmpty())
            for ((kind, sound) in sounds) assertEquals(sound, manager.getNotificationChannel("twicc-$kind").sound)
        } finally { preferences.edit().remove(instance.id).commit() }
    }

    private fun assertPushState(state: JSONObject) {
        assertEquals(2, state.getInt("payloadVersion"))
        assertEquals("idle", state.getString("status"))
        assertTrue(state.getString("permission") in setOf("default", "granted", "denied"))
        assertTrue(state.getString("soundLibrary") in setOf("preparing", "ready", "error"))
    }

    private fun onMain(block: () -> Unit) = InstrumentationRegistry.getInstrumentation().runOnMainSync { block() }

    private fun assertFailure(reply: JSONObject) {
        assertFalse(reply.getBoolean("ok"))
        val key = reply.getJSONObject("ui_message").getString("key")
        assertEquals("common.android.errors.actionFailed", key)
        assertTrue(NativeMessages.text(context, UiMessage(key)).isNotEmpty())
        assertFalse(reply.has("state"))
    }

    private fun dispatch(activity: Activity, command: String, arguments: JSONObject = JSONObject()): JSONObject {
        val bridge = AndroidAppBridge({ JSONObject().put("push", AndroidPush.state(context, instance)) }, { _, _ -> },
            { _, _ -> null }, androidPush = { wire, request -> AndroidPush.receive(activity, instance, wire, request) })
        return JSONObject(bridge.receive(arguments.put("protocol", 1).put("id", "sounds").put("command", command).toString()))
    }

    /** Intercept only the external Settings boundary; channel storage and push handling remain real. */
    private class RecordingActivity(base: Context, private val unavailable: Set<String> = emptySet()) : Activity() {
        val intents = mutableListOf<Intent>()
        init { attachBaseContext(base) }
        override fun startActivity(intent: Intent) {
            intents += intent
            if (intent.action in unavailable) throw ActivityNotFoundException()
        }
    }
}
