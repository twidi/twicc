package com.twidi.twicc.tts

import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import java.util.concurrent.Executors
import java.time.Instant
import java.time.format.DateTimeFormatterBuilder

/** Plain Service: listening and settling retain foreground ownership independently of player inactivity. */
class TtsService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    // Release each owned resource before closing its transport. Neither operation blocks the main looper.
    private val workers = Executors.newSingleThreadExecutor()
    private lateinit var notification: TtsNotification
    private lateinit var wakeLock: PowerManager.WakeLock
    private lateinit var owner: TtsServiceOwner
    private val binder = LocalBinder()
    private var foreground = false

    inner class LocalBinder : Binder() {
        fun configure(source: TtsActivationSource): Boolean { checkOwner(); return owner.configure(source) }
        fun detachSource(source: TtsActivationSource) { checkOwner(); owner.detachSource(source) }
        fun dispatch(command: TtsCommand): Acceptance { checkOwner(); return owner.dispatch(command) }
        fun snapshot(): NativeTtsSnapshot { checkOwner(); return owner.snapshot() }
    }
    private fun checkOwner() = check(Looper.myLooper() == Looper.getMainLooper()) { "TTS commands require the service main looper" }
    @androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
    override fun onCreate() {
        super.onCreate()
        notification = TtsNotification(this)
        val power = getSystemService(PowerManager::class.java)
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "TwiCC:NativeTts").apply { setReferenceCounted(false) }
        owner = TtsServiceOwner(object : TtsServicePlatform {
            override fun notificationError() = notification.error()
            override fun foreground(snapshot: NativeTtsSnapshot, epoch: String) {
                // Binding never starts work. The visible activation grants started-service lifetime now.
                startForegroundService(Intent(this@TtsService, TtsService::class.java).setAction(ACTION_ACTIVATE).putExtra(TtsNotification.EXTRA_EPOCH, epoch))
                val types = ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK or
                    if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
                startForeground(TtsNotification.ID, notification.build(snapshot, epoch, owner.instanceName), types)
                foreground = true
            }
            override fun update(snapshot: NativeTtsSnapshot, epoch: String) { notification.update(snapshot, epoch, owner.instanceName) }
            override fun wakeLock(held: Boolean) {
                if (held && !wakeLock.isHeld) wakeLock.acquire()
                if (!held && wakeLock.isHeld) wakeLock.release()
            }
            override fun stop() {
                if (foreground) stopForeground(STOP_FOREGROUND_REMOVE)
                foreground = false
                stopSelf()
            }
            override fun enqueue(block: () -> Unit) { handler.post(block) }
            override fun after(delayMillis: Long, block: () -> Unit) { handler.postDelayed(block, delayMillis) }
            override fun worker(block: () -> Unit) { workers.execute(block) }
        }, outputFactory = { TtsPlayer(this) }, cache = cache, elapsedMs = android.os.SystemClock::elapsedRealtime)
        current = this
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val epoch = intent?.getStringExtra(TtsNotification.EXTRA_EPOCH)
        when (intent?.action) {
            TtsNotification.ACTION_SKIP -> owner.notificationCommand(epoch.orEmpty(), TtsCommand.Skip)
            TtsNotification.ACTION_STOP_ALL -> owner.notificationCommand(epoch.orEmpty(), TtsCommand.StopAll)
            TtsNotification.ACTION_PAUSE_TIMELINE -> owner.notificationCommand(epoch.orEmpty(),
                TtsCommand.PauseTimeline(intent.getStringExtra(TtsNotification.EXTRA_ACTIVATION).orEmpty()))
            TtsNotification.ACTION_RESUME_TIMELINE -> owner.notificationCommand(epoch.orEmpty(),
                TtsCommand.ResumeTimeline(intent.getStringExtra(TtsNotification.EXTRA_ACTIVATION).orEmpty(),
                    DateTimeFormatterBuilder().appendInstant(6).toFormatter().format(Instant.now())))
        }
        // An old PendingIntent can instantiate an idle Service, but it never activates foreground or a session.
        if (!foreground) stopSelfResult(startId)
        return START_NOT_STICKY
    }
    override fun onBind(intent: Intent?): IBinder = binder
    override fun onDestroy() {
        owner.destroy()
        handler.removeCallbacksAndMessages(null)
        workers.shutdown()
        if (current === this) current = null
        super.onDestroy()
    }
    companion object {
        private const val ACTION_ACTIVATE = "com.twidi.twicc.tts.ACTIVATE"
        private val cache = TtsSnapshotCache(SystemClock::elapsedRealtime)
        private var current: TtsService? = null
        private var documentGeneration = 0L
        fun nextDocumentGeneration(): Long = ++documentGeneration
        fun cachedSnapshot(instanceId: String): NativeTtsSnapshot {
            current?.owner?.snapshot()?.takeIf { it.instanceId == instanceId }?.let { return it }
            cache.expire(instanceId)
            return cache.snapshot(instanceId)
        }
        fun isCurrentSession(epoch: String?, instanceId: String): Boolean = epoch != null &&
            current?.owner?.sessionEpoch == epoch && current?.owner?.snapshot()?.instanceId == instanceId
        /** Actual switch/removal/logout hooks run on the Activity's main looper. */
        fun stopForInstance(instanceId: String) {
            current?.let { service ->
                if (service.owner.snapshot().instanceId == instanceId)
                    service.owner.dispatch(TtsCommand.AuthorizationLost("The active instance was closed. Start speech again to continue."))
            }
        }
    }
}
