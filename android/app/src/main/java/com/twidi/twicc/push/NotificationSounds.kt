package com.twidi.twicc.push

import android.content.ContentResolver
import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.util.Log
import com.twidi.twicc.i18n.AndroidLanguageStore
import com.twidi.twicc.i18n.NativeMessages
import com.twidi.twicc.i18n.UiMessage
import org.json.JSONObject
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Packaged defaults stay readable independently of MediaStore and its publication state. */
object NotificationSounds {
    val ids: List<String> = listOf("beep", "chime", "ping", "pop", "bloop", "tink", "knock", "sweep", "signal", "ripple")
    private val lock = Any()
    @Volatile private var publicationState = "preparing"
    private var running = false
    private var requestedLocale: String? = null
    private var successfulCatalog: Pair<String, String>? = null
    @Volatile private var manifest: SoundManifest? = null

    fun uri(context: Context, soundId: String): Uri {
        require(soundId in ids)
        return Uri.parse("content://${context.packageName}.notification-sounds/$soundId")
    }

    fun state(): String = publicationState

    internal fun title(context: Context, soundId: String, locale: String = AndroidLanguageStore(context).read().locale): String {
        require(soundId in ids)
        return NativeMessages.text(context, UiMessage("android.push.soundTitle", mapOf(
            "sound" to UiMessage("settings.notifications.sounds.$soundId"))), locale)
    }

    internal fun catalog(context: Context): SoundManifest = manifest ?: synchronized(lock) {
        manifest ?: context.assets.open("notification-sounds/manifest.json").bufferedReader().use {
            parseManifest(it.readText())
        }.also { manifest = it }
    }

    internal fun parseManifest(source: String): SoundManifest {
        val value = JSONObject(source)
        require(value.getInt("format") == 1)
        val entries = value.getJSONArray("entries")
        require(entries.length() == ids.size)
        val sounds = ids.mapIndexed { index, id ->
            val entry = entries.getJSONObject(index)
            require(entry.getString("id") == id && entry.getString("file") == "$id.wav")
            val hash = entry.getString("sha256")
            require(hash.matches(Regex("[a-f0-9]{64}")))
            val frames = entry.getLong("frames")
            require(frames in 1..Int.MAX_VALUE)
            SoundAsset(id, hash, 44 + frames * 2)
        }
        val fingerprint = value.getString("sourceFingerprint")
        require(fingerprint.matches(Regex("[a-f0-9]{64}")))
        return SoundManifest(fingerprint, sounds)
    }

    /** No MediaStore IO runs on the caller's thread. Failure is retried by the next call. */
    fun publishAsync(context: Context) {
        val app = context.applicationContext
        val locale = AndroidLanguageStore(app).read().locale
        synchronized(lock) {
            requestedLocale = locale
            if (running) return
            // Packaged assets cannot change within a process. The successful key includes their fingerprint.
            val fingerprint = manifest?.fingerprint
            if (fingerprint != null && successfulCatalog == (fingerprint to locale)) return
            publicationState = "preparing"
            running = true
        }
        Thread({ publishRequested(app) }, "TwiCC notification sounds").start()
    }

    private fun publishRequested(context: Context) {
        while (true) {
            val locale = synchronized(lock) { checkNotNull(requestedLocale) }
            val result = runCatching {
                val assets = catalog(context)
                NotificationSoundPublisher(context).reconcile(locale)
                assets.fingerprint to locale
            }
            result.exceptionOrNull()?.let { Log.w("NotificationSounds", "Sound catalog publication failed", it) }
            val latestLocale = AndroidLanguageStore(context).read().locale
            val finished = synchronized(lock) {
                // Re-read persisted language too: concurrent callers can carry an older snapshot.
                if (requestedLocale != locale || latestLocale != locale) {
                    requestedLocale = latestLocale
                    false
                } else {
                    successfulCatalog = result.getOrNull()
                    publicationState = if (result.isSuccess) "ready" else "error"
                    running = false
                    true
                }
            }
            if (finished) return
        }
    }
}

internal data class SoundAsset(val id: String, val sha256: String, val size: Long)
internal data class SoundManifest(val fingerprint: String, val sounds: List<SoundAsset>)

