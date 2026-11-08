package com.twidi.twicc

import android.app.Activity
import android.content.Context
import android.view.ViewGroup
import com.twidi.twicc.i18n.AndroidLanguageStore
import com.twidi.twicc.i18n.NativeMessages
import com.twidi.twicc.i18n.UiMessage
import java.util.Locale
import android.graphics.Insets
import android.os.Build
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsAnimation
import android.widget.TextView

fun Activity.dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

/** Apply system-bar and keyboard insets once, on the root, including Android 15 edge-to-edge. */
fun Activity.fitSystemInsets(root: View) {
    if (Build.VERSION.SDK_INT >= 30) {
        window.setDecorFitsSystemWindows(false)
        val systemTypes = WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout()
        val handledTypes = systemTypes or WindowInsets.Type.ime()
        val imeAnimations = mutableSetOf<WindowInsetsAnimation>()

        fun applyPadding(insets: WindowInsets) {
            val bars = insets.getInsets(systemTypes)
            val keyboard = insets.getInsets(WindowInsets.Type.ime())
            root.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, keyboard.bottom))
        }

        // The root reserves this space. Forward zero values, including during animation,
        // instead of CONSUMED so browser views still receive updates without applying it twice.
        fun remainingInsets(insets: WindowInsets): WindowInsets =
            WindowInsets.Builder(insets).setInsets(handledTypes, Insets.NONE).build()

        root.setWindowInsetsAnimationCallback(object : WindowInsetsAnimation.Callback(
            WindowInsetsAnimation.Callback.DISPATCH_MODE_CONTINUE_ON_SUBTREE,
        ) {
            override fun onPrepare(animation: WindowInsetsAnimation) {
                if (animation.typeMask and WindowInsets.Type.ime() != 0) {
                    imeAnimations.add(animation)
                }
            }

            override fun onProgress(
                insets: WindowInsets,
                runningAnimations: MutableList<WindowInsetsAnimation>,
            ): WindowInsets {
                // Keep the browser layout stable while the keyboard moves. Repeated
                // resizes make the web renderer lag behind the native animation.
                return remainingInsets(insets)
            }

            override fun onEnd(animation: WindowInsetsAnimation) {
                if (imeAnimations.remove(animation) && imeAnimations.isEmpty()) {
                    // Resize once after opening/closing, using the current final insets.
                    // Re-read them so interrupted animations also use the actual state.
                    root.requestApplyInsets()
                }
            }
        })
        root.setOnApplyWindowInsetsListener { _, insets ->
            // Android sends the end state before the first animation frame. Defer
            // resizing until onEnd, keeping keyboard animation and web reflow separate.
            if (imeAnimations.isEmpty()) applyPadding(insets)
            remainingInsets(insets)
        }
    } else {
        root.fitsSystemWindows = true
    }
    root.requestApplyInsets()
}

fun Activity.label(text: String, size: Float = 16f): TextView = TextView(this).apply {
    this.text = text
    textSize = size
    setPadding(dp(16), dp(12), dp(16), dp(12))
}

/** Keep message identity on the existing view, including open dialog fields and actions. */
fun TextView.bindUiMessage(message: UiMessage?, uppercase: Boolean = false) {
    setTag(R.id.native_text_message, message)
    setTag(R.id.native_text_uppercase, uppercase)
    val value = message?.let { NativeMessages.text(context, it) }.orEmpty()
    text = if (uppercase) value.uppercase(interfaceLocale(context)) else value
}

fun View.bindUiDescription(message: UiMessage) {
    setTag(R.id.native_description_message, message)
    contentDescription = NativeMessages.text(context, message)
}

fun TextView.bindUiHint(message: UiMessage) {
    setTag(R.id.native_hint_message, message)
    hint = NativeMessages.text(context, message)
}

/** Refresh labels only. Never replace views, input values, focus, or selection. */
fun View.refreshNativeMessages() {
    (getTag(R.id.native_text_message) as? UiMessage)?.let {
        (this as? TextView)?.bindUiMessage(it, getTag(R.id.native_text_uppercase) == true)
    }
    (getTag(R.id.native_description_message) as? UiMessage)?.let { bindUiDescription(it) }
    (getTag(R.id.native_hint_message) as? UiMessage)?.let { (this as? TextView)?.bindUiHint(it) }
    if (this is ViewGroup) for (index in 0 until childCount) getChildAt(index).refreshNativeMessages()
}

fun interfaceLocale(context: Context): Locale = Locale.forLanguageTag(
    AndroidLanguageStore(context).read().locale,
)

fun instanceValidationMessage(code: InstanceValidationCode): UiMessage =
    UiMessage(when (code) {
        InstanceValidationCode.INVALID_ADDRESS -> "android.instances.errors.invalidAddress"
        InstanceValidationCode.HTTPS_REQUIRED -> "android.instances.errors.httpsRequired"
        InstanceValidationCode.ORIGIN_REQUIRED -> "android.instances.errors.originRequired"
        InstanceValidationCode.ROOT_REQUIRED -> "android.instances.errors.rootRequired"
        InstanceValidationCode.INVALID_PORT -> "android.instances.errors.invalidPort"
        InstanceValidationCode.DUPLICATE -> "android.instances.errors.duplicate"
    })
