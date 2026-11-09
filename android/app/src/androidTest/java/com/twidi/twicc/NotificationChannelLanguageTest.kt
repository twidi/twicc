package com.twidi.twicc

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Intent
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import com.twidi.twicc.i18n.AndroidLanguageStore
import com.twidi.twicc.push.AndroidPush
import com.twidi.twicc.tts.TtsNotification
import org.junit.Assert.*
import org.junit.Test

/** Disposable emulator only. No page, push, or speech activation triggers these label refreshes. */
class NotificationChannelLanguageTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private val manager get() = context.getSystemService(NotificationManager::class.java)
    private val language get() = AndroidLanguageStore(context)

    @Test fun startupRefreshesExistingCategoriesWithoutSpeechOrPageActivity() {
        val original = language.read().locale
        try {
            select("fr")
            AndroidPush.manager(context)
            TtsNotification(context)
            val before = channels()
            val active = manager.activeNotifications.map { it.key }.toSet()
            select("en")
            launch().use {
                instrumentation.waitForIdleSync()
                assertNames("en")
                assertSettings(before)
                assertEquals(active, manager.activeNotifications.map { it.key }.toSet())
            }
        } finally { select(original) }
    }

    @Test fun languageChangesRefreshEveryCategoryWithoutSpeechOrPageActivity() {
        val original = language.read().locale
        try {
            select("fr")
            AndroidPush.manager(context)
            TtsNotification(context)
            val before = channels()
            val active = manager.activeNotifications.map { it.key }.toSet()
            launch().use {
                for (locale in listOf("en", "fr", "en")) {
                    select(locale)
                    instrumentation.waitForIdleSync()
                    assertNames(locale)
                    assertSettings(before)
                    assertEquals(active, manager.activeNotifications.map { it.key }.toSet())
                }
            }
        } finally { select(original) }
    }

    private fun launch(): ActivityScenario<MainActivity> = ActivityScenario.launch(
        Intent(context, MainActivity::class.java).putExtra("manage", true))

    private fun select(locale: String) {
        assertTrue(language.select(locale, language.read().revision).accepted)
    }

    private fun channels() = manager.notificationChannels.associateBy { it.id }

    private fun assertNames(locale: String) {
        val expected = if (locale == "fr") listOf("Travail terminé", "Questions et approbations",
            "Utilisation supplémentaire", "Messages et demandes des pairs", "Lecture de la chronologie")
        else listOf("Finished work", "Questions and approvals", "Extra usage",
            "Peer messages and requests", "Timeline speech")
        val ids = listOf("twicc-user_turn", "twicc-pending_request", "twicc-extra_usage", "twicc-peer", "tts_playback")
        assertEquals(ids.toSet(), channels().keys)
        ids.zip(expected).forEach { (id, name) -> assertEquals(id, name, manager.getNotificationChannel(id).name.toString()) }
    }

    private fun assertSettings(before: Map<String, NotificationChannel>) {
        val after = channels()
        assertEquals(before.keys, after.keys)
        for ((id, previous) in before) {
            val current = after.getValue(id)
            assertEquals(id, previous.importance, current.importance)
            assertEquals(id, previous.sound, current.sound)
            assertEquals(id, previous.audioAttributes, current.audioAttributes)
            assertEquals(id, previous.shouldVibrate(), current.shouldVibrate())
            assertArrayEquals(id, previous.vibrationPattern, current.vibrationPattern)
            assertEquals(id, previous.shouldShowLights(), current.shouldShowLights())
            assertEquals(id, previous.lightColor, current.lightColor)
            assertEquals(id, previous.canShowBadge(), current.canShowBadge())
            assertEquals(id, previous.lockscreenVisibility, current.lockscreenVisibility)
        }
    }
}
