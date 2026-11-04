package com.twicc.android

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView

class MainActivity : Activity() {
    private lateinit var store: InstanceStore
    private lateinit var list: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = InstanceStore(this)
        deleteUnusedProfiles()
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        fitSystemInsets(root)
        root.addView(label("TwiCC instances", 24f))
        root.addView(label("Connect to your existing TwiCC server through an HTTPS tunnel."))
        root.addView(Button(this).apply {
            text = "Add instance"
            setOnClickListener { edit(null) }
        })
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(ScrollView(this).apply { addView(list) }, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        if (savedInstanceState == null && !intent.getBooleanExtra("manage", false)) {
            store.last()?.let { open(it) }
        }
    }

    override fun onResume() {
        super.onResume()
        render()
    }

    private fun render() {
        list.removeAllViews()
        val instances = store.all()
        if (instances.isEmpty()) list.addView(label("No instances yet. Add your server address to get started."))
        instances.forEach { instance ->
            list.addView(LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(12), dp(8), dp(12), dp(8))
                addView(Button(this@MainActivity).apply {
                    text = "${instance.name}\n${instance.address}"
                    isAllCaps = false
                    contentDescription = "Open ${instance.name}, ${instance.address}"
                    setOnClickListener { open(instance) }
                })
                addView(LinearLayout(this@MainActivity).apply {
                    addView(Button(this@MainActivity).apply {
                        text = "Edit"
                        setOnClickListener { edit(instance) }
                    }, LinearLayout.LayoutParams(0, -2, 1f))
                    addView(Button(this@MainActivity).apply {
                        text = "Remove"
                        setOnClickListener { confirmRemove(instance) }
                    }, LinearLayout.LayoutParams(0, -2, 1f))
                })
            })
        }
    }

    private fun open(instance: Instance) {
        store.select(instance)
        startActivity(Intent(this, BrowserActivity::class.java).putExtra("instance", instance.id))
    }

    private fun edit(previous: Instance?) {
        val form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
        }
        val name = EditText(this).apply {
            hint = "Name (optional)"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            setSingleLine()
            setText(previous?.name.orEmpty())
        }
        val address = EditText(this).apply {
            hint = "https://twicc.example.com"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setSingleLine()
            setText(previous?.address.orEmpty())
        }
        form.addView(name)
        form.addView(address)
        val dialog = AlertDialog.Builder(this)
            .setTitle(if (previous == null) "Add instance" else "Edit instance")
            .setView(form)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Save", null)
            .create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                try {
                    val saved = store.save(previous, name.text.toString(), address.text.toString())
                    if (previous != null && saved.id != previous.id) {
                        store.deferProfileDeletion(previous)
                        deleteUnusedProfiles()
                    }
                    render()
                    dialog.dismiss()
                } catch (error: IllegalArgumentException) {
                    address.error = error.message
                    address.requestFocus()
                }
            }
        }
        dialog.show()
    }

    private fun confirmRemove(instance: Instance) {
        AlertDialog.Builder(this)
            .setTitle("Remove ${instance.name}?")
            .setMessage("Remove this address and request deletion of its Gecko login and Web storage. The server is unchanged.")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Remove") { _, _ ->
                store.remove(instance)
                store.deferProfileDeletion(instance)
                deleteUnusedProfiles()
                render()
            }.show()
    }

    private fun deleteUnusedProfiles() {
        try {
            store.pendingProfileDeletions().forEach { name ->
                // The browser session is closed before returning to the instance manager.
                runCatching { BrowserEngine.get(this).storageController.clearDataForSessionContext(name) }
                    .onSuccess { store.profileDeleted(name) }
            }
        } catch (_: RuntimeException) {
            // An engine startup failure must not prevent editing saved addresses.
        }
    }
}
