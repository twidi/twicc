package com.twicc.android

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.net.http.SslError
import android.os.Bundle
import android.os.Environment
import android.os.Message
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.SslErrorHandler
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.RenderProcessGoneDetail
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.ProgressBar
import android.widget.Toast
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import kotlin.math.abs

/** A remote web client. No native JavaScript bridge, background service, or bundled server. */
class BrowserActivity : Activity() {
    private lateinit var instance: Instance
    private lateinit var store: InstanceStore
    private lateinit var root: FrameLayout
    private lateinit var menuButton: Button
    private lateinit var progress: ProgressBar
    private lateinit var errorPanel: LinearLayout
    private var web: WebView? = null
    private var cookies: CookieManager? = null
    private var chooser: ValueCallback<Array<Uri>>? = null
    private val popups = mutableSetOf<WebView>()
    private var failed = false
    private var forceLoading = false
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
        val supported = runCatching {
            WebViewFeature.isFeatureSupported(WebViewFeature.MULTI_PROFILE)
        }.getOrDefault(false)
        if (!supported) {
            showError("Update Android System WebView or Chrome to use separate TwiCC instances. Then close and reopen this app.", false)
            return
        }
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

    @SuppressLint("SetJavaScriptEnabled")
    private fun openWebView(force: Boolean = false) {
        disposeWebView()
        failed = false
        errorPanel.visibility = View.GONE
        try {
            if (!WebViewFeature.isFeatureSupported(WebViewFeature.MULTI_PROFILE)) return showError(
                "Update Android System WebView or Chrome to use separate TwiCC instances.", false,
            )
            val view = WebView(this)
            web = view
            // Set the profile before touching any WebView API that can initialize a page.
            WebViewCompat.setProfile(view, instance.profileName)
            cookies = WebViewCompat.getProfile(view).cookieManager.apply {
                setAcceptCookie(true)
                setAcceptThirdPartyCookies(view, true)
            }
            view.settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                useWideViewPort = true
                loadWithOverviewMode = true
                mediaPlaybackRequiresUserGesture = false
                mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                allowFileAccess = false
                allowContentAccess = true // System file picker attachments.
                setSupportMultipleWindows(true)
                javaScriptCanOpenWindowsAutomatically = false
                builtInZoomControls = true
                displayZoomControls = false
            }
            WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
            view.webViewClient = pageClient()
            view.webChromeClient = chromeClient()
            view.setDownloadListener { url, agent, disposition, mime, _ ->
                download(url, agent, disposition, mime)
            }
            root.addView(view, 0, FrameLayout.LayoutParams(-1, -1))
            if (force) {
                view.clearCache(true)
                forceLoading = true
                view.settings.cacheMode = WebSettings.LOAD_NO_CACHE
                view.loadUrl(safeUrl, mapOf("Cache-Control" to "no-cache", "Pragma" to "no-cache"))
            } else view.loadUrl(safeUrl)
        } catch (_: RuntimeException) {
            disposeWebView()
            showError("The WebView could not start. Update Android System WebView or Chrome, then try again.")
        }
    }

    private fun pageClient() = object : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            if (web !== view) return true
            // Embedded browsers and artifacts own their iframe navigation.
            if (!request.isForMainFrame) return false
            val url = request.url.toString()
            if (instance.url.allowsNavigation(url)) return false
            openExternal(url)
            return true
        }

        override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
            if (web !== view) return
            if (url != null && !instance.url.allowsNavigation(url)) {
                view.stopLoading()
                if (InstanceUrl.canOpenExternally(url)) {
                    openExternal(url)
                    view.loadUrl(safeUrl)
                } else {
                    showError("This page cannot open inside TwiCC. Retry to return to your server.")
                }
                return
            }
            failed = false
            errorPanel.visibility = View.GONE
            progress.visibility = View.VISIBLE
            url?.takeIf { instance.url.allowsNavigation(it) }?.let { safeUrl = it }
        }

        override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
            if (web !== view) return
            url?.takeIf { instance.url.allowsNavigation(it) }?.let {
                safeUrl = it
                store.rememberPage(instance, it)
            }
        }

        override fun onPageFinished(view: WebView, url: String?) {
            if (web !== view) return
            progress.visibility = View.GONE
            restoreCacheMode()
            cookies?.flush()
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (web === view && request.isForMainFrame) {
                showError("Cannot reach ${instance.name}. Check your connection and HTTPS tunnel, then retry.")
            }
        }

        override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
            if (web === view && request.isForMainFrame && response.statusCode >= 400) {
                showError("The server returned HTTP ${response.statusCode}. Check your tunnel or server, then retry.")
            }
        }

        override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
            handler.cancel()
            if (web === view && (error.url == safeUrl || error.url == view.url)) {
                showError("The HTTPS certificate could not be verified. Check the tunnel certificate and your phone's date.")
            }
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            if (web === view) {
                disposeWebView()
                showError("Android stopped the WebView. Reload to reconnect to TwiCC.")
            }
            return true
        }
    }

    private fun chromeClient() = object : WebChromeClient() {
        override fun onProgressChanged(view: WebView, newProgress: Int) {
            if (web !== view) return
            progress.progress = newProgress
            progress.visibility = if (newProgress < 100 && !failed) View.VISIBLE else View.GONE
        }

        override fun onShowFileChooser(
            view: WebView,
            callback: ValueCallback<Array<Uri>>,
            params: FileChooserParams,
        ): Boolean {
            chooser?.onReceiveValue(null)
            chooser = callback
            return try {
                @Suppress("DEPRECATION")
                startActivityForResult(params.createIntent(), FILE_CHOOSER)
                true
            } catch (_: ActivityNotFoundException) {
                chooser = null
                callback.onReceiveValue(null)
                toast("No file picker is available.")
                true
            }
        }

        override fun onCreateWindow(view: WebView, isDialog: Boolean, isUserGesture: Boolean, resultMsg: Message): Boolean {
            if (!isUserGesture) return false
            if (!WebViewFeature.isFeatureSupported(WebViewFeature.MULTI_PROFILE)) return false
            // A temporary WebView captures the destination. It never replaces the main page.
            val popup = WebView(this@BrowserActivity)
            WebViewCompat.setProfile(popup, instance.profileName)
            popups.add(popup)
            var handled = false
            fun consume(url: String) {
                if (handled || url == "about:blank") return
                handled = true
                openExternal(url)
                popup.post { destroyPopup(popup) }
            }
            popup.webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    if (request.isForMainFrame) consume(request.url.toString())
                    return true
                }
                override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                    url?.let { consume(it) }
                }
                override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                    destroyPopup(popup)
                    return true
                }
            }
            (resultMsg.obj as WebView.WebViewTransport).webView = popup
            resultMsg.sendToTarget()
            // Also release popups that never navigate (for example, an empty window.open()).
            menuButton.postDelayed({ destroyPopup(popup) }, 10_000)
            return true
        }
    }

    private fun refresh(force: Boolean) {
        // A fresh view prevents stale completion/error callbacks from ending cache bypass early.
        if (force || web == null) { openWebView(force); return }
        val view = web ?: return
        // Stop callbacks from the old load before enabling bypass for the new one.
        view.stopLoading()
        restoreCacheMode()
        failed = false
        errorPanel.visibility = View.GONE
        view.loadUrl(safeUrl)
    }

    private fun restoreCacheMode() {
        if (forceLoading) {
            web?.settings?.cacheMode = WebSettings.LOAD_DEFAULT
            forceLoading = false
        }
    }

    private fun showError(message: String, canRetry: Boolean = true) {
        failed = true
        restoreCacheMode()
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

    private fun download(url: String, userAgent: String?, disposition: String?, mime: String?) {
        if (!url.startsWith("https://", ignoreCase = true) || !InstanceUrl.canOpenExternally(url)) {
            toast("This download needs a browser. Open the page in your browser to save blob or data exports.")
            return
        }
        try {
            val filename = URLUtil.guessFileName(url, disposition, mime)
                .replace(Regex("[\\\\/\\p{Cntrl}]"), "_").take(180)
            val request = DownloadManager.Request(Uri.parse(url))
                .setTitle(filename)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename)
            if (!mime.isNullOrBlank()) request.setMimeType(mime)
            if (!userAgent.isNullOrBlank()) request.addRequestHeader("User-Agent", userAgent)
            if (instance.url.allowsNavigation(url)) {
                cookies?.getCookie(url)?.takeIf { it.isNotBlank() }?.let { request.addRequestHeader("Cookie", it) }
            }
            (getSystemService(DOWNLOAD_SERVICE) as DownloadManager).enqueue(request)
            toast("Download started. Check Android's Downloads notification.")
        } catch (_: RuntimeException) {
            toast("The download could not start. Try opening this page in your browser.")
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
        val view = web
        if (view != null && !failed && view.canGoBack()) view.goBack() else manageInstances()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString("url", safeUrl)
        super.onSaveInstanceState(outState)
    }

    override fun onPause() {
        cookies?.flush()
        if (::instance.isInitialized) store.rememberPage(instance, safeUrl)
        web?.onPause()
        super.onPause()
    }

    override fun onResume() {
        super.onResume()
        web?.onResume()
    }

    @Deprecated("The platform file picker still uses this Activity result API.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == FILE_CHOOSER) {
            chooser?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data))
            chooser = null
        }
    }

    private fun destroyPopup(popup: WebView) {
        if (popups.remove(popup)) {
            popup.stopLoading()
            popup.destroy()
        }
    }

    private fun disposeWebView() {
        chooser?.onReceiveValue(null)
        chooser = null
        popups.toList().forEach { destroyPopup(it) }
        web?.let { view ->
            view.stopLoading()
            (view.parent as? ViewGroup)?.removeView(view)
            view.destroy()
        }
        web = null
        cookies = null
        forceLoading = false
    }

    override fun onDestroy() {
        disposeWebView()
        super.onDestroy()
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    companion object { private const val FILE_CHOOSER = 1 }
}

private fun android.content.res.Configuration.isNightModeActiveCompat(): Boolean =
    uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK == android.content.res.Configuration.UI_MODE_NIGHT_YES
