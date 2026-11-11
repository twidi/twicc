package com.twidi.twicc

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.res.ColorStateList
import android.widget.EditText
import android.widget.CheckedTextView
import android.graphics.Typeface
import android.os.Build
import android.util.TypedValue
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import java.util.WeakHashMap
import kotlin.math.roundToInt

/** Adapt native chrome without touching a browser view or replacing editable dialog fields. */
class NativeConfiguration {
    // Store logical dimensions, not successively rounded pixel ratios. Weak keys release closed dialogs.
    private class Geometry(view: View) {
        private val metrics = view.resources.displayMetrics
        private fun dp(value: Int) = value / metrics.density
        val padding = floatArrayOf(dp(view.paddingLeft), dp(view.paddingTop), dp(view.paddingRight), dp(view.paddingBottom))
        val width = view.layoutParams?.width?.takeIf { it > 0 }?.let(::dp)
        val height = view.layoutParams?.height?.takeIf { it > 0 }?.let(::dp)
        val margins = (view.layoutParams as? ViewGroup.MarginLayoutParams)?.let {
            floatArrayOf(dp(it.leftMargin), dp(it.topMargin), dp(it.rightMargin), dp(it.bottomMargin))
        }
        val minWidth = dp(view.minimumWidth)
        val minHeight = dp(view.minimumHeight)
        val textSp = (view as? TextView)?.let {
            if (Build.VERSION.SDK_INT >= 34) TypedValue.deriveDimension(TypedValue.COMPLEX_UNIT_SP, it.textSize, metrics)
            else @Suppress("DEPRECATION") (it.textSize / metrics.scaledDensity)
        }
        val typeface = (view as? TextView)?.typeface
        val weightAdjustment = if (Build.VERSION.SDK_INT >= 31) view.resources.configuration.fontWeightAdjustment else 0

        fun apply(view: View) {
            val current = view.resources.displayMetrics
            fun px(value: Float) = (value * current.density).roundToInt()
            view.setPadding(px(padding[0]), px(padding[1]), px(padding[2]), px(padding[3]))
            view.minimumWidth = px(minWidth)
            view.minimumHeight = px(minHeight)
            view.layoutParams?.let { params ->
                width?.let { params.width = px(it) }
                height?.let { params.height = px(it) }
                (params as? ViewGroup.MarginLayoutParams)?.let { margin -> margins?.let {
                    margin.setMargins(px(it[0]), px(it[1]), px(it[2]), px(it[3]))
                } }
                view.layoutParams = params
            }
            if (view is TextView) {
                textSp?.let { view.setTextSize(TypedValue.COMPLEX_UNIT_SP, it) }
                if (Build.VERSION.SDK_INT >= 31 && typeface != null) {
                    fun adjustment(value: Int) = if (value == Int.MAX_VALUE) 0 else value
                    val delta = adjustment(view.resources.configuration.fontWeightAdjustment) - adjustment(weightAdjustment)
                    view.typeface = Typeface.create(typeface, (typeface.weight + delta).coerceIn(1, 1000), typeface.isItalic)
                }
            }
        }
    }

    private val geometry = WeakHashMap<View, Geometry>()

    fun remember(view: View) {
        geometry.getOrPut(view) { Geometry(view) }
        if (view is ViewGroup) for (index in 0 until view.childCount) remember(view.getChildAt(index))
    }

    fun update(view: View, themeContext: Context = view.context) {
        geometry[view]?.apply(view)
        if (view is TextView) {
            val attributes = themeContext.obtainStyledAttributes(R.styleable.NativeConfiguration)
            try {
                attributes.getColorStateList(if (view is Button) R.styleable.NativeConfiguration_android_colorAccent
                    else R.styleable.NativeConfiguration_android_textColorPrimary)?.let(view::setTextColor)
                attributes.getColorStateList(R.styleable.NativeConfiguration_android_textColorHint)?.let(view::setHintTextColor)
            } finally { attributes.recycle() }
        }
        if (view is Button || view is EditText || view is CheckedTextView) {
            val attributes = themeContext.obtainStyledAttributes(R.styleable.NativeConfiguration)
            try {
                val active = attributes.getColor(R.styleable.NativeConfiguration_android_colorControlActivated, 0)
                val normal = attributes.getColor(R.styleable.NativeConfiguration_android_colorControlNormal, 0)
                val tint = ColorStateList(arrayOf(intArrayOf(android.R.attr.state_focused),
                    intArrayOf(android.R.attr.state_checked), intArrayOf()), intArrayOf(active, active, normal))
                if (view is EditText) view.backgroundTintList = tint
                if (view is Button) view.backgroundTintList = attributes.getColorStateList(R.styleable.NativeConfiguration_android_colorButtonNormal)
                if (view is CheckedTextView) view.checkMarkTintList = tint
            } finally { attributes.recycle() }
        }
        if (view is ViewGroup) for (index in 0 until view.childCount) update(view.getChildAt(index), themeContext)
        view.requestLayout()
        view.invalidate()
    }

    fun remember(dialog: AlertDialog) { dialog.window?.decorView?.let(::remember) }

    fun update(activity: Activity, dialog: AlertDialog) {
        // AlertDialog uses a cached ContextThemeWrapper. Rebase its theme onto current resources.
        dialog.context.theme.setTo(activity.theme)
        val style = TypedValue()
        activity.theme.resolveAttribute(android.R.attr.alertDialogTheme, style, true)
        if (style.resourceId != 0) dialog.context.theme.applyStyle(style.resourceId, true)
        dialog.window?.let { window ->
            val attributes = dialog.context.obtainStyledAttributes(R.styleable.NativeConfiguration)
            try {
                window.setBackgroundDrawable(attributes.getDrawable(R.styleable.NativeConfiguration_android_windowBackground))
            } finally { attributes.recycle() }
            window.decorView.layoutDirection = View.LAYOUT_DIRECTION_LOCALE
            update(window.decorView, dialog.context)
            window.decorView.requestApplyInsets()
        }
    }
}
