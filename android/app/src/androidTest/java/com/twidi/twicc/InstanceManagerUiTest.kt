package com.twidi.twicc

import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.os.Bundle
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import android.view.View
import android.view.ViewGroup
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.TextView
import android.widget.EditText
import com.twidi.twicc.i18n.AndroidLanguageStore
import com.twidi.twicc.i18n.NativeMessages
import com.twidi.twicc.i18n.UiMessage
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/** Real offline activity, dialogs, preferences, and system uiMode changes. No browser or server fixture. */
class InstanceManagerUiTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private val preferences get() = context.getSharedPreferences("instances", Context.MODE_PRIVATE)
    private var backup: Map<String, *> = emptyMap<String, Any>()
    private var originalNight = "no"
    private var scenario: ActivityScenario<MainActivity>? = null

    @Before fun prepare() {
        backup = preferences.all.toMap()
        preferences.edit().clear().commit()
        originalNight = shell("cmd uimode night").substringAfter("Night mode: ").trim()
        shell("cmd uimode night no")
        instrumentation.waitForIdleSync()
    }

    @After fun restore() {
        scenario?.close()
        val edit = preferences.edit().clear()
        backup.forEach { (key, value) -> when (value) {
            is String -> edit.putString(key, value)
            is Boolean -> edit.putBoolean(key, value)
            is Int -> edit.putInt(key, value)
            is Long -> edit.putLong(key, value)
            is Float -> edit.putFloat(key, value)
            is Set<*> -> edit.putStringSet(key, value.filterIsInstance<String>().toSet())
        } }
        edit.commit()
        shell("cmd uimode night ${originalNight.takeIf { it in setOf("yes", "no", "auto", "custom") } ?: "no"}")
    }

    @Test fun refreshKeepsFormDraftAndSemanticError() {
        val language = AndroidLanguageStore(context)
        val original = language.read().locale
        try {
            launch()
            clickId(R.id.instance_add_button)
            fields("Draft café", "http://invalid.example")
            clickId(R.id.instance_form_save)
            var nameNode: EditText? = null
            var addressNode: EditText? = null
            scenario!!.onActivity { activity ->
                val sheet = MainActivity::class.java.getDeclaredField("sheet").apply { isAccessible = true }.get(activity) as android.app.Dialog
                nameNode = sheet.findViewById(R.id.instance_form_name)
                addressNode = sheet.findViewById(R.id.instance_form_address)
                nameNode!!.requestFocus()
                nameNode!!.setSelection(3)
            }
            for (locale in listOf("en", "de", "es", "fr", "pt-BR")) {
                assertTrue(language.select(locale, language.read().revision).accepted)
                instrumentation.waitForIdleSync()
                scenario!!.onActivity { activity ->
                    val sheet = MainActivity::class.java.getDeclaredField("sheet").apply { isAccessible = true }.get(activity) as android.app.Dialog
                    assertSame(nameNode, sheet.findViewById<EditText>(R.id.instance_form_name))
                    assertSame(addressNode, sheet.findViewById<EditText>(R.id.instance_form_address))
                    assertEquals("Draft café", nameNode!!.text.toString())
                    assertEquals("http://invalid.example", addressNode!!.text.toString())
                    assertTrue(nameNode!!.hasFocus())
                    assertEquals(3, nameNode!!.selectionStart)
                    val error = sheet.findViewById<ViewGroup>(R.id.instance_form_error).getChildAt(1) as TextView
                    assertEquals(NativeMessages.text(activity, UiMessage("android.instances.errors.httpsRequired")), error.text.toString())
                    val add = activity.findViewById<ViewGroup>(R.id.instance_add_button)
                    val addLabel = NativeMessages.text(activity, UiMessage("android.instances.add"))
                    assertEquals(addLabel, add.contentDescription.toString())
                    assertEquals(addLabel, (add.getChildAt(1) as TextView).text.toString())
                    assertEquals(NativeMessages.text(activity, UiMessage("android.instances.nameLabel")), nameNode!!.hint.toString())
                    assertEquals(NativeMessages.text(activity, UiMessage("common.actions.save")),
                        sheet.findViewById<TextView>(R.id.instance_form_save).text.toString())
                }
            }
            scenario!!.recreate()
            assertEditor("Draft café", "http://invalid.example")
            assertTrue(nodes().any { it.text?.toString() == NativeMessages.text(context, UiMessage("android.instances.errors.httpsRequired")) })
        } finally { language.select(original, language.read().revision) }
    }

    @Test fun nativeEnglishIgnoresFrenchAndroidResources() {
        val language = AndroidLanguageStore(context)
        val original = language.read().locale
        try {
            language.select("en", language.read().revision)
            val configuration = Configuration(context.resources.configuration).apply { setLocale(java.util.Locale.FRENCH) }
            val frenchAndroid = context.createConfigurationContext(configuration)
            assertEquals("Add instance", NativeMessages.text(frenchAndroid, UiMessage("android.instances.add")))
            assertEquals("Cancel", NativeMessages.text(frenchAndroid, UiMessage("common.actions.cancel")))
        } finally { language.select(original, language.read().revision) }
    }

    @Test fun refreshKeepsRemoveConfirmationAndCancelPreservesTheInstance() {
        val saved = InstanceStore(context).save(null, "Saved café", "https://saved.example")
        val language = AndroidLanguageStore(context)
        val original = language.read().locale
        try {
            launch()
            removeAction(saved.name)
            var originalSheet: android.app.Dialog? = null
            scenario!!.onActivity { originalSheet = MainActivity::class.java.getDeclaredField("sheet")
                .apply { isAccessible = true }.get(it) as android.app.Dialog }
            for (locale in listOf("en", "de", "es", "fr", "pt-BR")) {
                language.select(locale, language.read().revision)
                instrumentation.waitForIdleSync()
                scenario!!.onActivity { activity ->
                    val sheet = MainActivity::class.java.getDeclaredField("sheet").apply { isAccessible = true }.get(activity) as android.app.Dialog
                    assertSame(originalSheet, sheet)
                    assertEquals(NativeMessages.text(activity, UiMessage("android.instances.removeTitle", mapOf("name" to saved.name))),
                        sheet.findViewById<TextView>(R.id.instance_remove_title).text.toString())
                }
            }
            clickId(R.id.instance_remove_cancel)
            assertNotNull(InstanceStore(context).find(saved.id))
        } finally { language.select(original, language.read().revision) }
    }

    private fun clickId(id: Int) {
        val resource = context.resources.getResourceName(id)
        waitFor { nodes().any { it.viewIdResourceName == resource } }
        assertTrue(nodes().first { it.viewIdResourceName == resource }.performAction(AccessibilityNodeInfo.ACTION_CLICK))
        instrumentation.waitForIdleSync()
    }

    @Test fun systemThemeChangesPreserveTheEditorAndDoNotOpenTheLastInstance() {
        val saved = InstanceStore(context).save(null, "Saved", "https://saved.example")
        InstanceStore(context).select(saved)
        launch()
        val lightText = titleColor()
        clickId(R.id.instance_add_button)
        fields("Draft laptop", "https://laptop.example")
        night(true)
        assertNotEquals(lightText, titleColor())
        assertEditor("Draft laptop", "https://laptop.example")
        night(false)
        assertEquals(lightText, titleColor())
        assertEditor("Draft laptop", "https://laptop.example")
        clickId(R.id.instance_form_save)
        waitFor { InstanceStore(context).all().any { it.name == "Draft laptop" } }
        assertTrue(InstanceStore(context).all().any { it.address == "https://laptop.example" })
        assertEquals(saved.id, InstanceStore(context).last()!!.id)
        assertTrue(nodes().any { it.text?.toString() == "Draft laptop" })
    }

    @Test fun invalidAndDuplicateAddressesStayEditableAcrossAThemeChange() {
        InstanceStore(context).save(null, "Saved", "https://saved.example")
        launch()
        clickId(R.id.instance_add_button)
        fields("Other", "http://invalid.example")
        clickId(R.id.instance_form_save)
        assertEditor("Other", "http://invalid.example")
        assertEquals(1, InstanceStore(context).all().size)
        fields("Other", "https://saved.example")
        clickId(R.id.instance_form_save)
        waitFor { nodes().any { it.text?.toString() == NativeMessages.text(context, UiMessage("android.instances.errors.duplicate")) } }
        night(true)
        assertEditor("Other", "https://saved.example")
        assertTrue(nodes().any { it.text?.toString() == NativeMessages.text(context, UiMessage("android.instances.errors.duplicate")) })
        assertEquals(1, InstanceStore(context).all().size)
    }

    @Test fun removeConfirmationSurvivesThemeChangeAndCancelPreservesTheInstance() {
        val saved = InstanceStore(context).save(null, "Saved", "https://saved.example")
        launch()
        removeAction("Saved")
        night(true)
        clickId(R.id.instance_remove_cancel)
        assertNotNull(InstanceStore(context).find(saved.id))
        removeAction("Saved")
        clickId(R.id.instance_remove_confirm)
        waitFor { InstanceStore(context).all().isEmpty() }
        val otherEngine = if (BuildConfig.FLAVOR == "webview") "gecko" else "webview"
        assertTrue(InstanceStore(context).pendingProfileDeletions(otherEngine).contains(saved.profileName))
        waitFor { nodes().any { it.text?.toString() == NativeMessages.text(context, UiMessage("android.instances.emptyTitle")) } }
    }

    @Test fun lastUsedInstanceNameStaysReadableOnANarrowScreenWithLargeFonts() {
        val fontScale = shell("settings get system font_scale").trim()
        val densityOverride = Regex("Override density: (\\d+)").find(shell("wm density"))?.groupValues?.get(1)
        val pixels = context.resources.displayMetrics.widthPixels
        try {
            shell("wm density ${pixels / 2}") // 320 dp wide, without resizing the emulator framebuffer.
            shell("settings put system font_scale 2.0")
            val saved = InstanceStore(context).save(null, "My personal server", "https://personal.example")
            InstanceStore(context).select(saved)
            launch()
            scenario!!.onActivity { activity ->
                fun find(view: View): TextView? {
                    if (view is TextView && view.text.toString() == saved.name) return view
                    if (view is ViewGroup) for (index in 0 until view.childCount) find(view.getChildAt(index))?.let { return it }
                    return null
                }
                val name = requireNotNull(find(activity.findViewById(android.R.id.content)))
                assertTrue("Last used must leave space for the instance name", name.width >= activity.dp(64))
            }
            assertTrue(nodes().any { it.contentDescription?.toString() == NativeMessages.text(context, UiMessage("android.instances.editDescription", mapOf("name" to saved.name))) })
            assertTrue(nodes().any { it.contentDescription?.toString() == NativeMessages.text(context, UiMessage("android.instances.removeDescription", mapOf("name" to saved.name))) })
        } finally {
            scenario?.close()
            scenario = null
            shell("wm density ${densityOverride ?: "reset"}")
            shell("settings put system font_scale ${fontScale.toFloatOrNull() ?: 1.0f}")
        }
    }

    private fun launch() {
        scenario = ActivityScenario.launch(Intent(context, MainActivity::class.java).putExtra("manage", true))
        waitFor { nodes().any { it.viewIdResourceName == context.resources.getResourceName(R.id.instance_add_button) } }
    }
    private fun shell(command: String): String = ParcelFileDescriptor.AutoCloseInputStream(
        instrumentation.uiAutomation.executeShellCommand(command)).bufferedReader().use { it.readText() }
    private fun night(dark: Boolean) {
        shell("cmd uimode night ${if (dark) "yes" else "no"}")
        waitFor {
            var matches = false
            scenario!!.onActivity { matches = (it.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) ==
                if (dark) Configuration.UI_MODE_NIGHT_YES else Configuration.UI_MODE_NIGHT_NO }
            matches
        }
        instrumentation.waitForIdleSync()
    }
    private fun titleColor(): Int {
        var color = 0
        scenario!!.onActivity { activity ->
            fun find(view: View): TextView? {
                if (view is TextView && view.id == R.id.instance_title) return view
                if (view is ViewGroup) for (index in 0 until view.childCount) find(view.getChildAt(index))?.let { return it }
                return null
            }
            color = requireNotNull(find(activity.findViewById(android.R.id.content))).currentTextColor
        }
        return color
    }
    private fun nodes(): List<AccessibilityNodeInfo> {
        val result = mutableListOf<AccessibilityNodeInfo>()
        fun walk(node: AccessibilityNodeInfo) {
            result += node
            for (index in 0 until node.childCount) node.getChild(index)?.let(::walk)
        }
        instrumentation.uiAutomation.serviceInfo = instrumentation.uiAutomation.serviceInfo.apply {
            flags = flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
        }
        instrumentation.uiAutomation.rootInActiveWindow?.let(::walk)
        return result
    }
    private fun removeAction(name: String) {
        val description = NativeMessages.text(context, UiMessage("android.instances.removeDescription", mapOf("name" to name)))
        val resource = context.resources.getResourceName(R.id.instance_remove_button)
        waitFor { nodes().any { it.viewIdResourceName == resource && it.contentDescription?.toString() == description } }
        assertTrue(nodes().first { it.viewIdResourceName == resource && it.contentDescription?.toString() == description }
            .performAction(AccessibilityNodeInfo.ACTION_CLICK))
        instrumentation.waitForIdleSync()
    }
    private fun fields(name: String, address: String) {
        val values = listOf(R.id.instance_form_name to name, R.id.instance_form_address to address)
        for ((id, value) in values) {
            val resource = context.resources.getResourceName(id)
            waitFor { nodes().any { it.viewIdResourceName == resource && it.isEditable } }
            assertTrue(nodes().first { it.viewIdResourceName == resource }.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT,
                Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value) }))
        }
        instrumentation.waitForIdleSync()
    }
    private fun assertEditor(name: String, address: String) {
        val resources = listOf(R.id.instance_form_name, R.id.instance_form_address).map(context.resources::getResourceName)
        waitFor { resources.all { resource -> nodes().any { it.viewIdResourceName == resource && it.isEditable } } }
        assertEquals(listOf(name, address), resources.map { resource -> nodes().first { it.viewIdResourceName == resource }.text.toString() })
    }
    private fun waitFor(condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + 10_000
        while (SystemClock.uptimeMillis() < deadline) {
            if (condition()) return
            SystemClock.sleep(80)
        }
        assertTrue("Expected instance manager UI did not appear", condition())
    }
}
