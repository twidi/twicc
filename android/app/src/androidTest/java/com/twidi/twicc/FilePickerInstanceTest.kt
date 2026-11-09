package com.twidi.twicc

import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.os.Environment
import android.os.SystemClock
import android.provider.MediaStore
import android.view.MotionEvent
import android.view.accessibility.AccessibilityNodeInfo
import androidx.test.platform.app.InstrumentationRegistry
import com.twidi.twicc.browser.BrowserSession
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

/** Real DocumentsUI selections during locale reconciliation, before and after A -> B -> A. */
class FilePickerInstanceTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private val filename = "twicc-file-picker-fixture.txt"

    private inner class Site : AutoCloseable {
        val server = MockWebServer()
        val events = CopyOnWriteArrayList<String>()
        var target = ""
        init {
            val assets = instrumentation.context.assets
            val certificate = HeldCertificate.decode(assets.open("tts-cert.pem").bufferedReader().readText() +
                assets.open("tts-server-key.fixture").bufferedReader().readText())
            server.useHttps(HandshakeCertificates.Builder().heldCertificate(certificate).build().sslSocketFactory(), false)
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val path = request.requestUrl!!.encodedPath
                    if (path == "/event") {
                        val value = request.requestUrl!!.queryParameter("value")!!
                        events += value
                        return MockResponse()
                    }
                    if (path == "/androidAppClient.js" || path == "/androidLocale.js") return MockResponse()
                        .setHeader("Content-Type", "text/javascript")
                        .setBody(assets.open(path.removePrefix("/")).bufferedReader().readText())
                    return MockResponse().setHeader("Content-Type", "text/html").setBody("""<!doctype html>
                        <meta name=viewport content="width=device-width,initial-scale=1">
                        <style>button{position:absolute;left:20px;width:250px;height:64px;font-size:20px}
                        #hidden{display:none}#rendered{position:fixed;width:1px;height:1px;opacity:0;pointer-events:none}</style>
                        <input id=hidden type=file multiple>
                        <input id=rendered type=file multiple>
                        <button style="top:20px" onclick="document.getElementById('hidden').click()">Hidden input</button>
                        <button style="top:110px" onclick="document.getElementById('rendered').click()">Rendered input</button>
                        <button style="top:200px" onclick="switchInstance()">Switch instance</button>
                        <button id=action style="top:290px" type=button>Page action</button>
                        <script type=module>
                        import {createAndroidAppClient} from '/androidAppClient.js';
                        import {createAndroidLocaleController} from '/androidLocale.js';
                        function report(value){fetch('/event?value='+encodeURIComponent(value))}
                        // Cold emulator startup can delay the native extension under host build load.
                        const native=createAndroidAppClient(window,{timeoutMs:10000});
                        window.switchInstance=()=>native.request('switchInstance',{instanceId:'${'$'}{TARGET}'});
                        const client={onStateChanged:listener=>native.onStateChanged(listener),async request(command,args){
                            if(command!=='locale.get')return native.request(command,args);
                            report('locale:busy');
                            try{const value=await native.request(command,args);await new Promise(resolve=>setTimeout(resolve,500));return value}
                            finally{setTimeout(()=>report('locale:idle'),0)}
                        }};
                        const locale=createAndroidLocaleController({client,document,applyLocale:async value=>value,readBrowserLocale:()=> 'en'});
                        // Force the reported resume-before-change ordering without relying on engine timing.
                        addEventListener('change',event=>{if(event.target.type==='file')document.dispatchEvent(new Event('visibilitychange'))},true);
                        const action=document.getElementById('action');
                        const interactions=['click','pointerdown','keydown','change','submit'];
                        for(const type of interactions)action.addEventListener(type,()=>report('action:'+type));
                        for(const id of ['hidden','rendered'])document.getElementById(id).addEventListener('change',e=>{
                            report(id+':'+e.target.files.length);e.target.value='';
                            // Exercise ordinary DOM handlers in the same task as picker completion.
                            for(const type of interactions)action.dispatchEvent(new Event(type,{bubbles:true,cancelable:true}));
                        });
                        locale.initialize().then(state=>report(state.mode==='native'?'ready':'locale:error'));
                        </script>
                    """.replace("${'$'}{TARGET}", target))
                }
            }
            server.start()
        }
        override fun close() = server.shutdown()
    }

    private fun field(activity: BrowserActivity): BrowserSession = BrowserActivity::class.java.getDeclaredField("browser")
        .apply { isAccessible = true }.get(activity) as BrowserSession

    private fun tap(activity: BrowserActivity, y: Int) {
        var x = 0f; var screenY = 0f
        instrumentation.runOnMainSync {
            val view = field(activity).view
            val location = IntArray(2); view.getLocationOnScreen(location)
            val density = context.resources.displayMetrics.density
            x = location[0] + 120 * density; screenY = location[1] + y * density
        }
        val now = SystemClock.uptimeMillis()
        instrumentation.sendPointerSync(MotionEvent.obtain(now, now, MotionEvent.ACTION_DOWN, x, screenY, 0))
        instrumentation.sendPointerSync(MotionEvent.obtain(now, now + 60, MotionEvent.ACTION_UP, x, screenY, 0))
    }

    private fun chooseFixture() {
        waitFor {
            val root = instrumentation.uiAutomation.rootInActiveWindow ?: return@waitFor false
            val candidates = root.findAccessibilityNodeInfosByText(filename)
            val node = candidates.firstOrNull() ?: return@waitFor false
            var clickable: AccessibilityNodeInfo? = node
            while (clickable != null && !clickable.isClickable) clickable = clickable.parent
            clickable?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true
        }
        // A multiple-selection DocumentsUI can require its Open toolbar action.
        val root = instrumentation.uiAutomation.rootInActiveWindow
        for (label in listOf("Open", "OPEN")) root?.findAccessibilityNodeInfosByText(label)?.firstOrNull()
            ?.performAction(AccessibilityNodeInfo.ACTION_CLICK)
    }

    @Test fun realPickerDeliversFilesAfterSwitchingAwayAndBack() {
        prepareBrowserTestProfile()
        val preferences = context.getSharedPreferences("instances", Context.MODE_PRIVATE)
        val backup = preferences.all.toMap()
        val collection = MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        val uri = context.contentResolver.insert(collection, ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, filename)
            put(MediaStore.MediaColumns.MIME_TYPE, "text/plain")
            put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
        })!!
        context.contentResolver.openOutputStream(uri)!!.use { it.write("fixture".toByteArray()) }
        val monitor = instrumentation.addMonitor(BrowserActivity::class.java.name, null, false)
        val opened = mutableListOf<BrowserActivity>()
        try {
            Site().use { a -> Site().use { b ->
                val store = InstanceStore(context)
                val first = store.save(null, "Fixture A", a.server.url("/").toString())
                val second = store.save(null, "Fixture B", b.server.url("/").toString())
                a.target = second.id; b.target = first.id
                context.startActivity(Intent(context, BrowserActivity::class.java).putExtra("instance", first.id)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                var activity = instrumentation.waitForMonitorWithTimeout(monitor, 15_000) as BrowserActivity
                opened += activity
                waitFor { a.events.count { it == "ready" } == 1 }
                for ((site, expectedReady) in listOf(a to 1, b to 1, a to 2)) {
                    waitFor { site.events.count { it == "ready" } == expectedReady }
                    for ((mode, y) in listOf("hidden" to 50, "rendered" to 140)) {
                        waitFor { site.events.count { it == "locale:busy" } == site.events.count { it == "locale:idle" } }
                        val before = site.events.count { it == "$mode:1" }
                        val readsBefore = site.events.count { it == "locale:busy" }
                        tap(activity, y)
                        chooseFixture()
                        waitFor { site.events.count { it == "$mode:1" } == before + 1 }
                        val completions = site.events.count { it == "hidden:1" || it == "rendered:1" }
                        for (type in listOf("click", "pointerdown", "keydown", "change", "submit")) {
                            waitFor { site.events.count { it == "action:$type" } == completions }
                        }
                        // The selected File arrives during reconciliation. Wait before the next user action.
                        waitFor { site.events.count { it == "locale:busy" } > readsBefore &&
                            site.events.count { it == "locale:busy" } == site.events.count { it == "locale:idle" } }
                    }
                    if (site === a && expectedReady == 2) break
                    instrumentation.addMonitor(monitor)
                    tap(activity, 230)
                    activity = instrumentation.waitForMonitorWithTimeout(monitor, 15_000) as BrowserActivity
                    opened += activity
                }
            } }
        } finally {
            instrumentation.runOnMainSync { opened.forEach { if (!it.isDestroyed) it.finish() } }
            instrumentation.removeMonitor(monitor)
            context.contentResolver.delete(uri, null, null)
            preferences.edit().clear().apply {
                backup.forEach { (key, value) -> when (value) {
                    is String -> putString(key, value)
                    is Set<*> -> putStringSet(key, value.filterIsInstance<String>().toSet())
                } }
            }.commit()
        }
    }

    private fun waitFor(check: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 20_000
        while (!check()) {
            assertTrue("File picker round trip timed out", SystemClock.elapsedRealtime() < deadline)
            SystemClock.sleep(100)
        }
    }
}
