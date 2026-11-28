package local.crew.android.voice

enum class CallState { Idle, Connecting, Listening, Thinking, Speaking, Muted, AwaitingConfirm, Offline }
data class Heard(val replyId: String, val chars: Int)
data class Sentence(val replyId: String, val text: String)
data class Confirmation(val id: String, val summary: String)
sealed interface TranscriptAction {
    data object Ignore : TranscriptAction
    data object PointAtCard : TranscriptAction
    data class Send(val text: String, val heard: Heard?) : TranscriptAction
}

/** All calls come from the conversation owner, on one dispatcher. */
class VoiceTurn(val interruptionMs: Int = 250) {
    private var connection = CallState.Idle
    var muted = false
        private set
    var generating = false
        private set
    var speaking = false
        private set
    var suppressReading = false
        private set
    var hasCards = false
    var hearing = false
        private set
    private var voicedMs = 0
    private var lastPlayed: Heard? = null
    private var pendingHeard: Heard? = null
    private var cancelled = false

    val state: CallState get() = when {
        connection != CallState.Listening -> connection
        muted -> CallState.Muted
        hasCards -> CallState.AwaitingConfirm
        speaking -> CallState.Speaking
        generating && !suppressReading -> CallState.Thinking
        else -> CallState.Listening
    }

    fun connect() { connection = CallState.Connecting }
    fun connected() { connection = CallState.Listening }
    fun disconnected() { connection = CallState.Offline; voiceEnded() }
    fun mute(value: Boolean) { muted = value; voiceEnded() }
    fun voiceEnded() { hearing = false; voicedMs = 0 }

    /** Uses raw consecutive Silero windows, never the endpoint detector's silence hangover. */
    fun voiceWindow(voiced: Boolean, milliseconds: Int): Boolean {
        if (!voiced || muted || connection != CallState.Listening) {
            voiceEnded()
            return false
        }
        voicedMs += milliseconds
        hearing = true
        if (speaking && voicedMs >= interruptionMs) {
            stopReading()
            return true
        }
        return false
    }

    fun transcript(raw: String): TranscriptAction {
        voiceEnded()
        val text = raw.trim()
        if (text.isEmpty() || muted || connection != CallState.Listening) return TranscriptAction.Ignore
        if (hasCards && isConfirmation(text)) return TranscriptAction.PointAtCard
        val carry = pendingHeard
        pendingHeard = null
        lastPlayed = null
        suppressReading = false
        cancelled = false
        generating = true
        return TranscriptAction.Send(text, carry)
    }

    fun sent() { /* Acceptance can arrive after the journal's turn_result. */ }
    fun failed() { generating = false }
    fun replyStarted() { if (!cancelled) generating = true }
    fun sentenceStarted(sentence: Sentence) {
        speaking = true
        if (lastPlayed?.replyId != sentence.replyId) lastPlayed = Heard(sentence.replyId, 0)
    }
    fun sentenceFinished(sentence: Sentence) {
        val start = lastPlayed ?: return
        lastPlayed = start.copy(chars = start.chars + sentence.text.codePointCount(0, sentence.text.length))
        speaking = false
    }
    fun noticeStarted() { speaking = true }
    fun noticeFinished() { speaking = false }
    fun result() { generating = false }
    fun stopReading() {
        pendingHeard = lastPlayed
        suppressReading = true
        speaking = false
    }
    fun cancelGeneration() { stopReading(); generating = false; cancelled = true }
    fun revise(id: String) {
        if (pendingHeard?.replyId == id) pendingHeard = null
        if (lastPlayed?.replyId == id) { lastPlayed = null; speaking = false }
    }
    fun newBoundary() { pendingHeard = null; lastPlayed = null }

    companion object {
        val CONFIRMATIONS = setOf("确认", "好的", "好", "执行", "同意", "可以", "行", "对", "是的", "嗯")
        private val punctuation = Regex("[\\p{P}\\p{Z}\\s]")
        fun isConfirmation(text: String): Boolean {
            val words = text.replace(punctuation, "").trimEnd('啊', '呀', '吧', '啦', '哦', '呢')
            return words in CONFIRMATIONS || words.trimStart('嗯','啊','哦') in CONFIRMATIONS
        }
    }
}
