package com.twidi.twicc

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.Context
import android.content.ComponentName
import android.content.ServiceConnection
import android.os.IBinder
import com.twidi.twicc.tts.*
import java.util.UUID
import android.graphics.Color
import android.os.Bundle
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ImageButton
import android.widget.ImageView
import android.util.TypedValue
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.ProgressBar
import android.widget.Toast
import com.twidi.twicc.browser.BrowserEvents
import com.twidi.twicc.browser.BrowserSession
import com.twidi.twicc.browser.EngineProvider
import com.twidi.twicc.platform.BrowserDownloads
import com.twidi.twicc.platform.DownloadSource
import com.twidi.twicc.platform.ExternalLinks
import com.twidi.twicc.platform.NativeRequests

/** Remote web client with a small, origin-scoped app command bridge. */
class BrowserActivity : Activity() {
    private lateinit var instance: Instance
    private lateinit var store: InstanceStore
    private lateinit var root: FrameLayout
    private lateinit var menuButton: ImageButton
    private lateinit var progress: ProgressBar
    private lateinit var errorPanel: LinearLayout
    private var browser: BrowserSession? = null
    private var requests: NativeRequests? = null
    private var canGoBack = false
    private var failed = false
    private var safeUrl = ""
    private lateinit var appBridge: AndroidAppBridge
    private val menuLease = AppMenuLease()
    private var documentGeneration = 0
    private val expireMenu = Runnable { updateNativeMenu() }
    private var ttsBinder: TtsService.LocalBinder? = null
    private var ttsBound = false
    private var ttsSource: TtsActivationSource? = null
    private lateinit var ttsDocument: DocumentOwner
    private val ttsConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            ttsBinder = binder as TtsService.LocalBinder
            attachNativeTtsDocument()
        }
        override fun onServiceDisconnected(name: ComponentName) { ttsBinder = null }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = InstanceStore(this)
        instance = store.find(intent.getStringExtra("instance")) ?: run { finish(); return }
        safeUrl = savedInstanceState?.getString("url")
            ?.takeIf { instance.url.allowsNavigation(it) }
            ?: store.page(instance)
            ?: "${instance.address}/"
        buildLayout()
        val ttsBridge = com.twidi.twicc.tts.NativeTtsBridge(
            { BuildConfig.NATIVE_TTS && browser?.nativeTtsSupported == true },
            ::nativeTtsDocumentOwner, ::nativeTtsSnapshot, { browser?.nativeTtsDocumentToken }, ::dispatchNativeTts)
        appBridge = AndroidAppBridge(::appState, ::reportWebMenu, ::queueAppAction, ttsBridge::receive)
        ttsDocument = DocumentOwner(instance.id, UUID.randomUUID().toString(), TtsService.nextDocumentGeneration())
        openBrowser()
        ttsBound = bindService(Intent(this, TtsService::class.java), ttsConnection, Context.BIND_AUTO_CREATE)
    }

    private fun buildLayout() {
        root = FrameLayout(this)
        fitSystemInsets(root)
        setContentView(root)
        errorPanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(24), dp(24), dp(24), dp(24))
            visibility = View.GONE
            isClickable = true
            setBackgroundColor(if (resources.configuration.isNightModeActiveCompat()) Color.rgb(24, 24, 27) else Color.rgb(250, 250, 252))
        }
        root.addView(errorPanel, FrameLayout.LayoutParams(-1, -1))
        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
            visibility = View.GONE
        }
        root.addView(progress, FrameLayout.LayoutParams(-1, dp(3), Gravity.TOP))
        menuButton = ImageButton(this).apply {
            setImageResource(R.drawable.twicc_robot)
            scaleType = ImageView.ScaleType.FIT_CENTER
            setPadding(dp(11), dp(11), dp(11), dp(11))
            val ripple = TypedValue()
            theme.resolveAttribute(android.R.attr.selectableItemBackgroundBorderless, ripple, true)
            setBackgroundResource(ripple.resourceId)
            contentDescription = "Android app menu for ${instance.name}"
            setOnClickListener { showMenu() }
        }
        root.addView(menuButton, FrameLayout.LayoutParams(dp(48), dp(48), Gravity.TOP or Gravity.LEFT).apply {
            leftMargin = dp(3)
            topMargin = dp(3)
        })
    }

    private fun openBrowser(force: Boolean = false) {
        disposeBrowser()
        failed = false
        canGoBack = false
        errorPanel.visibility = View.GONE
        try {
            val nativeRequests = NativeRequests(this)
            requests = nativeRequests
            val page = EngineProvider.engine.createSession(this, instance, events(), nativeRequests)
            browser = page
            root.addView(page.view, 0, FrameLayout.LayoutParams(-1, -1))
            page.load(safeUrl, force)
        } catch (_: RuntimeException) {
            disposeBrowser()
            showError("${EngineProvider.engine.displayName} could not start. Check the engine installation, then reopen TwiCC.")
        }
    }

    private fun events() = object : BrowserEvents {
        override fun onNavigationRequest(request: NavigationRequest): NavigationDecision {
            val decision = NavigationPolicy.decide(instance.url, request)
            if (decision == NavigationDecision.OPEN_EXTERNAL) openExternal(request.url)
            if (decision == NavigationDecision.BLOCK && (!request.newWindow || request.userGesture))
                toast("This link type is not supported.")
            return decision
        }
        override fun onPageStarted(url: String) {
            resetWebMenu()
            failed = false
            errorPanel.visibility = View.GONE
            progress.progress = 0
            progress.visibility = View.VISIBLE
        }
        override fun onLocationChanged(url: String) {
            if (instance.url.allowsNavigation(url)) {
                safeUrl = url
                store.rememberPage(instance, url)
            }
        }
        override fun onProgressChanged(progress: Int) {
            this@BrowserActivity.progress.progress = progress
            this@BrowserActivity.progress.visibility = if (progress < 100 && !failed) View.VISIBLE else View.GONE
        }
        override fun onPageFinished() { progress.visibility = View.GONE }
        override fun onHistoryChanged(canGoBack: Boolean) { this@BrowserActivity.canGoBack = canGoBack }
        override fun onFailure(message: String) { showError(message) }
        override fun onRendererStopped() {
            disposeBrowser()
            showError("Android stopped the browser engine. Reload to reconnect to TwiCC.")
        }
        override fun onAppMessage(message: String): String = appBridge.receive(message)
        override fun onDownload(source: DownloadSource) { BrowserDownloads.save(this@BrowserActivity, source) }
    }

    private fun refresh(force: Boolean) {
        if (force || browser == null) { openBrowser(force); return }
        failed = false
        errorPanel.visibility = View.GONE
        browser?.load(safeUrl)
    }

    private fun showError(message: String, canRetry: Boolean = true) {
        failed = true
        resetWebMenu()
        progress.visibility = View.GONE
        errorPanel.removeAllViews()
        errorPanel.addView(label(message))
        if (canRetry) errorPanel.addView(Button(this).apply {
            text = "Retry"
            setOnClickListener { refresh(false) }
        })
        errorPanel.addView(Button(this).apply {
            text = "Manage instances"
            setOnClickListener { manageInstances() }
        })
        errorPanel.visibility = View.VISIBLE
    }

    private fun showMenu() {
        PopupMenu(this, menuButton).apply {
            menu.add("TwiCC ${BuildConfig.VERSION_NAME} · ${EngineProvider.engine.displayName}").isEnabled = false
            store.all().forEach { saved ->
                menu.add(saved.name).apply {
                    isCheckable = true
                    isChecked = saved.id == instance.id
                    setOnMenuItemClickListener { switchInstance(saved); true }
                }
            }
            menu.add("Manage instances").setOnMenuItemClickListener { manageInstances(); true }
            menu.add("Reload page").setOnMenuItemClickListener { refresh(false); true }
            menu.add("Force refresh (keep login)").setOnMenuItemClickListener {
                AlertDialog.Builder(this@BrowserActivity)
                    .setTitle("Force refresh?")
                    .setMessage("Reload this page and bypass its resource cache. Your login and preferences stay saved. Unsaved text may be lost.")
                    .setNegativeButton("Cancel", null)
                    .setPositiveButton("Refresh") { _, _ -> refresh(true) }.show()
                true
            }
            menu.add("Server home").setOnMenuItemClickListener {
                safeUrl = "${instance.address}/"
                refresh(false)
                true
            }
            menu.add("Open in browser").setOnMenuItemClickListener { openExternal(safeUrl); true }
            show()
        }
    }

    private fun appState(): JSONObject = JSONObject()
        .put("version", BuildConfig.VERSION_NAME)
        .put("engine", EngineProvider.engine.displayName)
        .put("activeInstanceId", instance.id)
        .put("capabilities", JSONObject().apply {
            if (BuildConfig.NATIVE_TTS && browser?.nativeTtsSupported == true) put("nativeTts", 1)
        })
        .put("instances", JSONArray().apply {
            store.all().forEach { saved -> put(JSONObject()
                .put("id", saved.id).put("name", saved.name).put("address", saved.address)) }
        })

    private fun reportWebMenu(owner: String, visible: Boolean) {
        menuLease.report(owner, visible && !failed, SystemClock.uptimeMillis())
        updateNativeMenu()
        root.removeCallbacks(expireMenu)
        val remaining = menuLease.remainingMs(SystemClock.uptimeMillis())
        if (remaining > 0) root.postDelayed(expireMenu, remaining)
    }

    private fun updateNativeMenu() {
        menuButton.visibility = if (!failed && menuLease.isVisible(SystemClock.uptimeMillis())) View.GONE else View.VISIBLE
    }

    private fun resetWebMenu() {
        documentGeneration++
        if (::instance.isInitialized) {
            ttsDocument = DocumentOwner(instance.id, UUID.randomUUID().toString(), TtsService.nextDocumentGeneration())
            attachNativeTtsDocument()
        }
        menuLease.reset()
        if (::root.isInitialized) root.removeCallbacks(expireMenu)
        if (::menuButton.isInitialized) updateNativeMenu()
    }

    private fun queueAppAction(command: AppCommand, instanceId: String?): String? {
        val target = if (command == AppCommand.SWITCH_INSTANCE)
            store.find(instanceId) ?: return "This instance is no longer saved." else null
        val source = browser ?: return "The browser session is closed."
        val generation = documentGeneration
        // Return the transport reply before a navigation can destroy its document.
        root.post {
            if (browser !== source || documentGeneration != generation || isFinishing) return@post
            when (command) {
                AppCommand.SWITCH_INSTANCE -> target?.let { switchInstance(it) }
                AppCommand.MANAGE_INSTANCES -> manageInstances()
                AppCommand.RELOAD -> refresh(false)
                AppCommand.FORCE_REFRESH -> refresh(true)
                AppCommand.SERVER_HOME -> { safeUrl = "${instance.address}/"; refresh(false) }
                AppCommand.OPEN_IN_BROWSER -> openExternal(safeUrl)
                else -> Unit
            }
        }
        return null
    }

    private fun switchInstance(target: Instance) {
        if (target.id == instance.id) return
        TtsService.stopForInstance(instance.id)
        store.rememberPage(instance, safeUrl)
        store.select(target)
        disposeBrowser()
        startActivity(Intent(this, BrowserActivity::class.java).putExtra("instance", target.id))
        finish()
    }

    private fun manageInstances() {
        disposeBrowser()
        startActivity(Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            .putExtra("manage", true))
        finish()
    }

    private fun openExternal(url: String) = ExternalLinks.open(this, url)

    @Deprecated("Legacy back handling is enabled in the manifest for this Activity.")
    override fun onBackPressed() {
        if (browser != null && !failed && canGoBack) browser?.goBack() else manageInstances()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString("url", safeUrl)
        super.onSaveInstanceState(outState)
    }

    override fun onPause() {
        if (::instance.isInitialized) store.rememberPage(instance, safeUrl)
        super.onPause()
    }

    override fun onStart() {
        super.onStart()
        browser?.setVisible(true)
    }

    override fun onStop() {
        browser?.setVisible(false)
        super.onStop()
    }

    @Deprecated("The platform file picker still uses this Activity result API.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        requests?.onActivityResult(requestCode, resultCode, data)
    }

    private fun disposeBrowser() {
        resetWebMenu()
        val page = browser
        browser = null
        (page?.view?.parent as? ViewGroup)?.removeView(page.view)
        page?.close()
        requests?.close()
        requests = null
        canGoBack = false
    }

    override fun onDestroy() {
        ttsSource?.let { ttsBinder?.detachSource(it) }
        if (ttsBound) { unbindService(ttsConnection); ttsBound = false }
        ttsBinder = null
        disposeBrowser()
        super.onDestroy()
    }

    private fun attachNativeTtsDocument() {
        val binder = ttsBinder ?: return
        val page = browser ?: return
        val source = TtsActivationSource(instance.id, instance.name, instance.address, page::exportTtsCookies)
        if (binder.configure(source)) {
            ttsSource = source
            binder.dispatch(TtsCommand.AttachDocument(ttsDocument))
        }
    }

    /** Task 8 uses these methods after validating the current top-level bridge request. */
    fun nativeTtsDocumentOwner(): DocumentOwner = ttsDocument
    fun nativeTtsSnapshot(): NativeTtsSnapshot = ttsBinder?.snapshot() ?: TtsService.cachedSnapshot(instance.id)
    fun dispatchNativeTts(command: TtsCommand): Acceptance {
        if (command is TtsCommand.Manual || command is TtsCommand.Listen) {
            TtsNotification.visiblePreflight(this, command is TtsCommand.Listen)?.let { return Acceptance(false, error = it) }
            attachNativeTtsDocument()
        }
        return ttsBinder?.dispatch(command) ?: Acceptance(false,
            error = TtsActionError("initializing", "Native speech is initializing. Try again."))
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

}

private fun android.content.res.Configuration.isNightModeActiveCompat(): Boolean =
    uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK == android.content.res.Configuration.UI_MODE_NIGHT_YES
