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

    @Test fun systemThemeChangesPreserveTheEditorAndDoNotOpenTheLastInstance() {
        val saved = InstanceStore(context).save(null, "Saved", "https://saved.example")
        InstanceStore(context).select(saved)
        launch()
        val lightText = titleColor()
        click("Add instance")
        fields("Draft laptop", "https://laptop.example")
        night(true)
        assertNotEquals(lightText, titleColor())
        assertEditor("Draft laptop", "https://laptop.example")
        night(false)
        assertEquals(lightText, titleColor())
        assertEditor("Draft laptop", "https://laptop.example")
        click("Save")
        waitFor { InstanceStore(context).all().any { it.name == "Draft laptop" } }
        assertTrue(InstanceStore(context).all().any { it.address == "https://laptop.example" })
        assertEquals(saved.id, InstanceStore(context).last()!!.id)
        assertTrue(nodes().any { it.text?.toString() == "Draft laptop" })
    }

    @Test fun invalidAndDuplicateAddressesStayEditableAcrossAThemeChange() {
        InstanceStore(context).save(null, "Saved", "https://saved.example")
        launch()
        click("Add instance")
        fields("Other", "http://invalid.example")
        click("Save")
        assertEditor("Other", "http://invalid.example")
        assertEquals(1, InstanceStore(context).all().size)
        fields("Other", "https://saved.example")
        click("Save")
        waitFor { nodes().any { it.text?.toString()?.contains("already saved") == true } }
        night(true)
        assertEditor("Other", "https://saved.example")
        assertTrue(nodes().any { it.text?.toString()?.contains("already saved") == true })
        assertEquals(1, InstanceStore(context).all().size)
    }

    @Test fun removeConfirmationSurvivesThemeChangeAndCancelPreservesTheInstance() {
        val saved = InstanceStore(context).save(null, "Saved", "https://saved.example")
        launch()
        removeAction("Saved")
        night(true)
        click("Cancel")
        assertNotNull(InstanceStore(context).find(saved.id))
        removeAction("Saved")
        click("Remove")
        waitFor { InstanceStore(context).all().isEmpty() }
        val otherEngine = if (BuildConfig.FLAVOR == "webview") "gecko" else "webview"
        assertTrue(InstanceStore(context).pendingProfileDeletions(otherEngine).contains(saved.profileName))
        waitFor { nodes().any { it.text?.toString() == "No instances yet" } }
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
            assertTrue(nodes().any { it.contentDescription?.toString() == "Edit ${saved.name}" })
            assertTrue(nodes().any { it.contentDescription?.toString() == "Remove ${saved.name}" })
        } finally {
            scenario?.close()
            scenario = null
            shell("wm density ${densityOverride ?: "reset"}")
            shell("settings put system font_scale ${fontScale.toFloatOrNull() ?: 1.0f}")
        }
    }

    private fun launch() {
        scenario = ActivityScenario.launch(Intent(context, MainActivity::class.java).putExtra("manage", true))
        waitFor { nodes().any {
            it.text?.toString()?.equals("Add instance", ignoreCase = true) == true ||
                it.contentDescription?.toString() == "Add instance"
        } }
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
                if (view is TextView && view.text.toString() in setOf("TwiCC", "TwiCC instances")) return view
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
        instrumentation.uiAutomation.rootInActiveWindow?.let(::walk)
        return result
    }
    private fun click(text: String) {
        waitFor { nodes().any { (it.text?.toString()?.equals(text, ignoreCase = true) == true || it.contentDescription?.toString() == text) && it.isClickable } }
        assertTrue(nodes().first { (it.text?.toString()?.equals(text, ignoreCase = true) == true || it.contentDescription?.toString() == text) && it.isClickable }
            .performAction(AccessibilityNodeInfo.ACTION_CLICK))
        instrumentation.waitForIdleSync()
    }
    private fun removeAction(name: String) {
        waitFor { nodes().any { it.contentDescription?.toString() == "Remove $name" } }
        assertTrue(nodes().first { it.contentDescription?.toString() == "Remove $name" }
            .performAction(AccessibilityNodeInfo.ACTION_CLICK))
        instrumentation.waitForIdleSync()
    }
    private fun fields(name: String, address: String) {
        waitFor { nodes().count { it.isEditable } == 2 }
        for ((index, value) in listOf(name, address).withIndex()) {
            assertTrue(nodes().filter { it.isEditable }[index].performAction(AccessibilityNodeInfo.ACTION_SET_TEXT,
                Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value) }))
        }
        instrumentation.waitForIdleSync()
    }
    private fun assertEditor(name: String, address: String) {
        waitFor { nodes().count { it.isEditable } == 2 }
        assertEquals(listOf(name, address), nodes().filter { it.isEditable }.map { it.text.toString() })
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
