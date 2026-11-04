package com.twicc.android

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.webkit.URLUtil
import android.widget.Toast
import org.mozilla.geckoview.WebResponse
import java.io.IOException
import java.util.concurrent.Executors

/** Save the actual Gecko response. A second HTTP client would lose container cookies and POST data. */
object BrowserDownloads {
    private val worker = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())

    fun save(context: Context, response: WebResponse) {
        val app = context.applicationContext
        val body = response.body
        if (body == null || Uri.parse(response.uri).scheme !in setOf("https", "blob", "data")) {
            runCatching { body?.close() }
            message(app, "This download cannot be saved. Try opening the page in your browser.")
            return
        }
        val disposition = response.headers.entries.firstOrNull { it.key.equals("content-disposition", true) }?.value
        val mime = response.headers.entries.firstOrNull { it.key.equals("content-type", true) }
            ?.value?.substringBefore(';')?.trim()?.takeIf { it.contains('/') } ?: "application/octet-stream"
        val filename = FileNames.safe(URLUtil.guessFileName(response.uri, disposition, mime))
        message(app, "Saving $filename to Downloads…")
        worker.execute {
            var destination: Uri? = null
            try {
                body.use { input ->
                    val resolver = app.contentResolver
                    val target = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, ContentValues().apply {
                        put(MediaStore.MediaColumns.DISPLAY_NAME, filename)
                        put(MediaStore.MediaColumns.MIME_TYPE, mime)
                        put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
                        put(MediaStore.MediaColumns.IS_PENDING, 1)
                    }) ?: throw IOException("Cannot create download")
                    destination = target
                    (resolver.openOutputStream(target) ?: throw IOException("Cannot open download")).use {
                        input.copyTo(it)
                    }
                    resolver.update(target, ContentValues().apply {
                        put(MediaStore.MediaColumns.IS_PENDING, 0)
                    }, null, null)
                }
                message(app, "Saved $filename to Downloads.")
            } catch (_: Exception) {
                destination?.let { runCatching { app.contentResolver.delete(it, null, null) } }
                message(app, "Download failed. Check your connection and try again.")
            }
        }
    }

    private fun message(context: Context, text: String) {
        main.post { Toast.makeText(context, text, Toast.LENGTH_LONG).show() }
    }
}
