package local.crew.android.notify

import android.app.Service
import android.content.Intent
import android.os.IBinder

class NotificationService : Service() {
    override fun onBind(intent: Intent): IBinder? = null
}
