package com.twicc.android

import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import android.webkit.MimeTypeMap
import android.widget.EditText
import android.widget.Toast
import org.mozilla.geckoview.GeckoResult
import org.mozilla.geckoview.GeckoSession
import org.mozilla.geckoview.GeckoSession.PromptDelegate
import org.mozilla.geckoview.GeckoSession.PromptDelegate.*
import java.io.File
import java.io.IOException
import java.util.UUID
import java.util.concurrent.Executors

/** Native UI for the prompts the remote application needs. No camera or microphone access. */
class BrowserPrompts(private val activity: Activity) : PromptDelegate {
    private class Pending(val prompt: BasePrompt) {
        val result = GeckoResult<PromptResponse>()
        var dialog: AlertDialog? = null
        var done = false
    }
    private val pending = mutableSetOf<Pending>()
    private var file: Pending? = null
    private var pickerCode = -1
    private var closed = false
    private val uploads = File(activity.cacheDir, "gecko-uploads/${UUID.randomUUID()}")

    private fun begin(prompt: BasePrompt): Pending = Pending(prompt).also { request ->
        pending.add(request)
        prompt.setDelegate(object : PromptInstanceDelegate {
            override fun onPromptDismiss(prompt: BasePrompt) { finish(request) }
        })
    }

    private fun finish(request: Pending, response: (() -> PromptResponse)? = null) {
        if (request.done) return
        request.done = true
        pending.remove(request)
        if (file === request) file = null
        request.result.complete(if (request.prompt.isComplete) null else response?.invoke() ?: request.prompt.dismiss())
        request.dialog?.dismiss()
    }

    private fun show(request: Pending, builder: AlertDialog.Builder): GeckoResult<PromptResponse> {
        request.dialog = builder.setOnCancelListener { finish(request) }.create().also {
            it.setOnDismissListener { finish(request) }
            it.show()
        }
        return request.result
    }

    override fun onAlertPrompt(session: GeckoSession, prompt: AlertPrompt): GeckoResult<PromptResponse> {
        val request = begin(prompt)
        return show(request, AlertDialog.Builder(activity).setTitle(prompt.title).setMessage(prompt.message)
            .setPositiveButton("OK") { _, _ -> finish(request) })
    }

    override fun onButtonPrompt(session: GeckoSession, prompt: ButtonPrompt): GeckoResult<PromptResponse> {
        val request = begin(prompt)
        return show(request, AlertDialog.Builder(activity).setTitle(prompt.title).setMessage(prompt.message)
            .setPositiveButton("OK") { _, _ -> finish(request) { prompt.confirm(ButtonPrompt.Type.POSITIVE) } }
            .setNegativeButton("Cancel") { _, _ -> finish(request) { prompt.confirm(ButtonPrompt.Type.NEGATIVE) } })
    }

    override fun onTextPrompt(session: GeckoSession, prompt: TextPrompt): GeckoResult<PromptResponse> {
        val request = begin(prompt)
        val input = EditText(activity).apply { setText(prompt.defaultValue) }
        return show(request, AlertDialog.Builder(activity).setTitle(prompt.title).setMessage(prompt.message).setView(input)
            .setPositiveButton("OK") { _, _ -> finish(request) { prompt.confirm(input.text.toString()) } }
            .setNegativeButton("Cancel") { _, _ -> finish(request) })
    }

    override fun onChoicePrompt(session: GeckoSession, prompt: ChoicePrompt): GeckoResult<PromptResponse> {
        val request = begin(prompt)
        val choices = mutableListOf<ChoicePrompt.Choice>()
        fun collect(items: Array<ChoicePrompt.Choice>) {
            items.forEach { choice ->
                if (!choice.disabled && !choice.separator) {
                    val children = choice.items
                    if (children != null) collect(children) else choices.add(choice)
                }
            }
        }
        collect(prompt.choices)
        val labels = choices.map { it.label }.toTypedArray()
        val builder = AlertDialog.Builder(activity).setTitle(prompt.title).setNegativeButton("Cancel") { _, _ -> finish(request) }
        if (prompt.type == ChoicePrompt.Type.MULTIPLE) {
            val checked = choices.map { it.selected }.toBooleanArray()
            builder.setMultiChoiceItems(labels, checked) { _, which, selected -> checked[which] = selected }
                .setPositiveButton("OK") { _, _ -> finish(request) {
                    prompt.confirm(choices.filterIndexed { index, _ -> checked[index] }.map { it.id }.toTypedArray())
                } }
        } else {
            builder.setSingleChoiceItems(labels, choices.indexOfFirst { it.selected }) { _, which ->
                finish(request) { prompt.confirm(choices[which].id) }
            }
        }
        return show(request, builder)
    }

    override fun onFilePrompt(session: GeckoSession, prompt: FilePrompt): GeckoResult<PromptResponse> {
        file?.let { finish(it) }
        val request = begin(prompt)
        if (prompt.type == FilePrompt.Type.FOLDER) {
            toast("Folder uploads are not available. Select individual files instead.")
            finish(request)
            return request.result
        }
        file = request
        pickerCode = nextPickerCode++
        val types = prompt.mimeTypes.orEmpty().mapNotNull { value ->
            if (value.startsWith('.')) MimeTypeMap.getSingleton().getMimeTypeFromExtension(value.drop(1).lowercase())
            else value.takeIf { it.contains('/') }
        }.distinct()
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType(types.singleOrNull() ?: "*/*")
            .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, prompt.type == FilePrompt.Type.MULTIPLE)
        if (types.size > 1) intent.putExtra(Intent.EXTRA_MIME_TYPES, types.toTypedArray())
        try {
            @Suppress("DEPRECATION")
            activity.startActivityForResult(intent, pickerCode)
        } catch (_: ActivityNotFoundException) {
            finish(request)
            toast("No file picker is available.")
        }
        return request.result
    }

    fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode != pickerCode) return
        val request = file ?: return
        if (resultCode != Activity.RESULT_OK || data == null) { finish(request); return }
        val uris = data.clipData?.let { clip -> (0 until clip.itemCount).map { clip.getItemAt(it).uri } }
            ?: listOfNotNull(data.data)
        if (uris.isEmpty()) { finish(request); return }
        val selected = if ((request.prompt as FilePrompt).type == FilePrompt.Type.SINGLE) uris.take(1) else uris
        // Gecko's FilePrompt requires file paths. Document providers often expose only content URIs.
        // Copy via ContentResolver off the UI thread, preserving each selected file's display name.
        worker.execute {
            val copied = runCatching {
                selected.map { uri ->
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
                    onSuccess = { files -> finish(request) { request.prompt.confirm(activity.applicationContext, files) } },
                    onFailure = { finish(request); toast("The selected files could not be read. Try again.") },
                )
            }
        }
    }

    fun close() {
        closed = true
        pending.toList().forEach { finish(it) }
        // The owning GeckoSession is closed first. Do not delete a file while it can still upload.
        worker.execute { uploads.deleteRecursively() }
    }

    private fun toast(message: String) = Toast.makeText(activity, message, Toast.LENGTH_LONG).show()

    companion object {
        private val worker = Executors.newSingleThreadExecutor()
        private var nextPickerCode = 1000
    }
}
