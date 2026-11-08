package com.twidi.twicc.i18n

import android.content.Context
import android.content.SharedPreferences
import android.util.Log

/** One installation preference, independent of instances, browser profiles, and the system locale. */
class AndroidLanguageStore(context: Context) {
    private val owner: Owner

    init {
        val preferences = context.getSharedPreferences("app-language", Context.MODE_PRIVATE)
        owner = synchronized(lock) { owners.getOrPut(preferences) { Owner(preferences) } }
    }

    fun read(): AppLanguage = synchronized(lock) { owner.state }

    fun select(locale: String, expectedRevision: Long): LanguageSelection = synchronized(lock) {
        val selection = owner.state.select(locale, expectedRevision)
        if (!selection.accepted || selection.state == owner.state) return@synchronized selection
        val persisted = try {
            owner.preferences.edit()
                .putString("locale", selection.state.locale)
                .putLong("revision", selection.state.revision)
                .commit()
        } catch (error: RuntimeException) {
            Log.w("AndroidLanguageStore", "Cannot persist installation language", error)
            false
        }
        if (!persisted) {
            // SharedPreferences can change its memory cache on commit failure. Keep our accepted
            // snapshot authoritative for every store instance, including NativeMessages reads.
            return@synchronized LanguageSelection(owner.state, false, UiMessage("android.language.errors.saveFailed"))
        }
        owner.state = selection.state
        owner.pending.addLast(Change(selection.state, owner.observers.toList()))
        owner.notifyObservers()
        selection
    }

    /** Observe future accepted changes on the selecting thread. Close removes the listener.
     * No-op selections and rejected selections do not emit. Call read() for the initial state.
     */
    fun observe(listener: (AppLanguage) -> Unit): AutoCloseable = synchronized(lock) {
        val token = Any()
        owner.observers[token] = listener
        AutoCloseable { synchronized(lock) { owner.observers.remove(token) } }
    }

    private class Change(val state: AppLanguage, val observers: List<Pair<Any, (AppLanguage) -> Unit>>)

    private class Owner(val preferences: SharedPreferences) {
        private val stored = preferences.all
        var state = AppLanguage.fromStored(stored["locale"], stored["revision"])
        val observers = linkedMapOf<Any, (AppLanguage) -> Unit>()
        val pending = ArrayDeque<Change>()
        private var notifying = false

        fun notifyObservers() {
            if (notifying) return
            notifying = true
            try {
                // Queue reentrant selections so every observer receives revisions in order.
                while (pending.isNotEmpty()) {
                    val change = pending.removeFirst()
                    for ((token, listener) in change.observers) {
                        if (token !in observers) continue
                        try {
                            listener(change.state)
                        } catch (error: Exception) {
                            Log.w("AndroidLanguageStore", "Language observer failed", error)
                        }
                    }
                }
            } finally {
                notifying = false
            }
        }
    }

    companion object {
        // Android caches a SharedPreferences instance per file in the application process.
        // Share the lock and accepted snapshot across activity and headless service wrappers.
        private val lock = Any()
        private val owners = mutableMapOf<SharedPreferences, Owner>()
    }
}
