package com.twicc.android

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.net.Uri
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
import org.mozilla.geckoview.AllowOrDeny
import org.mozilla.geckoview.GeckoResult
import org.mozilla.geckoview.GeckoSession
import org.mozilla.geckoview.GeckoSessionSettings
import org.mozilla.geckoview.GeckoView
import org.mozilla.geckoview.WebRequestError
import org.mozilla.geckoview.WebResponse
import kotlin.math.abs

/** A remote web client. No native JavaScript bridge, background service, or bundled server. */
class BrowserActivity : Activity() {
    private lateinit var instance: Instance
    private lateinit var store: InstanceStore
    private lateinit var root: FrameLayout
    private lateinit var menuButton: Button
    private lateinit var progress: ProgressBar
    private lateinit var errorPanel: LinearLayout
    private var web: GeckoView? = null
    private var browser: GeckoSession? = null
    private var prompts: BrowserPrompts? = null
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
        openWebView()
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

    private fun openWebView(force: Boolean = false) {
        disposeWebView()
        failed = false
        canGoBack = false
        errorPanel.visibility = View.GONE
        try {
            val view = GeckoView(this)
            val page = GeckoSession(GeckoSessionSettings.Builder()
                .contextId(instance.profileName)
                .build())
            web = view
            browser = page
            prompts = BrowserPrompts(this)
            page.promptDelegate = prompts
            page.navigationDelegate = navigationDelegate()
            page.progressDelegate = progressDelegate()
            page.contentDelegate = contentDelegate()
            page.permissionDelegate = object : GeckoSession.PermissionDelegate {
                override fun onContentPermissionRequest(
                    session: GeckoSession,
                    perm: GeckoSession.PermissionDelegate.ContentPermission,
                ): GeckoResult<Int> {
                    val autoplay = perm.permission == GeckoSession.PermissionDelegate.PERMISSION_AUTOPLAY_AUDIBLE ||
                        perm.permission == GeckoSession.PermissionDelegate.PERMISSION_AUTOPLAY_INAUDIBLE
                    val allowed = browser === session && autoplay && instance.url.allowsNavigation(perm.uri)
                    return GeckoResult.fromValue(if (allowed)
                        GeckoSession.PermissionDelegate.ContentPermission.VALUE_ALLOW
                    else GeckoSession.PermissionDelegate.ContentPermission.VALUE_DENY)
                }
            }
            page.open(BrowserEngine.get(this))
            view.setSession(page)
            root.addView(view, 0, FrameLayout.LayoutParams(-1, -1))
            page.load(GeckoSession.Loader().uri(safeUrl).flags(
                if (force) GeckoSession.LOAD_FLAGS_BYPASS_CACHE else GeckoSession.LOAD_FLAGS_NONE,
            ))
        } catch (_: RuntimeException) {
            disposeWebView()
            showError("GeckoView could not start. Close and reopen TwiCC, then try again.")
        }
    }

    private fun navigationDelegate() = object : GeckoSession.NavigationDelegate {
        override fun onLoadRequest(
            session: GeckoSession,
            request: GeckoSession.NavigationDelegate.LoadRequest,
        ): GeckoResult<AllowOrDeny> {
            if (browser !== session) return GeckoResult.fromValue(AllowOrDeny.DENY)
            val newWindow = request.target == GeckoSession.NavigationDelegate.TARGET_WINDOW_NEW
            if (!newWindow && instance.url.allowsNavigation(request.uri)) {
                return GeckoResult.fromValue(AllowOrDeny.ALLOW)
            }
            // Subframe requests have a separate delegate method and remain engine-managed.
            // New windows never create another hidden TwiCC session or audio player.
            if (!newWindow || request.hasUserGesture) openExternal(request.uri)
            return GeckoResult.fromValue(AllowOrDeny.DENY)
        }

        override fun onLocationChange(
            session: GeckoSession,
            url: String?,
            perms: MutableList<GeckoSession.PermissionDelegate.ContentPermission>,
            hasUserGesture: Boolean,
        ) {
            if (browser !== session) return
            url?.takeIf { instance.url.allowsNavigation(it) }?.let {
                safeUrl = it
                store.rememberPage(instance, it)
            }
        }

        override fun onCanGoBack(session: GeckoSession, canGoBack: Boolean) {
            if (browser === session) this@BrowserActivity.canGoBack = canGoBack
        }

        override fun onLoadError(session: GeckoSession, uri: String?, error: WebRequestError): GeckoResult<String>? {
            if (browser === session) {
                showError(if (error.category == WebRequestError.ERROR_CATEGORY_SECURITY)
                    "The HTTPS certificate could not be verified. Check the tunnel certificate and your phone's date."
                else "Cannot reach ${instance.name}. Check your connection and HTTPS tunnel, then retry.")
            }
            // Do not install Gecko's privileged certificate-exception error page.
            return null
        }
    }

