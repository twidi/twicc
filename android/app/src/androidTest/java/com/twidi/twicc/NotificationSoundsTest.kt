package com.twidi.twicc

import android.content.ContentUris
import android.content.ContentValues
import android.media.MediaScannerConnection
import android.media.RingtoneManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.provider.MediaStore
import android.provider.OpenableColumns
import androidx.test.platform.app.InstrumentationRegistry
import com.twidi.twicc.i18n.AndroidLanguageStore
import com.twidi.twicc.push.NotificationSounds
import com.twidi.twicc.push.NotificationSoundPublisher
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Run only on a disposable emulator. These tests never open an Activity or a browser page. */
class NotificationSoundsTest {
    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private val resolver get() = context.contentResolver
    private val collection get() = MediaStore.Audio.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)

    @Test fun packagedProviderWorksWithoutActivityOrPublication() {
        for (id in NotificationSounds.ids) {
            val uri = NotificationSounds.uri(context, id)
            assertEquals("content://${context.packageName}.notification-sounds/$id", uri.toString())
            assertEquals("audio/wav", resolver.getType(uri))
            val bytes = resolver.openInputStream(uri)!!.use { it.readBytes() }
            assertTrue(bytes.size > 44)
            assertEquals("RIFF", String(bytes, 0, 4, Charsets.US_ASCII))
            assertArrayEquals(context.assets.open("notification-sounds/$id.wav").use { it.readBytes() }, bytes)
            resolver.query(uri, null, null, null, null)!!.use {
                assertTrue(it.moveToFirst())
                assertEquals(bytes.size.toLong(), it.getLong(it.getColumnIndexOrThrow(OpenableColumns.SIZE)))
                assertTrue(it.getString(it.getColumnIndexOrThrow(OpenableColumns.DISPLAY_NAME)).endsWith(".wav"))
                assertFalse(it.columnNames.any { name -> name in listOf("_data", "instance", "token") })
                assertFalse(it.moveToNext())
            }
        }
    }

    @Test fun providerRejectsUnknownPathsWritesAndPrivateMetadata() {
        val valid = NotificationSounds.uri(context, "ping")
        for (suffix in listOf("unknown", "ping/extra", "../ping", "ping/", "%70ing", "ping?private=1", "ping#private")) {
            val uri = Uri.parse("content://${context.packageName}.notification-sounds/$suffix")
            assertNull(resolver.getType(uri))
            resolver.query(uri, null, null, null, null)!!.use { assertEquals(0, it.count) }
            expectFailure { resolver.openAssetFileDescriptor(uri, "r")?.close() }
        }
        for (mode in listOf("w", "rw", "wa", "rwt")) expectFailure {
            resolver.openAssetFileDescriptor(valid, mode)?.close()
        }
        expectFailure { resolver.insert(valid, ContentValues()) }
        expectFailure { resolver.update(valid, ContentValues(), null, null) }
        expectFailure { resolver.delete(valid, null, null) }
        expectFailure { resolver.query(valid, arrayOf("_data"), null, null, null)?.close() }
    }

    @Test fun publicationReusesRowsAndRecoversInterruptedPendingWrites() = fixture {
        clearOwnedRows()
        var interrupted = false
        val failing = NotificationSoundPublisher(context) { _, _ ->
            interrupted = true
            throw IOException("Injected interruption after pending insert")
        }
        expectFailure { failing.reconcile("en") }
        assertTrue(interrupted)
        assertEquals(1, rows().size)
        assertEquals(1, rows().single().pending)
        // A new publisher has no previous in-memory state, as after process death.
        NotificationSoundPublisher(context).reconcile("en")
        val completed = rows()
        assertEquals(10, completed.size)
        assertTrue(completed.all { it.pending == 0 })
        // Recover a second interruption beside an already-completed row without replacing its URI.
        val pending = resolver.insert(collection, ContentValues().apply {
            put(MediaStore.MediaColumns.RELATIVE_PATH, "Notifications/TwiCC/beep/")
            put(MediaStore.MediaColumns.DISPLAY_NAME, "interrupted.wav")
            put(MediaStore.MediaColumns.MIME_TYPE, "audio/wav")
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        })
        assertNotNull(pending)
        NotificationSoundPublisher(context).reconcile("en")
        assertEquals(completed.map { it.uri }, rows().map { it.uri })
        assertCatalog("en", completed)
    }

    @Test fun overlappingRequestsAndLanguageChangesKeepPickerUris() = fixture {
        select("en")
        val callers = (1..12).map { Thread { NotificationSounds.publishAsync(context) }.apply { start() } }
        callers.forEach { it.join() }
        awaitReady()
        val before = rows()
        assertEquals(10, before.size)
        assertCatalog("en", before)
        val beforeChime = before.single { it.relativePath == "Notifications/TwiCC/chime/" }
        val beforeChimeBytes = resolver.openInputStream(beforeChime.uri)!!.use { it.readBytes() }
        // Queue more language changes while the first reconciliation can still be running.
        for (locale in listOf("de", "es", "fr")) {
            select(locale)
            NotificationSounds.publishAsync(context)
        }
        awaitReady()
        val localized = rows()
        assertEquals(before.map { it.uri }, localized.map { it.uri })
        assertCatalog("fr", localized)
        val localizedChime = localized.single { it.relativePath == beforeChime.relativePath }
        assertNotEquals(beforeChime.name, localizedChime.name)
        assertNotEquals(beforeChime.title, localizedChime.title)
        assertEquals(NotificationSounds.title(context, "chime", "fr"), localizedChime.title)
        assertEquals(beforeChime.uri, localizedChime.uri)
        assertArrayEquals(beforeChimeBytes, resolver.openInputStream(localizedChime.uri)!!.use { it.readBytes() })
        assertEquals("ready", NotificationSounds.state())
        // An explicit scanner pass must not restore an old, embedded WAV title.
        localized.forEach { row ->
            val done = CountDownLatch(1)
            MediaScannerConnection.scanFile(context, arrayOf(row.path), arrayOf("audio/wav")) { _, _ -> done.countDown() }
            assertTrue(done.await(30, TimeUnit.SECONDS))
        }
        assertCatalog("fr", rows())
    }

    @Test fun heldPublicWorkerReconcilesLatestPersistedLocaleWithoutOverlap() = fixture {
        select("en")
        clearOwnedRows()
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val finished = CountDownLatch(1)
        val active = AtomicInteger()
        val maximumActive = AtomicInteger()
        val locales = mutableListOf<String>()
        val hooks = NotificationSounds.overrideReconciliationForTests({ locale ->
            val count = active.incrementAndGet()
            maximumActive.updateAndGet { maxOf(it, count) }
            try {
                locales.add(locale)
                if (locales.size == 1) {
                    entered.countDown()
                    check(release.await(30, TimeUnit.SECONDS)) { "Held reconciliation timed out" }
                }
                NotificationSoundPublisher(context).reconcile(locale)
            } finally { active.decrementAndGet() }
        }, { finished.countDown() })
        try {
            NotificationSounds.publishAsync(context)
            assertTrue(entered.await(30, TimeUnit.SECONDS))
            assertEquals("preparing", NotificationSounds.state())
            select("de")
            repeat(12) { NotificationSounds.publishAsync(context) }
            // Persist a newer locale without another call. The held worker must re-read it.
            select("fr")
            assertEquals(1, active.get())
            release.countDown()
            assertTrue(finished.await(60, TimeUnit.SECONDS))
            assertEquals("ready", NotificationSounds.state())
            assertEquals(listOf("en", "fr"), locales)
            assertEquals(1, maximumActive.get())
            assertEquals(0, active.get())
            val completed = rows()
            assertEquals(10, completed.size)
            assertCatalog("fr", completed)
        } finally {
            release.countDown()
            try { assertTrue(finished.await(60, TimeUnit.SECONDS)) }
            finally { hooks.close() }
        }
    }

    @Test fun publicFailureReleasesWorkerAndRetryPublishesReadyCatalog() = fixture {
        select("en")
        clearOwnedRows()
        val attempts = AtomicInteger()
        val active = AtomicInteger()
        val maximumActive = AtomicInteger()
        val released = LinkedBlockingQueue<String>()
        val hooks = NotificationSounds.overrideReconciliationForTests({ locale ->
            val count = active.incrementAndGet()
            maximumActive.updateAndGet { maxOf(it, count) }
            try {
                if (attempts.incrementAndGet() == 1) {
                    NotificationSoundPublisher(context) { _, _ ->
                        throw IOException("Injected public worker failure after pending insert")
                    }.reconcile(locale)
                } else NotificationSoundPublisher(context).reconcile(locale)
            } finally { active.decrementAndGet() }
        }, { released.add(NotificationSounds.state()) })
        try {
            NotificationSounds.publishAsync(context)
            assertEquals("error", released.poll(60, TimeUnit.SECONDS))
            assertEquals("error", NotificationSounds.state())
            assertEquals(0, active.get())
            assertEquals(1, rows().size)
            assertEquals(1, rows().single().pending)
            NotificationSounds.publishAsync(context)
            assertEquals("ready", released.poll(60, TimeUnit.SECONDS))
            assertEquals("ready", NotificationSounds.state())
            assertEquals(2, attempts.get())
            assertEquals(1, maximumActive.get())
            assertEquals(0, active.get())
            val completed = rows()
            assertEquals(10, completed.size)
            assertCatalog("en", completed)
        } finally { hooks.close() }
    }

    private fun assertCatalog(locale: String, entries: List<Row>) {
        val picker = RingtoneManager(context).apply { setType(RingtoneManager.TYPE_NOTIFICATION) }
        val discovered = mutableMapOf<Long, Pair<Uri, String>>()
        picker.cursor.use { cursor ->
            while (cursor.moveToNext()) {
                val uri = picker.getRingtoneUri(cursor.position)
                if (uri.authority == MediaStore.AUTHORITY && uri.pathSegments.firstOrNull() != "internal") {
                    discovered[ContentUris.parseId(uri)] = uri to cursor.getString(RingtoneManager.TITLE_COLUMN_INDEX)
                }
            }
        }
        for (id in NotificationSounds.ids) {
            val row = entries.single { it.relativePath == "Notifications/TwiCC/$id/" }
            val title = NotificationSounds.title(context, id, locale)
            assertEquals("$title.wav", row.name)
            assertEquals(title, row.title)
            assertEquals(0, row.pending)
            val picked = discovered[ContentUris.parseId(row.uri)]
            assertNotNull("Picker misses $id", picked)
            assertEquals(title, picked!!.second)
            assertArrayEquals(context.assets.open("notification-sounds/$id.wav").use { it.readBytes() },
                resolver.openInputStream(picked.first)!!.use { it.readBytes() })
        }
    }

    private fun select(locale: String) {
        val store = AndroidLanguageStore(context)
        assertTrue(store.select(locale, store.read().revision).accepted)
    }

    private fun awaitReady() {
        val deadline = SystemClock.elapsedRealtime() + 60_000
        while (NotificationSounds.state() == "preparing" && SystemClock.elapsedRealtime() < deadline) SystemClock.sleep(25)
        assertEquals("ready", NotificationSounds.state())
    }

    private fun fixture(block: () -> Unit) {
        val previous = AndroidLanguageStore(context).read().locale
        try { block() } finally {
            select(previous)
            NotificationSoundPublisher(context).reconcile(previous)
            NotificationSounds.publishAsync(context)
            awaitReady()
        }
    }

    private data class Row(val uri: Uri, val relativePath: String, val name: String, val title: String,
        val pending: Int, val path: String)

    @Suppress("DEPRECATION")
    private fun rows(): List<Row> {
        val selection = "${MediaStore.MediaColumns.OWNER_PACKAGE_NAME} = ? AND ${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?"
        val arguments = arrayOf(context.packageName, "Notifications/TwiCC/%")
        val query = if (Build.VERSION.SDK_INT >= 30) resolver.query(collection, null, Bundle().apply {
            putInt(MediaStore.QUERY_ARG_MATCH_PENDING, MediaStore.MATCH_INCLUDE)
            putString(android.content.ContentResolver.QUERY_ARG_SQL_SELECTION, selection)
            putStringArray(android.content.ContentResolver.QUERY_ARG_SQL_SELECTION_ARGS, arguments)
        }, null) else resolver.query(MediaStore.setIncludePending(collection), null, selection, arguments, null)
        return query!!.use { cursor -> buildList {
            while (cursor.moveToNext()) add(Row(ContentUris.withAppendedId(collection,
                cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.MediaColumns._ID))),
                cursor.getString(cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.RELATIVE_PATH)),
                cursor.getString(cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)),
                cursor.getString(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.TITLE)),
                cursor.getInt(cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.IS_PENDING)),
                cursor.getString(cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DATA))))
        }.sortedBy { it.relativePath } }
    }

    private fun clearOwnedRows() { rows().forEach { resolver.delete(it.uri, null, null) } }

    private fun expectFailure(block: () -> Unit) {
        try { block() } catch (_: Exception) { return }
        fail("Expected operation to fail")
    }
}
