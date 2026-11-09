package com.twidi.twicc

import android.content.Context
import com.twidi.twicc.push.AndroidPush
import com.twidi.twicc.tts.TtsNotification

/** Refresh persisted category names without posting notifications or starting speech. */
object NotificationChannels {
    fun refresh(context: Context) {
        AndroidPush.manager(context)
        TtsNotification.refreshChannel(context)
    }
}
