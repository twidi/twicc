package com.twidi.twicc.engines.gecko

import android.app.Activity
import android.net.Uri
import android.provider.OpenableColumns
import com.twidi.twicc.FileNames
import com.twidi.twicc.platform.*
import org.mozilla.geckoview.GeckoResult
import org.mozilla.geckoview.GeckoSession
import org.mozilla.geckoview.GeckoSession.PromptDelegate
import org.mozilla.geckoview.GeckoSession.PromptDelegate.*
import java.io.File
import java.io.IOException
import java.util.UUID
import java.util.concurrent.Executors

/** Translates Gecko prompt responses. Only Gecko needs temporary file paths for uploads. */
internal class GeckoPrompts(private val activity: Activity, private val requests: NativeRequests) : PromptDelegate {
    private inner class Pending(val prompt: BasePrompt) {
        val result = GeckoResult<PromptResponse>()
        var ui: PendingRequest? = null
        var done = false
        init {
            pending.add(this)
            prompt.setDelegate(object : PromptInstanceDelegate {
                override fun onPromptDismiss(prompt: BasePrompt) { finish() }
            })
        }
        fun finish(response: (() -> PromptResponse)? = null) {
            if (done) return
            done = true
            pending.remove(this)
            ui?.cancel()
            result.complete(if (prompt.isComplete) null else response?.invoke() ?: prompt.dismiss())
        }
    }
    private val pending = mutableSetOf<Pending>()
    private var closed = false
    private val uploads = File(activity.cacheDir, "gecko-uploads/${UUID.randomUUID()}")

    private fun show(prompt: BasePrompt, spec: PromptRequest, confirm: (PromptAnswer) -> PromptResponse): GeckoResult<PromptResponse> {
        val request = Pending(prompt)
        if (closed) { request.finish(); return request.result }
        request.ui = requests.showPrompt(spec) { answer ->
            request.finish(if (answer == null) null else { { confirm(answer) } })
        }
        return request.result
    }

    override fun onAlertPrompt(session: GeckoSession, prompt: AlertPrompt) = show(prompt,
        PromptRequest(PromptKind.ALERT, prompt.title, prompt.message)) { prompt.dismiss() }

    override fun onButtonPrompt(session: GeckoSession, prompt: ButtonPrompt) = show(prompt,
        PromptRequest(PromptKind.CONFIRM, prompt.title, prompt.message)) { prompt.confirm(ButtonPrompt.Type.POSITIVE) }

    override fun onTextPrompt(session: GeckoSession, prompt: TextPrompt) = show(prompt,
        PromptRequest(PromptKind.TEXT, prompt.title, prompt.message, prompt.defaultValue.orEmpty())) { prompt.confirm(it.text) }

    override fun onChoicePrompt(session: GeckoSession, prompt: ChoicePrompt): GeckoResult<PromptResponse> {
        val choices = mutableListOf<PromptChoice>()
        fun collect(items: Array<ChoicePrompt.Choice>) {
            items.forEach { choice ->
                if (!choice.disabled && !choice.separator) {
                    val children = choice.items
                    if (children != null) collect(children)
                    else choices.add(PromptChoice(choice.id, choice.label, choice.selected))
                }
            }
        }
        collect(prompt.choices)
        return show(prompt, PromptRequest(PromptKind.CHOICE, prompt.title, choices = choices,
            multiple = prompt.type == ChoicePrompt.Type.MULTIPLE)) { prompt.confirm(it.choices.toTypedArray()) }
    }

    override fun onFilePrompt(session: GeckoSession, prompt: FilePrompt): GeckoResult<PromptResponse> {
        val request = Pending(prompt)
        if (closed || prompt.type == FilePrompt.Type.FOLDER) {
            if (!closed) requests.message("Folder uploads are not available. Select individual files instead.")
            request.finish()
            return request.result
        }
        request.ui = requests.pickFiles(FileRequest(prompt.mimeTypes.orEmpty().toList(), prompt.type == FilePrompt.Type.MULTIPLE)) { uris ->
            if (uris == null) request.finish() else copyFiles(request, prompt, uris)
        }
        return request.result
    }

    private fun copyFiles(request: Pending, prompt: FilePrompt, uris: List<Uri>) {
        worker.execute {
            val copied = runCatching {
                uris.map { uri ->
                    val name = activity.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
                        if (it.moveToFirst()) it.getString(0) else null
                    } ?: "attachment"
                    val directory = File(uploads, UUID.randomUUID().toString()).apply { mkdirs() }
                    val target = File(directory, FileNames.safe(name))
                    (activity.contentResolver.openInputStream(uri) ?: throw IOException("Cannot read file")).use { input ->
                        target.outputStream().use { output -> input.copyTo(output) }
                    }
                    Uri.fromFile(target)
                }.toTypedArray()
            }
            activity.runOnUiThread {
                if (closed || request.done) {
                    if (closed) worker.execute { uploads.deleteRecursively() }
                } else copied.fold(
                    onSuccess = { files -> request.finish { prompt.confirm(activity.applicationContext, files) } },
                    onFailure = { request.finish(); requests.message("The selected files could not be read. Try again.") },
                )
            }
        }
    }

    fun close() {
        closed = true
        pending.toList().forEach { it.finish() }
        // The session is closed first, so no upload can still read these files.
        worker.execute { uploads.deleteRecursively() }
    }
    companion object { private val worker = Executors.newSingleThreadExecutor() }
}