    private fun progressDelegate() = object : GeckoSession.ProgressDelegate {
        override fun onPageStart(session: GeckoSession, url: String) {
            if (browser !== session) return
            failed = false
            errorPanel.visibility = View.GONE
            progress.visibility = View.VISIBLE
            progress.progress = 0
        }

        override fun onProgressChange(session: GeckoSession, value: Int) {
            if (browser !== session) return
            progress.progress = value
            progress.visibility = if (value < 100 && !failed) View.VISIBLE else View.GONE
        }

        override fun onPageStop(session: GeckoSession, success: Boolean) {
            if (browser === session) progress.visibility = View.GONE
        }
    }

    private fun contentDelegate() = object : GeckoSession.ContentDelegate {
        override fun onExternalResponse(session: GeckoSession, response: WebResponse) {
            if (browser !== session) { runCatching { response.body?.close() }; return }
            BrowserDownloads.save(this@BrowserActivity, response)
        }

        override fun onCrash(session: GeckoSession) = engineStopped(session)
        override fun onKill(session: GeckoSession) = engineStopped(session)
    }

    private fun engineStopped(session: GeckoSession) {
        if (browser !== session) return
        disposeWebView()
        showError("Android stopped the Gecko content process. Reload to reconnect to TwiCC.")
    }

    private fun refresh(force: Boolean) {
        // A new session discards stale page callbacks while keeping its persistent storage context.
        if (force || browser == null) { openWebView(force); return }
        failed = false
        errorPanel.visibility = View.GONE
        browser?.stop()
        browser?.loadUri(safeUrl)
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
            menu.add("TwiCC ${BuildConfig.VERSION_NAME} · GeckoView 159").isEnabled = false
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
        disposeWebView()
        startActivity(Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            .putExtra("manage", true))
        finish()
    }

    private fun openExternal(url: String) {
        if (!InstanceUrl.canOpenExternally(url)) {
            toast("This link type is not supported.")
            return
        }
        try {
            val target = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE)
            if (Uri.parse(url).scheme?.lowercase() in listOf("http", "https")) {
                // Resolve browsers against an unrelated reserved host, not the destination.
                // This avoids verified app links sending a GitHub/YouTube link into its app.
                val probe = Intent(Intent.ACTION_VIEW, Uri.parse("https://browser-selection.invalid/"))
                    .addCategory(Intent.CATEGORY_BROWSABLE)
                val browsers = packageManager.queryIntentActivities(probe, PackageManager.MATCH_DEFAULT_ONLY)
                    .map { it.activityInfo.packageName }.distinct()
                val preferred = probe.resolveActivity(packageManager)?.packageName
                when {
                    preferred in browsers -> startActivity(target.setPackage(preferred))
                    browsers.size == 1 -> startActivity(target.setPackage(browsers.first()))
                    browsers.isNotEmpty() -> {
                        val choices = browsers.map { Intent(target).setPackage(it) }
                        startActivity(Intent.createChooser(choices.first(), "Open in browser")
                            .putExtra(Intent.EXTRA_INITIAL_INTENTS, choices.drop(1).toTypedArray()))
                    }
                    else -> toast("No browser is available to open this link.")
                }
            } else startActivity(target)
        } catch (_: ActivityNotFoundException) {
            toast("No application can open this link.")
        }
    }

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
        browser?.setActive(true)
    }

    override fun onStop() {
        browser?.setActive(false)
        super.onStop()
    }

    @Deprecated("The platform file picker still uses this Activity result API.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        prompts?.onActivityResult(requestCode, resultCode, data)
    }

    private fun disposeWebView() {
        val page = browser
        browser = null // Ignore callbacks from the old session during disposal.
        web?.let { view ->
            view.releaseSession()
            (view.parent as? ViewGroup)?.removeView(view)
        }
        web = null
        if (page?.isOpen == true) page.close()
        prompts?.close()
        prompts = null
        canGoBack = false
    }

    override fun onDestroy() {
        disposeWebView()
        super.onDestroy()
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

}

private fun android.content.res.Configuration.isNightModeActiveCompat(): Boolean =
    uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK == android.content.res.Configuration.UI_MODE_NIGHT_YES
