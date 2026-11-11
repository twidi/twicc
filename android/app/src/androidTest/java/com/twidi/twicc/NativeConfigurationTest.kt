package com.twidi.twicc

import android.content.Context
import android.content.res.Configuration
import android.content.res.Resources
import android.view.ContextThemeWrapper
import android.widget.EditText
import android.widget.LinearLayout
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test

class NativeConfigurationTest {
    private class ChangingContext(private val source: Context) : ContextThemeWrapper(source, R.style.AppTheme) {
        var configuration = Configuration(source.resources.configuration)
        override fun getResources(): Resources = source.createConfigurationContext(configuration).resources
    }

    @Test fun nativeDimensionsAndTextScaleWithoutDriftOrInputLoss() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        instrumentation.runOnMainSync {
            val context = ChangingContext(instrumentation.targetContext)
            val original = Configuration(context.configuration)
            val density = context.resources.displayMetrics.density
            val editor = EditText(context).apply {
                layoutParams = LinearLayout.LayoutParams((120 * density).toInt(), -2)
                setPadding((12 * density).toInt(), 0, 0, 0)
                textSize = 18f
                setText("unfinished input")
                setSelection(2, 7)
            }
            val width = editor.layoutParams.width
            val padding = editor.paddingLeft
            val textSize = editor.textSize
            val configuration = NativeConfiguration()
            configuration.remember(editor)
            repeat(3) {
                context.configuration = Configuration(original).apply {
                    densityDpi *= 2
                    fontScale = 1.5f
                }
                configuration.update(editor)
                assertEquals(width * 2, editor.layoutParams.width)
                assertEquals(padding * 2, editor.paddingLeft)
                assertTrue(editor.textSize > textSize * 2)
                context.configuration = Configuration(original)
                configuration.update(editor)
                assertEquals(width, editor.layoutParams.width)
                assertEquals(padding, editor.paddingLeft)
                assertEquals(textSize, editor.textSize, 0.1f)
                assertEquals("unfinished input", editor.text.toString())
                assertEquals(2, editor.selectionStart)
                assertEquals(7, editor.selectionEnd)
            }
        }
    }
}
