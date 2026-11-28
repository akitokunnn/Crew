package local.crew.android

import android.app.Application
import kotlinx.coroutines.flow.MutableStateFlow
import local.crew.android.core.ServerSessions
import local.crew.android.core.ServerSessionsProvider

class CrewApp : Application(), ServerSessionsProvider {
    internal val computers by lazy { ComputerRepository(this) }
    override val serverSessions: ServerSessions get() = computers
    internal val notificationOpen = MutableStateFlow<NotificationOpen?>(null)
    internal val revoked = MutableStateFlow<local.crew.android.core.PairedComputer?>(null)
}

internal data class NotificationOpen(val instanceId: String, val url: String)
