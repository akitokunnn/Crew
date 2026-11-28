package local.crew.android.ui.voice

import local.crew.android.core.ServerSessionsProvider

class VoiceActivity : VoiceCallActivity() {
    override val sessionLookup: SessionLookup
        get() = SessionLookup((application as ServerSessionsProvider).serverSessions::byInstance)
}
