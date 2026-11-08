package com.twidi.twicc.platform

import com.twidi.twicc.i18n.UiMessage
import com.twidi.twicc.i18n.NativeMessages
import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.webkit.URLUtil
import android.widget.Toast
import com.twidi.twicc.FileNames
import com.twidi.twicc.InstanceUrl
import android.app.DownloadManager
import java.io.InputStream
import java.io.IOException
import java.util.concurrent.Executors

sealed interface DownloadSource {
    val url: String
    val mime: String?
    val disposition: String?
    data class HttpRequest(override val url: String, override val mime: String?, override val disposition: String?,
        val headers: Map<String, String>) : DownloadSource
    data class Stream(override val url: String, override val mime: String?, override val disposition: String?,
        val body: InputStream?) : DownloadSource
}

/** HTTP downloads use Android's manager. Engine streams retain their authenticated response body. */
object BrowserDownloads {
    private val worker = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())

    fun save(context: Context, source: DownloadSource) {
        when (source) {
            is DownloadSource.HttpRequest -> saveHttp(context, source)
            is DownloadSource.Stream -> saveStream(context, source)
        }
    }

    private fun saveHttp(context: Context, source: DownloadSource.HttpRequest) {
        if (!source.url.startsWith("https://", true) || !InstanceUrl.canOpenExternally(source.url)) {
            message(context, UiMessage("android.downloads.browserRequired"))
            return
        }
        try {
            val filename = FileNames.safe(URLUtil.guessFileName(source.url, source.disposition, source.mime))
            val request = DownloadManager.Request(Uri.parse(source.url)).setTitle(filename)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename)
            source.mime?.takeIf { it.isNotBlank() }?.let { request.setMimeType(it) }
            source.headers.forEach { (name, value) -> request.addRequestHeader(name, value) }
            (context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(request)
            message(context, UiMessage("android.downloads.started"))
        } catch (_: RuntimeException) {
            message(context, UiMessage("android.downloads.errors.startFailed"))
        }
    }

    private fun saveStream(context: Context, response: DownloadSource.Stream) {
        val app = context.applicationContext
        val body = response.body
        if (body == null || Uri.parse(response.url).scheme !in setOf("https", "blob", "data")) {
            runCatching { body?.close() }
            message(app, UiMessage("android.downloads.errors.saveUnavailable"))
            return
        }
        val disposition = response.disposition
        val mime = response.mime?.substringBefore(';')?.trim()?.takeIf { it.contains('/') } ?: "application/octet-stream"
        val filename = FileNames.safe(URLUtil.guessFileName(response.url, disposition, mime))
        message(app, UiMessage("android.downloads.saving", mapOf("filename" to filename)))
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
                message(app, UiMessage("android.downloads.saved", mapOf("filename" to filename)))
            } catch (_: Exception) {
                destination?.let { runCatching { app.contentResolver.delete(it, null, null) } }
                message(app, UiMessage("android.downloads.errors.failed"))
            }
        }
    }

    private fun message(context: Context, message: UiMessage) {
        main.post { Toast.makeText(context, NativeMessages.text(context, message), Toast.LENGTH_LONG).show() }
    }
}
