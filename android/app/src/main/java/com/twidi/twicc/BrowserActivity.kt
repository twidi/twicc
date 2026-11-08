package com.twidi.twicc

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.Context
import android.content.ComponentName
import android.content.ServiceConnection
import android.os.IBinder
import android.os.Handler
import android.os.Looper
import java.util.concurrent.FutureTask
import com.twidi.twicc.i18n.AndroidLanguageStore
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
import com.twidi.twicc.push.AndroidPush

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
    private var foreground = false
    private var documentLoaded = false
    private var displayedSessionId: String? = null
    private val pushVisibilityOwner = Any()
    private var rendererRecovery = RendererRecovery()
    private var safeUrl = ""
    private lateinit var appBridge: AndroidAppBridge
    private lateinit var languageStore: AndroidLanguageStore
    private var languageObserver: AutoCloseable? = null
    private val ownerHandler = Handler(Looper.getMainLooper())
    private val menuLease = AppMenuLease()
    private val notificationNavigation = NotificationNavigation()
    private var documentGeneration = 0
    private val expireMenu = Runnable { updateNativeMenu() }
    private var ttsBinder: TtsService.LocalBinder? = null
    private var ttsBound = false
    private var ttsSource: TtsActivationSource? = null
    private lateinit var ttsDocument: DocumentOwner
    private var batteryAdviceDialog: AlertDialog? = null
    private var batteryAdviceCommand: TtsCommand.Listen? = null
    private var batteryAdviceConfirmed = false
    private val ttsConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            ttsBinder = binder as TtsService.LocalBinder
            attachNativeTtsDocument()
        }
        override fun onServiceDisconnected(name: ComponentName) { ttsBinder = null }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        rendererRecovery = RendererRecovery(savedInstanceState?.takeIf { it.containsKey("rendererRecoveryAt") }
            ?.getLong("rendererRecoveryAt"))
        store = InstanceStore(this)
        instance = store.find(intent.getStringExtra("instance")) ?: run { finish(); return }
        store.last()?.takeIf { it.id != instance.id }?.let { TtsService.stopForInstance(it.id) }
        store.select(instance)
        val session = notificationSession(intent)
        safeUrl = savedInstanceState?.getString("url")
            ?.takeIf { instance.url.allowsNavigation(it) }
            ?: store.page(instance)
            ?: session?.let { "${instance.address}${it.path}" }
            ?: "${instance.address}/"
        queueNotification(intent)
        buildLayout()
        val ttsBridge = com.twidi.twicc.tts.NativeTtsBridge(
            { browser?.nativeTtsSupported == true },
            ::nativeTtsDocumentOwner, ::nativeTtsSnapshot, { browser?.nativeTtsDocumentToken }, ::dispatchNativeTts)
        languageStore = AndroidLanguageStore(this)
        val languageBridge = AppLanguageBridge(languageStore::read, { browser?.nativeTtsDocumentToken }, languageStore::select)
        appBridge = AndroidAppBridge(::appState, ::reportWebMenu, ::queueAppAction, ttsBridge::receive,
            { command, request -> AndroidPush.receive(this, instance, command, request) }, { id ->
                notificationNavigation.acknowledge(id)
                if (notificationNavigation.pending == null) clearNotificationIntent()
            }, sessionVisibility = { id -> displayedSessionId = id; reportPushSession() }, appLanguage = languageBridge::receive)
        languageObserver = languageStore.observe {
            // Store observers hold a shared lock. Post without waiting for the activity thread.
            ownerHandler.post {
                if (languageObserver != null && !isFinishing && !isDestroyed) browser?.notifyAppStateChanged()
            }
        }
        ttsDocument = DocumentOwner(instance.id, UUID.randomUUID().toString(), TtsService.nextDocumentGeneration())
        openBrowser()
        ttsBound = bindService(Intent(this, TtsService::class.java), ttsConnection, Context.BIND_AUTO_CREATE)
    }

    private fun notificationSession(request: Intent): SessionLink? =
        if (request.action == AndroidPush.ACTION_OPEN) SessionLink.parse(request.getStringExtra(AndroidPush.EXTRA_PATH)) else null

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val target = store.find(intent.getStringExtra("instance")) ?: return
        if (target.id != instance.id) {
            switchInstance(target, intent.takeIf { it.action == AndroidPush.ACTION_OPEN })
            return
        }
        setIntent(intent)
        if (browser == null || failed) {
            queueNotification(intent)
            openBrowser()
            return
        }
        queueNotification(intent)
    }

    private fun queueNotification(request: Intent) {
        notificationNavigation.open(request.getStringExtra(AndroidPush.EXTRA_NAVIGATION_ID) ?: UUID.randomUUID().toString(),
            notificationSession(request)?.path, safeUrl,
            request.getStringExtra(AndroidPush.EXTRA_ACTION), request.getStringExtra(AndroidPush.EXTRA_DETAIL))
        if (notificationNavigation.pending == null) clearNotificationIntent()
        else browser?.notifyAppStateChanged()
    }

    private fun clearNotificationIntent() {
        listOf(AndroidPush.EXTRA_PATH, AndroidPush.EXTRA_ACTION, AndroidPush.EXTRA_DETAIL, AndroidPush.EXTRA_NAVIGATION_ID)
            .forEach { intent.removeExtra(it) }
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
            setBackgroundColor(getColor(R.color.twicc_canvas))
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
            documentLoaded = false
            displayedSessionId = null
            reportPushSession()
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
                reportPushSession()
            }
        }
        override fun onProgressChanged(progress: Int) {
            this@BrowserActivity.progress.progress = progress
            this@BrowserActivity.progress.visibility = if (progress < 100 && !failed) View.VISIBLE else View.GONE
        }
        override fun onPageFinished() {
            progress.visibility = View.GONE
            documentLoaded = true
            reportPushSession()
        }
        override fun onHistoryChanged(canGoBack: Boolean) { this@BrowserActivity.canGoBack = canGoBack }
        override fun onFailure(message: String) { showError(message) }
        override fun onRendererStopped(crashed: Boolean) {
            android.util.Log.w("TwiCCBrowser", "Renderer ${if (crashed) "crashed" else "was killed"}; foreground=$foreground")
            rendererRecovery.stopped()
            disposeBrowser()
            showError("The page stopped unexpectedly. Retry to reopen TwiCC.")
            recoverRenderer()
        }
        override fun onAppMessage(message: String): String {
            if (Looper.myLooper() == ownerHandler.looper) return appBridge.receive(message)
            val response = FutureTask { appBridge.receive(message) }
            ownerHandler.post(response)
            return response.get()
        }
        override fun onDownload(source: DownloadSource) { BrowserDownloads.save(this@BrowserActivity, source) }
    }

    private fun refresh(force: Boolean) {
        rendererRecovery.reset()
        if (force || browser == null) { openBrowser(force); return }
        failed = false
        documentLoaded = false
        reportPushSession()
        errorPanel.visibility = View.GONE
        browser?.load(safeUrl)
    }

    private fun showError(message: String, canRetry: Boolean = true) {
        failed = true
        reportPushSession()
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
        .apply {
            notificationNavigation.pending?.let { put("navigation", JSONObject().put("id", it.id).put("path", it.path).put("action", it.action).put("detail", it.detail)) }
        }
        .put("push", AndroidPush.state(this, instance))
        .put("capabilities", JSONObject().apply {
            put("androidPush", 1)
            if (browser?.nativeTtsSupported == true) put("nativeTts", 1)
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
        clearBatteryAdvice()
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

    private fun switchInstance(target: Instance, notification: Intent? = null) {
        if (target.id == instance.id) return
        TtsService.stopForInstance(instance.id)
        store.rememberPage(instance, safeUrl)
        store.select(target)
        disposeBrowser()
        startActivity(Intent(this, BrowserActivity::class.java).putExtra("instance", target.id).apply {
            if (notification != null) {
                action = AndroidPush.ACTION_OPEN
                listOf(AndroidPush.EXTRA_PATH, AndroidPush.EXTRA_ACTION, AndroidPush.EXTRA_DETAIL, AndroidPush.EXTRA_NAVIGATION_ID)
                    .forEach { key -> notification.getStringExtra(key)?.let { putExtra(key, it) } }
            }
        })
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
        rendererRecovery.lastAttemptMs?.let { outState.putLong("rendererRecoveryAt", it) }
        super.onSaveInstanceState(outState)
    }

    private fun reportPushSession() {
        if (::instance.isInitialized && foreground && documentLoaded && browser != null && !failed &&
            displayedSessionId != null && SessionLink.fromLocation(safeUrl)?.sessionId == displayedSessionId)
            AndroidPush.showSession(this, pushVisibilityOwner, instance.id, safeUrl)
        else AndroidPush.hideSession(pushVisibilityOwner)
    }

    override fun onPause() {
        foreground = false
        reportPushSession()
        if (::instance.isInitialized) store.rememberPage(instance, safeUrl)
        super.onPause()
    }

    override fun onResume() {
        super.onResume()
        foreground = true
        reportPushSession()
        recoverRenderer()
    }

    private fun recoverRenderer() {
        // Start the replacement after the engine callback returns.
        root.post {
            if (!isFinishing && !isDestroyed && browser == null &&
                rendererRecovery.takeAttempt(SystemClock.elapsedRealtime(), foreground)) openBrowser()
        }
    }

    override fun onStart() {
        super.onStart()
        browser?.setVisible(true)
    }

    override fun onStop() {
        AndroidPush.hideSession(pushVisibilityOwner)
        clearBatteryAdvice()
        browser?.setVisible(false)
        super.onStop()
    }

    @Deprecated("The platform file picker still uses this Activity result API.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        requests?.onActivityResult(requestCode, resultCode, data)
    }

    private fun disposeBrowser() {
        documentLoaded = false
        displayedSessionId = null
        AndroidPush.hideSession(pushVisibilityOwner)
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
        languageObserver?.close()
        languageObserver = null
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
        if (command is TtsCommand.Manual || command is TtsCommand.Listen || command is TtsCommand.Disable || command == TtsCommand.StopAll)
            clearBatteryAdvice()
        if (command is TtsCommand.Manual || command is TtsCommand.Listen) {
            TtsNotification.visiblePreflight(this)?.let { return Acceptance(false, error = it) }
            if (command is TtsCommand.Listen && TtsNotification.batteryAdviceNeeded(this)) {
                batteryAdviceCommand = command
                val dialog = TtsNotification.showBatteryAdvice(this) {
                    if (batteryAdviceCommand === command) batteryAdviceConfirmed = true
                }
                batteryAdviceDialog = dialog
                dialog.setOnDismissListener {
                    if (batteryAdviceDialog === dialog) {
                        batteryAdviceDialog = null
                        if (!batteryAdviceConfirmed) batteryAdviceCommand = null
                    }
                }
                return Acceptance(false, error = TtsActionError("confirmation_required", "Choose how to continue in the battery advice dialog."))
            }
            attachNativeTtsDocument()
        }
        return ttsBinder?.dispatch(command) ?: Acceptance(false,
            error = TtsActionError("initializing", "Speech is getting ready. Try again in a moment."))
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) reportPushSession()
        if (!hasFocus || !batteryAdviceConfirmed) return
        val command = batteryAdviceCommand ?: return
        clearBatteryAdvice()
        // Dialog dismissal restores window focus asynchronously. Never revive a stopped or replaced page.
        if (isFinishing || isDestroyed || command.owner != ttsDocument) return
        val acceptance = dispatchNativeTts(command)
        if (!acceptance.accepted) acceptance.error?.let { toast(it.message) }
    }

    private fun clearBatteryAdvice() {
        batteryAdviceCommand = null
        batteryAdviceConfirmed = false
        batteryAdviceDialog?.setOnDismissListener(null)
        batteryAdviceDialog?.dismiss()
        batteryAdviceDialog = null
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

}
