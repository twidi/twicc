package com.twidi.twicc

import com.twidi.twicc.browser.EngineProvider
import com.twidi.twicc.tts.TtsService
import com.twidi.twicc.tts.TtsNotification
import android.app.Activity
import android.app.Dialog
import android.content.Intent
import android.os.Bundle

class MainActivity : Activity() {
    private lateinit var store: InstanceStore
    private lateinit var ui: InstanceManagerUi

    // The open sheet, if any. Kept in plain fields and saved in the instance state: a system theme
    // change recreates this Activity (uiMode is deliberately not in configChanges) and the sheet,
    // with its draft or its pending confirmation, comes back.
    private var sheet: Dialog? = null
    private var pending: Pending? = null

    private sealed class Pending {
        class Form(val previousId: String?, val draft: InstanceManagerUi.FormDraft) : Pending()
        class Remove(val instanceId: String) : Pending()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = InstanceStore(this)
        deleteUnusedProfiles()
        ui = InstanceManagerUi(this).apply { onAdd = { edit(null) } }
        fitSystemInsets(ui.root)
        setContentView(ui.root)
        render()
        restorePending(savedInstanceState)
        // A recreation (theme change, rotation) must not open an instance again.
        if (savedInstanceState != null) return
        if (routeSpeechNotification(intent)) return
        if (!intent.getBooleanExtra("manage", false)) {
            store.last()?.let { open(it) }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        routeSpeechNotification(intent)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        when (val state = pending) {
            is Pending.Form -> {
                outState.putString(STATE_SHEET, "form")
                outState.putString(STATE_INSTANCE, state.previousId)
                outState.putString(STATE_NAME, state.draft.name)
                outState.putString(STATE_ADDRESS, state.draft.address)
                outState.putString(STATE_ERROR, state.draft.error)
            }
            is Pending.Remove -> {
                outState.putString(STATE_SHEET, "remove")
                outState.putString(STATE_INSTANCE, state.instanceId)
            }
            null -> Unit
        }
    }

    override fun onDestroy() {
        // Dismiss without clearing `pending`: the sheet is rebuilt from it after a recreation.
        sheet?.dismiss()
        sheet = null
        super.onDestroy()
    }

    private fun restorePending(state: Bundle?) {
        if (state == null) return
        val instance = store.find(state.getString(STATE_INSTANCE))
        when (state.getString(STATE_SHEET)) {
            "form" -> {
                // An edit whose instance disappeared meanwhile is dropped.
                if (state.getString(STATE_INSTANCE) != null && instance == null) return
                showForm(instance, InstanceManagerUi.FormDraft(
                    state.getString(STATE_NAME).orEmpty(),
                    state.getString(STATE_ADDRESS).orEmpty(),
                    state.getString(STATE_ERROR),
                ))
            }
            "remove" -> instance?.let { confirmRemove(it) }
        }
    }

    private fun routeSpeechNotification(intent: Intent): Boolean {
        if (intent.action != TtsNotification.ACTION_REOPEN) return false
        val id = intent.getStringExtra(TtsNotification.EXTRA_INSTANCE) ?: return true
        if (TtsService.isCurrentSession(intent.getStringExtra(TtsNotification.EXTRA_EPOCH), id)) store.find(id)?.let { open(it) }
        return true
    }

    override fun onResume() {
        super.onResume()
        render()
    }

    private fun render() {
        ui.render(store.all(), store.last()?.id, ::open, ::edit, ::confirmRemove)
    }

    private fun open(instance: Instance) {
        store.last()?.takeIf { it.id != instance.id }?.let { TtsService.stopForInstance(it.id) }
        store.select(instance)
        startActivity(Intent(this, BrowserActivity::class.java).putExtra("instance", instance.id))
    }

    private fun edit(previous: Instance?) {
        showForm(previous, InstanceManagerUi.FormDraft(previous?.name.orEmpty(), previous?.address.orEmpty(), null))
    }

    private fun showForm(previous: Instance?, draft: InstanceManagerUi.FormDraft) {
        pending = Pending.Form(previous?.id, draft)
        sheet = ui.showForm(
            isEdit = previous != null,
            draft = draft,
            onSave = { name, address ->
                try {
                    val saved = store.save(previous, name, address)
                    if (previous != null && saved.id != previous.id) {
                        TtsService.stopForInstance(previous.id)
                        store.deferProfileDeletion(previous)
                        deleteUnusedProfiles()
                    }
                    pending = null
                    render()
                    null
                } catch (error: IllegalArgumentException) {
                    error.message ?: "Enter a valid HTTPS address."
                }
            },
            onCancel = { pending = null },
        )
    }

    private fun confirmRemove(instance: Instance) {
        pending = Pending.Remove(instance.id)
        sheet = ui.showRemove(
            instance,
            onConfirm = {
                TtsService.stopForInstance(instance.id)
                store.remove(instance)
                store.deferProfileDeletion(instance)
                deleteUnusedProfiles()
                pending = null
                render()
            },
            onCancel = { pending = null },
        )
    }

    private fun deleteUnusedProfiles() {
        try {
            store.pendingProfileDeletions(BuildConfig.FLAVOR).forEach { name ->
                // The browser session is closed before returning to the instance manager.
                if (EngineProvider.engine.requestStorageDeletion(this, name)) store.profileDeleted(name, BuildConfig.FLAVOR)
            }
        } catch (_: RuntimeException) {
            // An engine startup failure must not prevent editing saved addresses.
        }
    }

    private companion object {
        const val STATE_SHEET = "sheet"
        const val STATE_INSTANCE = "sheetInstance"
        const val STATE_NAME = "formName"
        const val STATE_ADDRESS = "formAddress"
        const val STATE_ERROR = "formError"
    }
}
