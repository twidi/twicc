package com.twidi.twicc.platform

import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.webkit.MimeTypeMap
import android.widget.EditText
import android.widget.Toast

fun interface PendingRequest { fun cancel() }
data class FileRequest(val mimeTypes: List<String>, val multiple: Boolean)
enum class PromptKind { ALERT, CONFIRM, TEXT, CHOICE }
data class PromptChoice(val id: String, val label: String, val selected: Boolean)
data class PromptRequest(
    val kind: PromptKind,
    val title: String? = null,
    val message: String? = null,
    val text: String = "",
    val choices: List<PromptChoice> = emptyList(),
    val multiple: Boolean = false,
)
data class PromptAnswer(val text: String = "", val choices: List<String> = emptyList())

/** Activity-owned UI. Cancellation and late picker results complete each callback at most once. */
class NativeRequests(private val activity: Activity) {
    private val pending = mutableSetOf<PendingRequest>()
    private var closed = false
    private var file: PendingRequest? = null
    private var pickerCode = -1
    private var receiveFiles: ((List<Uri>?) -> Unit)? = null

    private inner class Completion<T>(private val callback: (T?) -> Unit) : PendingRequest {
        private var done = false
        var dialog: AlertDialog? = null
        init { pending.add(this) }
        fun finish(value: T?) {
            if (done) return
            done = true
            pending.remove(this)
            if (file === this) { file = null; receiveFiles = null }
            dialog?.dismiss()
            callback(value)
        }
        override fun cancel() = finish(null)
    }

    fun pickFiles(spec: FileRequest, callback: (List<Uri>?) -> Unit): PendingRequest {
        file?.cancel()
        val request = Completion(callback)
        if (closed) { request.cancel(); return request }
        file = request
        pickerCode = nextPickerCode++
        receiveFiles = { uris -> request.finish(if (spec.multiple) uris else uris?.take(1)) }
        val types = spec.mimeTypes.flatMap { it.split(',') }.mapNotNull { raw ->
            val value = raw.trim()
            if (value.startsWith('.')) MimeTypeMap.getSingleton().getMimeTypeFromExtension(value.drop(1).lowercase())
            else value.takeIf { it.contains('/') }
        }.distinct()
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType(types.singleOrNull() ?: "*/*")
            .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, spec.multiple)
        if (types.size > 1) intent.putExtra(Intent.EXTRA_MIME_TYPES, types.toTypedArray())
        try {
            @Suppress("DEPRECATION")
            activity.startActivityForResult(intent, pickerCode)
        } catch (_: ActivityNotFoundException) {
            request.cancel()
            message("No file picker is available.")
        }
        return request
    }

    fun showPrompt(spec: PromptRequest, callback: (PromptAnswer?) -> Unit): PendingRequest {
        val request = Completion(callback)
        if (closed) { request.cancel(); return request }
        val builder = AlertDialog.Builder(activity).setTitle(spec.title).setMessage(spec.message)
        when (spec.kind) {
            PromptKind.ALERT -> builder.setPositiveButton("OK") { _, _ -> request.finish(PromptAnswer()) }
            PromptKind.CONFIRM -> builder.setPositiveButton("OK") { _, _ -> request.finish(PromptAnswer()) }
                .setNegativeButton("Cancel") { _, _ -> request.cancel() }
            PromptKind.TEXT -> {
                val input = EditText(activity).apply { setText(spec.text) }
                builder.setView(input).setPositiveButton("OK") { _, _ -> request.finish(PromptAnswer(text = input.text.toString())) }
                    .setNegativeButton("Cancel") { _, _ -> request.cancel() }
            }
            PromptKind.CHOICE -> {
                val labels = spec.choices.map { it.label }.toTypedArray()
                builder.setNegativeButton("Cancel") { _, _ -> request.cancel() }
                if (spec.multiple) {
                    val checked = spec.choices.map { it.selected }.toBooleanArray()
                    builder.setMultiChoiceItems(labels, checked) { _, index, selected -> checked[index] = selected }
                        .setPositiveButton("OK") { _, _ -> request.finish(PromptAnswer(choices =
                            spec.choices.filterIndexed { index, _ -> checked[index] }.map { it.id })) }
                } else builder.setSingleChoiceItems(labels, spec.choices.indexOfFirst { it.selected }) { _, index ->
                    request.finish(PromptAnswer(choices = listOf(spec.choices[index].id)))
                }
            }
        }
        request.dialog = builder.setOnCancelListener { request.cancel() }.create().also { dialog ->
            dialog.setOnDismissListener { request.cancel() }
            dialog.show()
        }
        return request
    }

    fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode != pickerCode) return
        val uris = if (resultCode != Activity.RESULT_OK || data == null) null else
            data.clipData?.let { clip -> (0 until clip.itemCount).map { clip.getItemAt(it).uri } }
                ?: listOfNotNull(data.data).takeIf { it.isNotEmpty() }
        receiveFiles?.invoke(uris)
    }

    fun close() {
        closed = true
        pending.toList().forEach { it.cancel() }
    }

    fun message(text: String) = Toast.makeText(activity, text, Toast.LENGTH_LONG).show()
    companion object { private var nextPickerCode = 1000 }
}