/** Serialized by NotificationSounds. Byte copying is independent of the MediaStore row lifecycle. */
internal class NotificationSoundPublisher(
    private val context: Context,
    private val writeAsset: (String, Uri) -> Unit = { id, uri ->
        context.assets.open("notification-sounds/$id.wav").use { input ->
            val output = context.contentResolver.openOutputStream(uri, "wt") ?: throw IOException("Missing media output")
            output.use { input.copyTo(it) }
        }
    },
) {
    private val resolver = context.contentResolver
    private val collection = MediaStore.Audio.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)

    fun reconcile(locale: String) {
        for (sound in NotificationSounds.catalog(context).sounds) reconcile(sound, locale)
    }

    private fun reconcile(sound: SoundAsset, locale: String) {
        val relativePath = "Notifications/TwiCC/${sound.id}/"
        val rows = ownedRows(relativePath)
        val completed = rows.filter { !it.pending }
        check(completed.size <= 1) { "Multiple completed sound rows" }
        val existing = completed.firstOrNull() ?: rows.firstOrNull()
        // Interruption can leave pending rows. Retain the completed URI and remove only redundant pending rows.
        rows.filter { it.pending && it != existing }.forEach {
            check(resolver.delete(it.uri, null, null) == 1)
        }
        val name = "${NotificationSounds.title(context, sound.id, locale)}.wav"
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, name)
            put(MediaStore.MediaColumns.MIME_TYPE, "audio/wav")
            put(MediaStore.Audio.Media.IS_NOTIFICATION, 1)
            put(MediaStore.Audio.Media.IS_MUSIC, 0)
        }
        val target = existing?.uri ?: resolver.insert(collection, ContentValues(values).apply {
            put(MediaStore.MediaColumns.RELATIVE_PATH, relativePath)
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }) ?: throw IOException("Cannot insert sound row")
        val rewrite = existing == null || existing.pending || !matchesAsset(target, sound)
        val refresh = rewrite || existing.name != name || existing.title != name.removeSuffix(".wav")
        if (refresh) {
            update(target, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 1) })
            if (rewrite) {
                writeAsset(sound.id, target)
                check(matchesAsset(target, sound)) { "Incomplete sound write" }
            }
            // API 29 permits scanner-owned metadata changes only after the row is already pending.
            // Invalidate cached mtime so a same-size rename cannot skip the next scan.
            update(target, ContentValues(values).apply { putNull(MediaStore.MediaColumns.DATE_MODIFIED) })
            update(target, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) })
        } else {
            update(target, values)
        }
        // Query the owned row after rename: DATA can change with DISPLAY_NAME. Never construct a filesystem path.
        val actual = ownedRows(relativePath).single { it.uri == target }
        check(!actual.pending)
        scan(actual)
        val scanned = ownedRows(relativePath).single { it.uri == target }
        check(scanned.name == name && scanned.title == name.removeSuffix(".wav")) { "Sound title scan failed" }
    }

    private fun update(uri: Uri, values: ContentValues) {
        check(resolver.update(uri, values, null, null) == 1) { "Cannot update sound row" }
    }

    private fun matchesAsset(uri: Uri, sound: SoundAsset): Boolean = try {
        resolver.openInputStream(uri)?.use { input ->
            val hash = MessageDigest.getInstance("SHA-256")
            val buffer = ByteArray(8192)
            var size = 0L
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                hash.update(buffer, 0, count)
                size += count
            }
            size == sound.size && hash.digest().joinToString("") { "%02x".format(it.toInt() and 0xff) } == sound.sha256
        } ?: false
    } catch (_: IOException) { false }

    private fun scan(row: OwnedRow) {
        val completed = CountDownLatch(1)
        var scanned: Uri? = null
        MediaScannerConnection.scanFile(context, arrayOf(row.path), arrayOf("audio/wav")) { _, uri ->
            scanned = uri
            completed.countDown()
        }
        check(completed.await(30, TimeUnit.SECONDS)) { "Sound scan timed out" }
        check(scanned?.let { ContentUris.parseId(it) } == ContentUris.parseId(row.uri)) { "Sound scan failed" }
    }

    private data class OwnedRow(val uri: Uri, val pending: Boolean, val path: String, val name: String, val title: String)

    @Suppress("DEPRECATION")
    private fun ownedRows(relativePath: String): List<OwnedRow> {
        val selection = "${MediaStore.MediaColumns.OWNER_PACKAGE_NAME} = ? AND ${MediaStore.MediaColumns.RELATIVE_PATH} = ?"
        val arguments = arrayOf(context.packageName, relativePath)
        val projection = arrayOf(MediaStore.MediaColumns._ID, MediaStore.MediaColumns.IS_PENDING, MediaStore.MediaColumns.DATA,
            MediaStore.MediaColumns.DISPLAY_NAME, MediaStore.Audio.Media.TITLE)
        val cursor = if (Build.VERSION.SDK_INT >= 30) resolver.query(collection, projection, Bundle().apply {
            putInt(MediaStore.QUERY_ARG_MATCH_PENDING, MediaStore.MATCH_INCLUDE)
            putString(ContentResolver.QUERY_ARG_SQL_SELECTION, selection)
            putStringArray(ContentResolver.QUERY_ARG_SQL_SELECTION_ARGS, arguments)
        }, null) else resolver.query(MediaStore.setIncludePending(collection), projection, selection, arguments, null)
        return (cursor ?: throw IOException("Cannot query sound rows")).use { result ->
            buildList {
                while (result.moveToNext()) add(OwnedRow(ContentUris.withAppendedId(collection, result.getLong(0)),
                    result.getInt(1) != 0, result.getString(2), result.getString(3), result.getString(4)))
            }.sortedBy { ContentUris.parseId(it.uri) }
        }
    }
}
