package com.twicc.android

import android.app.Activity
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
