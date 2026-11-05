package com.twidi.twicc.browser

import android.app.Activity
import android.content.Context
import android.view.View
import com.twidi.twicc.Instance
import com.twidi.twicc.platform.NativeRequests
import com.twidi.twicc.tts.TtsCookie

/** One implementation is selected at build time by EngineProvider in the flavor source set. */
interface BrowserEngine {
    val displayName: String
    fun createSession(activity: Activity, instance: Instance, events: BrowserEvents, requests: NativeRequests): BrowserSession
    /** True means submitted/already absent; false means retry on the next process start. */
    fun requestStorageDeletion(context: Context, storageId: String): Boolean
}

interface BrowserSession {
    val nativeTtsSupported: Boolean get() = false
    val view: View
    fun load(url: String, bypassCache: Boolean = false)
    fun goBack()
    fun setVisible(visible: Boolean)
    /** Export a detached credential snapshot. Accepted exports survive close; credentials never reach page JS. */
    fun exportTtsCookies(urls: List<String>, callback: (Result<List<TtsCookie>>) -> Unit)
    /** Close cancels view callbacks and releases the view, without clearing persistent storage. */
    fun close()
}
