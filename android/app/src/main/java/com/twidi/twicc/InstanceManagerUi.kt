package com.twidi.twicc

import android.app.Activity
import android.app.Dialog
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.os.Build
import android.text.Editable
import android.text.InputType
import android.text.TextUtils
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * Native instance manager screen in the TwiCC visual language (cyan brand, tinted neutrals,
 * translucent cards on a lit canvas). Colors come only from `@color/twicc_*`, so Android's
 * day/night resource selection restyles the screen: MainActivity is recreated on a theme change.
 * No business logic lives here; callers pass callbacks.
 */
class InstanceManagerUi(private val activity: Activity) {
    private val palette = Palette(activity)
    private lateinit var content: LinearLayout

    val root: LinearLayout = LinearLayout(activity).apply {
        id = R.id.instance_manager_root
        orientation = LinearLayout.VERTICAL
    }

    init {
        val scroll = ScrollView(activity).apply {
            id = R.id.instance_manager_scroll
            clipToPadding = false
            isVerticalScrollBarEnabled = false
            overScrollMode = View.OVER_SCROLL_IF_CONTENT_SCROLLS
        }
        content = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(activity.dp(12), activity.dp(8), activity.dp(12), activity.dp(12))
        }
        scroll.addView(content, ViewGroup.LayoutParams(-1, -2))
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(addButton(), LinearLayout.LayoutParams(-1, -2).apply {
            setMargins(activity.dp(12), activity.dp(8), activity.dp(12), activity.dp(12))
        })
    }

    var onAdd: () -> Unit = {}

    private fun addButton(): View = object : LinearLayout(activity) {
        override fun getAccessibilityClassName(): CharSequence = Button::class.java.name
    }.apply {
        id = R.id.instance_add_button
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER
        setPadding(activity.dp(12), activity.dp(12), activity.dp(12), activity.dp(12))
        contentDescription = activity.getString(R.string.instances_add)
        importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_YES
        isFocusable = true
        minimumHeight = activity.dp(52)
        stateListAnimator = null
        elevation = activity.dp(2).toFloat()
        addView(ImageView(activity).apply {
            setImageResource(R.drawable.ic_plus)
            imageTintList = ColorStateList.valueOf(palette.accentOn)
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        }, LinearLayout.LayoutParams(activity.dp(24), activity.dp(24)).apply {
            marginEnd = activity.dp(8)
        })
        addView(text(activity.getString(R.string.instances_add), 16f, palette.accentOn, bold = true).apply {
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        })
        background = ripple(
            GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, intArrayOf(Color.argb(36, 255, 255, 255), Color.argb(26, 0, 0, 0))).let {
                // The brand button of the web app: accent fill with a light-to-dark shading over it.
                val fill = rounded(palette.accent, 6)
                android.graphics.drawable.LayerDrawable(arrayOf<android.graphics.drawable.Drawable>(fill, it.apply { cornerRadius = activity.dp(6).toFloat() }))
            },
            Color.argb(60, 0, 0, 0),
            6,
        )
        setOnClickListener { onAdd() }
    }

    /** Rebuild the header and the list. [lastId] marks the instance opened last. */
    fun render(
        instances: List<Instance>,
        lastId: String?,
        onOpen: (Instance) -> Unit,
        onEdit: (Instance) -> Unit,
        onRemove: (Instance) -> Unit,
    ) {
        content.removeAllViews()
        content.addView(header())
        if (instances.isEmpty()) {
            content.addView(emptyState())
            return
        }
        content.addView(text(activity.getString(R.string.instances_saved).uppercase(), 12f, palette.textQuiet, bold = true).apply {
            letterSpacing = 0.06f
            setPadding(activity.dp(4), 0, 0, activity.dp(8))
        })
        val list = LinearLayout(activity).apply {
            id = R.id.instance_list
            orientation = LinearLayout.VERTICAL
        }
        instances.forEach { instance ->
            list.addView(card(instance, instance.id == lastId, onOpen, onEdit, onRemove), LinearLayout.LayoutParams(-1, -2).apply {
                bottomMargin = activity.dp(8)
            })
        }
        content.addView(list)
    }

    private fun header(): View = LinearLayout(activity).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER_HORIZONTAL
        setPadding(activity.dp(8), activity.dp(16), activity.dp(8), activity.dp(24))
        addView(ImageView(activity).apply {
            setImageResource(R.drawable.twicc_robot)
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        }, LinearLayout.LayoutParams(activity.dp(80), activity.dp(80)))
        addView(text(activity.getString(R.string.instances_title), 22f, palette.text, bold = true).apply {
            letterSpacing = 0.05f
            gravity = Gravity.CENTER
            isAccessibilityHeading = true
        })
        addView(text(activity.getString(R.string.instances_subtitle), 14f, palette.textQuiet).apply {
            gravity = Gravity.CENTER
            setPadding(0, activity.dp(2), 0, 0)
        })
    }

    private fun emptyState(): View = LinearLayout(activity).apply {
        id = R.id.instance_empty
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER_HORIZONTAL
        setPadding(activity.dp(16), activity.dp(32), activity.dp(16), activity.dp(32))
        background = rounded(palette.veil, 12).apply { setStroke(activity.dp(1), palette.border, activity.dp(6).toFloat(), activity.dp(4).toFloat()) }
        addView(text(activity.getString(R.string.instances_empty_title), 18f, palette.text, bold = true).apply { gravity = Gravity.CENTER })
        addView(text(activity.getString(R.string.instances_empty_body), 14f, palette.textQuiet).apply {
            gravity = Gravity.CENTER
            setPadding(0, activity.dp(4), 0, 0)
        })
    }

    private fun card(
        instance: Instance,
        isLast: Boolean,
        onOpen: (Instance) -> Unit,
        onEdit: (Instance) -> Unit,
        onRemove: (Instance) -> Unit,
    ): View = LinearLayout(activity).apply {
        tag = "instance-card:${instance.id}"
        orientation = LinearLayout.HORIZONTAL
        background = rounded(palette.veil, 12).apply { setStroke(activity.dp(1), palette.border) }
        clipToOutline = true
        addView(LinearLayout(activity).apply {
            tag = "instance-open:${instance.id}"
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = activity.dp(64)
            setPadding(activity.dp(12), activity.dp(8), activity.dp(8), activity.dp(8))
            isClickable = true
            isFocusable = true
            background = ripple(null, palette.pressed, 0)
            contentDescription = activity.getString(R.string.instances_open_description, instance.name, instance.address)
            setOnClickListener { onOpen(instance) }
            addView(avatar(instance), LinearLayout.LayoutParams(activity.dp(40), activity.dp(40)).apply { marginEnd = activity.dp(12) })
            addView(LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                addView(text(instance.name, 16f, palette.text, bold = true).apply {
                    maxLines = 2
                    ellipsize = TextUtils.TruncateAt.END
                })
                addView(text(instance.address, 14f, palette.textQuiet).apply {
                    maxLines = 1
                    ellipsize = TextUtils.TruncateAt.END
                })
                if (isLast) addView(lastUsedPill(), LinearLayout.LayoutParams(-2, -2).apply {
                    topMargin = activity.dp(4)
                })
            }, LinearLayout.LayoutParams(0, -2, 1f))
        }, LinearLayout.LayoutParams(0, -2, 1f))
        addView(View(activity).apply { setBackgroundColor(palette.divider) }, LinearLayout.LayoutParams(activity.dp(1), -1))
        addView(iconButton(R.drawable.ic_edit, palette.textQuiet, activity.getString(R.string.instances_edit_description, instance.name), "instance-edit:${instance.id}") { onEdit(instance) })
        addView(iconButton(R.drawable.ic_trash, palette.danger, activity.getString(R.string.instances_remove_description, instance.name), "instance-remove:${instance.id}") { onRemove(instance) })
    }

    private fun avatar(instance: Instance): View = TextView(activity).apply {
        text = instance.name.firstOrNull()?.uppercase() ?: "?"
        gravity = Gravity.CENTER
        textSize = 16f
        typeface = Typeface.create(typeface, Typeface.BOLD)
        setTextColor(Color.WHITE)
        importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        background = GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, intArrayOf(palette.logoTop, palette.logoBottom)).apply {
            cornerRadius = activity.dp(6).toFloat()
        }
    }

    private fun lastUsedPill(): View = text(activity.getString(R.string.instances_last_used), 11f, palette.accentText, bold = true).apply {
        maxLines = 1
        setPadding(activity.dp(8), activity.dp(1), activity.dp(8), activity.dp(1))
        background = rounded(palette.accentQuiet, 99)
    }

    private fun iconButton(icon: Int, tint: Int, description: String, viewTag: String, onClick: () -> Unit) = ImageButton(activity).apply {
        tag = viewTag
        setImageResource(icon)
        imageTintList = ColorStateList.valueOf(tint)
        contentDescription = description
        scaleType = ImageView.ScaleType.CENTER
        minimumWidth = activity.dp(48)
        minimumHeight = activity.dp(48)
        background = ripple(null, palette.pressed, 0)
        setOnClickListener { onClick() }
        layoutParams = LinearLayout.LayoutParams(activity.dp(48), -1)
    }

    // ── Bottom sheet ────────────────────────────────────────────────────────────

    /** Draft values of the add/edit form, kept by the caller so they survive a recreation. */
    class FormDraft(var name: String, var address: String, var error: String?)

    /**
     * Add/edit form in a bottom sheet. [onSave] returns an error message, or null when saved (the
     * sheet then closes). Back, scrim tap and Cancel call [onCancel].
     */
    fun showForm(isEdit: Boolean, draft: FormDraft, onSave: (name: String, address: String) -> String?, onCancel: () -> Unit): Dialog {
        val name = field(R.id.instance_form_name, activity.getString(R.string.instances_name_label), draft.name, InputType.TYPE_TEXT_FLAG_CAP_SENTENCES, EditorInfo.IME_ACTION_NEXT)
        val address = field(R.id.instance_form_address, "https://twicc.example.com", draft.address, InputType.TYPE_TEXT_VARIATION_URI, EditorInfo.IME_ACTION_DONE)
        val error = LinearLayout(activity).apply {
            id = R.id.instance_form_error
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(activity.dp(12), activity.dp(8), activity.dp(12), activity.dp(8))
            background = rounded(palette.dangerQuiet, 6).apply { setStroke(activity.dp(1), palette.dangerBorder) }
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
            addView(ImageView(activity).apply {
                setImageResource(R.drawable.ic_alert)
                imageTintList = ColorStateList.valueOf(palette.danger)
                importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            }, LinearLayout.LayoutParams(activity.dp(20), activity.dp(20)).apply { marginEnd = activity.dp(8) })
            addView(text("", 14f, palette.text), LinearLayout.LayoutParams(0, -2, 1f))
        }
        fun showError(message: String?) {
            draft.error = message
            error.visibility = if (message == null) View.GONE else View.VISIBLE
            (error.getChildAt(1) as TextView).text = message.orEmpty()
        }
        showError(draft.error)
        val clearError = object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
            override fun afterTextChanged(s: Editable?) {
                draft.name = name.text.toString()
                draft.address = address.text.toString()
                if (draft.error != null) showError(null)
            }
        }
        name.addTextChangedListener(clearError)
        address.addTextChangedListener(clearError)

        lateinit var sheet: Dialog
        fun save() {
            val message = onSave(name.text.toString(), address.text.toString())
            if (message == null) sheet.dismiss() else {
                showError(message)
                address.requestFocus()
            }
        }
        address.setOnEditorActionListener { _, action, _ ->
            if (action == EditorInfo.IME_ACTION_DONE) save()
            action == EditorInfo.IME_ACTION_DONE
        }
        val body = sheetBody(
            R.id.instance_form_title,
            activity.getString(if (isEdit) R.string.instances_edit_title else R.string.instances_add),
        ).apply {
            addView(error, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = activity.dp(12) })
            addView(label(activity.getString(R.string.instances_name_label), R.id.instance_form_name))
            addView(name, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = activity.dp(12) })
            addView(label(activity.getString(R.string.instances_address_label), R.id.instance_form_address))
            addView(address, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = activity.dp(4) })
            addView(text(activity.getString(R.string.instances_address_hint), 12f, palette.textQuiet), LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = activity.dp(16) })
        }
        val cancelButton = sheetButton(R.id.instance_form_cancel, activity.getString(R.string.instances_cancel), primary = false) { sheet.cancel() }
        body.addView(buttonRow(cancelButton, sheetButton(R.id.instance_form_save, activity.getString(R.string.instances_save), primary = true) { save() }))
        sheet = buildSheet(body, onCancel)
        sheet.window?.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_STATE_VISIBLE or WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        sheet.show()
        address.requestFocus()
        address.setSelection(address.text.length)
        return sheet
    }

    /** Removal confirmation in a bottom sheet. [onConfirm] runs, then the sheet closes. */
    fun showRemove(instance: Instance, onConfirm: () -> Unit, onCancel: () -> Unit): Dialog {
        lateinit var sheet: Dialog
        val body = sheetBody(R.id.instance_remove_title, activity.getString(R.string.instances_remove_title, instance.name)).apply {
            addView(text(activity.getString(R.string.instances_remove_message), 14f, palette.textQuiet), LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = activity.dp(16) })
            addView(buttonRow(
                sheetButton(R.id.instance_remove_cancel, activity.getString(R.string.instances_cancel), primary = false) { sheet.cancel() },
                sheetButton(R.id.instance_remove_confirm, activity.getString(R.string.instances_remove), primary = true, danger = true) {
                    onConfirm()
                    sheet.dismiss()
                },
            ))
        }
        sheet = buildSheet(body, onCancel)
        sheet.show()
        return sheet
    }

    private fun sheetBody(titleId: Int, title: String) = LinearLayout(activity).apply {
        orientation = LinearLayout.VERTICAL
        addView(View(activity).apply { background = rounded(palette.border, 99) }, LinearLayout.LayoutParams(activity.dp(36), activity.dp(4)).apply {
            gravity = Gravity.CENTER_HORIZONTAL
            bottomMargin = activity.dp(12)
        })
        addView(text(title, 18f, palette.text, bold = true).apply {
            id = titleId
            isAccessibilityHeading = true
        }, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = activity.dp(12) })
    }

    private fun buttonRow(left: View, right: View) = LinearLayout(activity).apply {
        orientation = LinearLayout.HORIZONTAL
        addView(left, LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = activity.dp(8) })
        addView(right, LinearLayout.LayoutParams(0, -2, 1f))
    }

    private fun sheetButton(id: Int, label: String, primary: Boolean, danger: Boolean = false, onClick: () -> Unit) = Button(activity).apply {
        this.id = id
        text = label
        isAllCaps = false
        textSize = 16f
        typeface = Typeface.create(typeface, Typeface.BOLD)
        minHeight = activity.dp(48)
        minimumHeight = activity.dp(48)
        stateListAnimator = null
        val fill = when {
            danger -> palette.danger
            primary -> palette.accent
            else -> palette.surface
        }
        setTextColor(if (danger) palette.dangerOn else if (primary) palette.accentOn else palette.text)
        background = ripple(rounded(fill, 6).apply { if (!primary) setStroke(activity.dp(1), palette.border) }, palette.pressed, 6)
        setOnClickListener { onClick() }
    }

    private fun label(label: String, forId: Int) = text(label, 14f, palette.text, bold = true).apply {
        labelFor = forId
        setPadding(0, 0, 0, activity.dp(4))
    }

    private fun field(id: Int, hint: String, value: String, variation: Int, action: Int) = EditText(activity).apply {
        this.id = id
        this.hint = hint
        setText(value)
        inputType = InputType.TYPE_CLASS_TEXT or variation
        imeOptions = action or EditorInfo.IME_FLAG_NO_EXTRACT_UI
        setSingleLine()
        textSize = 16f
        setTextColor(palette.text)
        setHintTextColor(palette.textQuiet)
        minHeight = activity.dp(48)
        setPadding(activity.dp(12), 0, activity.dp(12), 0)
        val idle = rounded(palette.surface, 6).apply { setStroke(activity.dp(1), palette.border) }
        val focused = rounded(palette.surface, 6).apply { setStroke(activity.dp(2), palette.accentText) }
        background = idle
        setOnFocusChangeListener { _, hasFocus -> background = if (hasFocus) focused else idle }
    }

    /**
     * A transparent full-screen window with the sheet anchored at the bottom. The sheet scrolls when
     * the keyboard, a small screen or a large font leave too little room.
     */
    private fun buildSheet(body: LinearLayout, onCancel: () -> Unit): Dialog {
        val dialog = Dialog(activity, R.style.TwiccSheet)
        val frame = FrameLayout(activity).apply { setBackgroundColor(palette.scrim) }
        val scroll = ScrollView(activity).apply {
            isFillViewport = false
            background = GradientDrawable().apply {
                setColor(palette.surface)
                setStroke(activity.dp(1), palette.border)
                val r = activity.dp(12).toFloat()
                cornerRadii = floatArrayOf(r, r, r, r, 0f, 0f, 0f, 0f)
            }
            isClickable = true // taps inside the sheet must not reach the scrim
            addView(body)
        }
        val padding = activity.dp(16)
        body.setPadding(padding, padding, padding, padding)
        frame.addView(scroll, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL))
        frame.setOnClickListener { dialog.cancel() }
        dialog.setContentView(frame)
        dialog.window?.apply {
            setLayout(-1, -1)
            setBackgroundDrawable(android.graphics.drawable.ColorDrawable(Color.TRANSPARENT))
            setGravity(Gravity.BOTTOM)
        }
        // Insets: the status bar above the sheet; the navigation bar or the keyboard below it, inside the sheet.
        if (Build.VERSION.SDK_INT >= 30) {
            dialog.window?.setDecorFitsSystemWindows(false)
            frame.setOnApplyWindowInsetsListener { view, insets ->
                val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
                val keyboard = insets.getInsets(WindowInsets.Type.ime())
                view.setPadding(bars.left, bars.top, bars.right, 0)
                body.setPadding(padding, padding, padding, padding + maxOf(bars.bottom, keyboard.bottom))
                WindowInsets.CONSUMED
            }
            frame.requestApplyInsets()
        } else {
            frame.fitsSystemWindows = true
        }
        dialog.setOnCancelListener { onCancel() }
        return dialog
    }

    // ── Helpers ─────────────────────────────────────────────────────────────────

    private fun text(value: String, size: Float, color: Int, bold: Boolean = false) = TextView(activity).apply {
        text = value
        textSize = size
        setTextColor(color)
        if (bold) typeface = Typeface.create(typeface, Typeface.BOLD)
    }

    private fun rounded(color: Int, radiusDp: Int) = GradientDrawable().apply {
        setColor(color)
        cornerRadius = activity.dp(radiusDp).toFloat()
    }

    private fun ripple(content: android.graphics.drawable.Drawable?, color: Int, radiusDp: Int) = RippleDrawable(
        ColorStateList.valueOf(color),
        content,
        rounded(Color.WHITE, radiusDp),
    )

    /** The `@color/twicc_*` tokens, resolved for the current day/night configuration. */
    private class Palette(activity: Activity) {
        val surface = activity.getColor(R.color.twicc_surface)
        val veil = activity.getColor(R.color.twicc_veil)
        val border = activity.getColor(R.color.twicc_border)
        val divider = activity.getColor(R.color.twicc_divider)
        val text = activity.getColor(R.color.twicc_text)
        val textQuiet = activity.getColor(R.color.twicc_text_quiet)
        val accent = activity.getColor(R.color.twicc_accent)
        val accentOn = activity.getColor(R.color.twicc_accent_on)
        val accentText = activity.getColor(R.color.twicc_accent_text)
        val accentQuiet = activity.getColor(R.color.twicc_accent_quiet)
        val pressed = activity.getColor(R.color.twicc_pressed)
        val danger = activity.getColor(R.color.twicc_danger)
        val dangerOn = activity.getColor(R.color.twicc_danger_on)
        val dangerQuiet = activity.getColor(R.color.twicc_danger_quiet)
        val dangerBorder = activity.getColor(R.color.twicc_danger_border)
        val scrim = activity.getColor(R.color.twicc_scrim)
        val logoTop = activity.getColor(R.color.twicc_logo_top)
        val logoBottom = activity.getColor(R.color.twicc_logo_bottom)
    }
}
