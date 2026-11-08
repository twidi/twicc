package com.twidi.twicc.i18n

import android.content.Context
import java.math.BigDecimal
import java.text.NumberFormat
import java.util.Locale
import org.json.JSONObject

object NativeMessages {
    // Read on every render. Headless services need no preceding activity initialization.
    @Volatile var localeProvider: (Context) -> String = { AndroidLanguageStore(it).read().locale }
    @Volatile private var packagedRenderer: MessageRenderer? = null

    fun text(context: Context, message: UiMessage): String =
        text(renderer(context), localeProvider(context), message)

    /** Nullable rendering lets a caller fall back an entire presentation together. */
    fun render(context: Context, message: UiMessage): String? =
        renderer(context).render(localeProvider(context), message)

    private fun renderer(context: Context): MessageRenderer = packagedRenderer ?: synchronized(this) {
        packagedRenderer ?: context.assets.open("i18n/messages.json").bufferedReader().use { input ->
            MessageRenderer(UiMessageJson.decodeCatalog(JSONObject(input.readText())), ::formatNumber)
        }.also { packagedRenderer = it }
    }

    internal fun text(renderer: MessageRenderer, locale: String, message: UiMessage): String =
        renderer.render(locale, message)
            ?: checkNotNull(renderer.render(locale, UiMessage("common.android.errors.actionFailed")))

    /** Decimal strings retain their scale. Numeric values use their ordinary decimal form. */
    fun formatNumber(locale: String, value: Any): String? {
        val decimal = when (value) {
            is String -> {
                if (!value.matches(Regex("-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?"))) return null
                value.toBigDecimalOrNull() ?: return null
            }
            is Number -> {
                if (!value.toDouble().isFinite()) return null
                value.toString().toBigDecimalOrNull()?.stripTrailingZeros() ?: return null
            }
            else -> return null
        }
        val formatter = NumberFormat.getNumberInstance(Locale.forLanguageTag(locale))
        formatter.minimumFractionDigits = if (value is String) decimal.scale().coerceAtLeast(0) else 0
        formatter.maximumFractionDigits = decimal.scale().coerceAtLeast(0)
        val negativeZero = decimal.compareTo(BigDecimal.ZERO) == 0 && value.toString().startsWith("-")
        return formatter.format(if (negativeZero) -0.0 else decimal)
    }
}
