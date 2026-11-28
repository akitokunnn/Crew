package local.crew.android.notify

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import local.crew.android.CrewApp
import local.crew.android.MainActivity
import local.crew.android.NotificationOpen

class NotificationOpenActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val id = requireNotNull(intent.getStringExtra(INSTANCE))
        val url = requireNotNull(intent.getStringExtra(URL))
        (application as CrewApp).notificationOpen.value = NotificationOpen(id, url)
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP))
        finish()
    }

    companion object {
        const val INSTANCE = "notification_instance"
        const val URL = "notification_url"
    }
}
