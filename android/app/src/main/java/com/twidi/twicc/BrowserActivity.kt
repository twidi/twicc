package com.twidi.twicc

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.Button
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
import kotlin.math.abs

/** A remote web client. No native JavaScript bridge, background service, or bundled server. */
class BrowserActivity : Activity() {
    private lateinit var instance: Instance
    private lateinit var store: InstanceStore
    private lateinit var root: FrameLayout
    private lateinit var menuButton: Button
    private lateinit var progress: ProgressBar
    private lateinit var errorPanel: LinearLayout
    private var browser: BrowserSession? = null
    private var requests: NativeRequests? = null
    private var canGoBack = false
    private var failed = false
    private var safeUrl = ""
    private var menuX = 1f
    private var menuY = 0.1f

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = InstanceStore(this)
        instance = store.find(intent.getStringExtra("instance")) ?: run { finish(); return }
        safeUrl = savedInstanceState?.getString("url")
            ?.takeIf { instance.url.allowsNavigation(it) }
            ?: store.page(instance)
            ?: "${instance.address}/"
        val preferences = getPreferences(MODE_PRIVATE)
        menuX = preferences.getFloat("menuX", 1f)
        menuY = preferences.getFloat("menuY", 0.1f)
        buildLayout()
        openBrowser()
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
        menuButton = Button(this).apply {
            text = "⋮"
            textSize = 24f
            setTextColor(Color.WHITE)
            setPadding(0, 0, 0, 0)
            minWidth = 0
            minHeight = 0
            elevation = dp(6).toFloat()
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(Color.rgb(65, 65, 83))
            }
            alpha = 0.9f
            contentDescription = "TwiCC menu for ${instance.name}. Drag to move."
            setOnClickListener { showMenu() }
        }
        root.addView(menuButton, FrameLayout.LayoutParams(dp(48), dp(48), Gravity.TOP or Gravity.START))
        makeMenuMovable()
        root.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> positionMenu() }
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
            menu.add("${instance.name} — Instances").setOnMenuItemClickListener { manageInstances(); true }
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

    private fun manageInstances() {
        disposeBrowser()
        startActivity(Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            .putExtra("manage", true))
        finish()
    }

    private fun openExternal(url: String) = ExternalLinks.open(this, url)

    @SuppressLint("ClickableViewAccessibility")
    private fun makeMenuMovable() {
        var downX = 0f
        var downY = 0f
        var startX = 0
        var startY = 0
        var moved = false
        menuButton.setOnTouchListener { _, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX; downY = event.rawY
                    val layout = menuButton.layoutParams as FrameLayout.LayoutParams
                    startX = layout.leftMargin; startY = layout.topMargin
                    moved = false
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    if (abs(event.rawX - downX) + abs(event.rawY - downY) > dp(8)) moved = true
                    if (moved) {
                        val availableX = maxOf(0, root.width - root.paddingLeft - root.paddingRight - dp(48))
                        val availableY = maxOf(0, root.height - root.paddingTop - root.paddingBottom - dp(48))
                        menuX = if (availableX == 0) 0f else (startX + event.rawX - downX).coerceIn(0f, availableX.toFloat()) / availableX
                        menuY = if (availableY == 0) 0f else (startY + event.rawY - downY).coerceIn(0f, availableY.toFloat()) / availableY
                        positionMenu()
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    if (!moved) menuButton.performClick()
                    else getPreferences(MODE_PRIVATE).edit().putFloat("menuX", menuX).putFloat("menuY", menuY).apply()
                    true
                }
                MotionEvent.ACTION_CANCEL -> true
                else -> false
            }
        }
    }

    private fun positionMenu() {
        val x = (maxOf(0, root.width - root.paddingLeft - root.paddingRight - dp(48)) * menuX.coerceIn(0f, 1f)).toInt()
        val y = (maxOf(0, root.height - root.paddingTop - root.paddingBottom - dp(48)) * menuY.coerceIn(0f, 1f)).toInt()
        val layout = menuButton.layoutParams as FrameLayout.LayoutParams
        if (layout.leftMargin != x || layout.topMargin != y) {
            layout.leftMargin = x
            layout.topMargin = y
            menuButton.layoutParams = layout
        }
    }

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
        val page = browser
        browser = null
        (page?.view?.parent as? ViewGroup)?.removeView(page.view)
        page?.close()
        requests?.close()
        requests = null
        canGoBack = false
    }

    override fun onDestroy() {
        disposeBrowser()
        super.onDestroy()
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

}

private fun android.content.res.Configuration.isNightModeActiveCompat(): Boolean =
    uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK == android.content.res.Configuration.UI_MODE_NIGHT_YES
