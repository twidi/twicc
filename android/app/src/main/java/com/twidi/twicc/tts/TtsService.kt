package com.twidi.twicc.tts

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder

/** Foreground ownership begins at a visible user activation, before credential export or network waits. */
class TtsService : Service() {
    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Live timeline speech", NotificationManager.IMPORTANCE_LOW))
        val notification = Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_lock_silent_mode_off)
            .setContentTitle("TwiCC speech")
            .setContentText("Waiting for assistant messages")
            .setOngoing(true).build()
        val types = ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK or
            if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
        startForeground(1001, notification, types)
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int) = START_NOT_STICKY
    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL = "twicc-tts"
        /** Call only from the current visible user action. Never restart after process death. */
        fun start(context: Context) { context.startForegroundService(Intent(context, TtsService::class.java)) }
    }
}
