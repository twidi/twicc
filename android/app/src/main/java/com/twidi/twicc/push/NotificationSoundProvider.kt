package com.twidi.twicc.push

import android.content.ContentProvider
import android.content.ContentValues
import android.content.res.AssetFileDescriptor
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.provider.MediaStore
import android.provider.OpenableColumns
import java.io.FileNotFoundException

/** Public, fixed-resource reads only. Startup never touches MediaStore or opens an Activity. */
class NotificationSoundProvider : ContentProvider() {
    override fun onCreate(): Boolean = true

    private fun soundId(uri: Uri): String? {
        val owner = context ?: return null
        if (uri.scheme != "content" || uri.authority != "${owner.packageName}.notification-sounds" ||
            uri.query != null || uri.fragment != null) return null
        return NotificationSounds.ids.firstOrNull { uri.encodedPath == "/$it" }
    }

    override fun getType(uri: Uri): String? = soundId(uri)?.let { "audio/wav" }

    override fun openAssetFile(uri: Uri, mode: String): AssetFileDescriptor {
        if (mode != "r") throw FileNotFoundException("Read-only sound provider")
        val id = soundId(uri) ?: throw FileNotFoundException("Unknown sound")
        return requireNotNull(context).assets.openFd("notification-sounds/$id.wav")
    }

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?,
        selectionArgs: Array<out String>?, sortOrder: String?): Cursor {
        require(selection == null && selectionArgs == null && sortOrder == null) { "Unsupported sound query" }
        val columns = projection ?: arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE,
            MediaStore.Audio.Media.TITLE, MediaStore.MediaColumns.MIME_TYPE)
        require(columns.all { it in allowedColumns }) { "Unsupported sound metadata column" }
        val cursor = MatrixCursor(columns)
        val id = soundId(uri) ?: return cursor
        val owner = requireNotNull(context)
        val title = NotificationSounds.title(owner, id)
        val size = owner.assets.openFd("notification-sounds/$id.wav").use { it.length }
        cursor.addRow(columns.map {
            when (it) {
                OpenableColumns.DISPLAY_NAME -> "$title.wav"
                OpenableColumns.SIZE -> size
                MediaStore.Audio.Media.TITLE -> title
                MediaStore.MediaColumns.MIME_TYPE -> "audio/wav"
                MediaStore.MediaColumns._ID -> NotificationSounds.ids.indexOf(id) + 1
                else -> error("Unsupported sound metadata column")
            }
        })
        return cursor
    }

    override fun insert(uri: Uri, values: ContentValues?): Uri = throw UnsupportedOperationException("Read-only sound provider")
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int =
        throw UnsupportedOperationException("Read-only sound provider")
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int =
        throw UnsupportedOperationException("Read-only sound provider")

    companion object {
        private val allowedColumns = setOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE,
            MediaStore.Audio.Media.TITLE, MediaStore.MediaColumns.MIME_TYPE, MediaStore.MediaColumns._ID)
    }
}
