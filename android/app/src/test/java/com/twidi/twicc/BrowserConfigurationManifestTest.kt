package com.twidi.twicc

import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import org.junit.Assert.assertTrue
import org.junit.Test

class BrowserConfigurationManifestTest {
    @Test fun browserHandlesUserConfigurationChangesWithoutActivityRecreation() {
        val factory = DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }
        val manifest = factory.newDocumentBuilder().parse(File("src/main/AndroidManifest.xml"))
        val activities = manifest.getElementsByTagName("activity")
        val android = "http://schemas.android.com/apk/res/android"
        val browser = (0 until activities.length).map { activities.item(it) as org.w3c.dom.Element }
            .single { it.getAttributeNS(android, "name") == ".BrowserActivity" }
        val handled = browser.getAttributeNS(android, "configChanges").split('|').toSet()
        val required = setOf("orientation", "screenSize", "screenLayout", "smallestScreenSize", "uiMode",
            "keyboard", "keyboardHidden", "navigation", "touchscreen", "density", "fontScale",
            "fontWeightAdjustment", "locale", "layoutDirection", "colorMode", "mcc", "mnc", "grammaticalGender")
        assertTrue("Unhandled changes: ${required - handled}", handled.containsAll(required))
    }
}
