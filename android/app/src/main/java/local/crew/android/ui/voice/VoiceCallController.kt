package local.crew.android.ui.voice

import android.content.Context
import android.media.AudioManager
import androidx.compose.runtime.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import local.crew.android.voice.*
import java.io.IOException

data class TranscriptLine(val key: String, val user: Boolean, val text: String, val interrupted: Boolean = false)

class VoiceCallController(
    private val context: Context,
    val project: String,
    val api: CrewVoiceApi,
    cursor: CallCursor,
    private val injected: AudioInput?,
    private val invalidSession: (String) -> Unit,
) : CallOutput {
    private val call = VoiceConversation(api,cursor,this)
    var state by mutableStateOf(CallState.Idle); private set
    var preparing by mutableStateOf(true); private set
    var preparationFailed by mutableStateOf(false); private set
    var error by mutableStateOf(""); private set
    var partial by mutableStateOf(""); private set
    var line by mutableStateOf(""); private set
    var progress by mutableFloatStateOf(0f); private set
    var level by mutableFloatStateOf(0f); private set
    var reading by mutableStateOf(false); private set
    var preparingSentence by mutableStateOf(false); private set
    var generation by mutableStateOf(false); private set
    var muted by mutableStateOf(false); private set
    var speaker by mutableStateOf(true); private set
    var systemVoice by mutableStateOf(false); private set
    var canSwitch by mutableStateOf(false); private set
    var flashCount by mutableIntStateOf(0); private set
    val timeline = mutableStateListOf<TranscriptLine>()
    val diagnostics = mutableStateListOf<String>()
    var cards by mutableStateOf(emptyList<Confirmation>()); private set
    private val captureEnabled = MutableStateFlow(true)
    private val queue = ArrayDeque<Sentence>()
    private val player = SpeechPlayer()
    private var playback: Job? = null
    private var playingId: String? = null
    private var activeSynth: SpeechSynth? = null
    private var visibleScope: CoroutineScope? = null
    private var route: CallAudioRoute? = null
    private var builtIn: SpeechSynth? = null
    private var system: SpeechSynth? = null
    private var preferredSystem: Boolean? = null
    private val names = MutableStateFlow(listOf(project))
    private val synth get() = if (systemVoice) system else builtIn

    override fun changed() {
        if (state == CallState.Offline && call.turn.state != CallState.Offline) error = ""
        state = call.turn.state
        generation = call.turn.generating
        cards = call.cards
    }
    override fun history(key: String, user: Boolean, text: String) {
        val index = timeline.indexOfFirst { it.key == key }
        val item = TranscriptLine(key,user,text,index >= 0 && timeline[index].interrupted)
        if (index < 0) timeline.add(item) else timeline[index] = item
    }
    override fun problem(text: String) { error = text }
    override fun flash() { flashCount++ }
    override fun notice(text: String) {
        val nextReply = queue.indexOfFirst { it.replyId.isNotEmpty() }
        queue.add(if (nextReply == -1) queue.size else nextReply,Sentence("",text))
        startPlayback()
    }
    override fun queue(sentence: Sentence) {
        queue.addLast(sentence)
        startPlayback()
    }
    private fun startPlayback() {
        val scope = visibleScope ?: return
        if (synth == null) return
        if (playback?.isActive == true) return
        playback = scope.launch {
            guarded {
                while (queue.isNotEmpty()) {
                    val sentence = queue.removeFirst()
                    val engine = checkNotNull(synth)
                    activeSynth = engine
                    playingId = sentence.replyId
                    var started = false
                    preparingSentence = true
                    engine.synth(sentence.text).collect { pcm ->
                        if (!started) {
                            started = true
                            reading = true
                            preparingSentence = false
                            if (sentence.replyId.isNotEmpty()) call.turn.sentenceStarted(sentence)
                            else call.turn.noticeStarted()
                            line = sentence.text
                            progress = 0f
                            changed()
                        }
                        player.play(pcm) { fraction, amplitude ->
                            progress = pcm.sentenceStart + fraction * (pcm.sentenceEnd - pcm.sentenceStart)
                            level = amplitude
                        }
                    }
                    if (started && sentence.replyId.isNotEmpty()) call.turn.sentenceFinished(sentence)
                    else if (started) call.turn.noticeFinished()
                    reading = false
                    playingId = null
                    changed()
                }
            }
            reading = false
            preparingSentence = false
            activeSynth = null
            queue.clear()
            if (call.turn.speaking) call.turn.stopReading()
            changed()
        }
    }
    override fun revise(id: String) {
        val remaining = queue.filterNot { it.replyId == id }
        if (playingId == id) stopAudio() else queue.clear()
        queue.addAll(remaining)
        if (queue.isNotEmpty()) startPlayback()
    }
    override fun stopAudio() {
        if (reading) {
            val index = timeline.indexOfFirst { it.key == playingId }
            if (index >= 0) timeline[index] = timeline[index].copy(interrupted = true)
        }
        playingId = null
        player.stop()
        playback?.cancel()
        activeSynth?.cancel()
        activeSynth = null
        queue.clear()
        reading = false
        preparingSentence = false
    }
    fun stopReading() = call.stopReading()
    fun cancelGeneration() = launchAction { call.cancelGeneration() }
    fun decide(id: String, approve: Boolean) = launchAction { call.decide(id,approve) }
    fun toggleMute() {
        muted = !muted
        captureEnabled.value = !muted
        call.turn.mute(muted)
        partial = ""
        changed()
    }
    fun toggleRoute() = launchAction { route?.speaker(!speaker); speaker = !speaker }
    fun toggleVoice() {
        systemVoice = !systemVoice
        preferredSystem = systemVoice
    }
    private fun launchAction(action: suspend () -> Unit) {
        visibleScope?.launch { guarded(action) }
    }
    private suspend fun guarded(action: suspend () -> Unit) {
        try { action() }
        catch (e: VoiceHttpError) {
            if (e.status in listOf(401,403,404)) invalidSession("连接已失效，请重新配对")
            else problem(e.message ?: "电脑请求失败")
        }
        catch (e: IOException) { problem(e.javaClass.simpleName + " · " + e.message) }
        catch (e: CancellationException) { throw e }
        catch (e: IllegalStateException) { problem(e.javaClass.simpleName + " · " + e.message) }
        catch (e: IllegalArgumentException) { problem(e.javaClass.simpleName + " · " + e.message) }
        catch (e: NoSuchElementException) { problem("电脑回复缺少字段 · " + e.message) }
        catch (e: SecurityException) { problem("没有录音或音频权限 · " + e.message) }
        catch (e: UnsupportedOperationException) { problem("设备不支持这项音频操作 · " + e.message) }
        catch (e: OutOfMemoryError) { problem("内存不足，无法准备语音 (" + e.javaClass.simpleName + ")") }
        catch (e: UnsatisfiedLinkError) { problem("语音组件加载失败 · " + e.message) }
    }

    /** repeatOnLifecycle(STARTED) waits for this scope's teardown before restarting. */
    suspend fun visible() {
        var recognizer: Recognition? = null
        preparing = true
        preparationFailed = false
        error = ""
        diagnostics.clear()
        guarded {
            try {
                coroutineScope {
                    visibleScope = this
                    withContext(Dispatchers.Default) {
                        recognizer = Recognition(context.assets,names.value)
                        builtIn = BuiltInSpeech(context.assets)
                    }
                    system = SystemSpeech.discover(context) { diagnostics.add(it) }
                    canSwitch = system != null
                    systemVoice = canSwitch && preferredSystem != false
                    val audio = CallAudioRoute(context.getSystemService(AudioManager::class.java))
                    route = audio
                    audio.speaker(speaker)
                    preparing = false
                    launch { call.listen() }
                    launch {
                        snapshotFlow { state }.first { it !in setOf(CallState.Idle,CallState.Offline,CallState.Connecting) }
                        guarded { names.value = listOf(project) + api.leaderNames() }
                    }
                    if (injected != null)
                        snapshotFlow { state }.first { it == CallState.Listening || it == CallState.AwaitingConfirm }
                    capture(checkNotNull(recognizer))
                }
            } finally {
                visibleScope = null
                stopReading()
                route?.close()
                route = null
                system?.close()
                system = null
                withContext(NonCancellable + Dispatchers.Default) {
                    builtIn?.close()
                    builtIn = null
                    recognizer?.close()
                }
                preparationFailed = currentCoroutineContext().isActive
                preparing = false
            }
        }
    }

    private suspend fun capture(recognizer: Recognition) {
        coroutineScope {
            val owner = this
            captureEnabled.collectLatest { enabled ->
                if (!enabled) return@collectLatest
                var fromSpeaker = false
                var barge = false
                val source = injected ?: Microphone { diagnosis ->
                    owner.launch { diagnostics.removeAll { it.substringBefore(" ·") == diagnosis.substringBefore(" ·") }; diagnostics.add(diagnosis) }
                }
                withContext(Dispatchers.Default) {
                    recognizer.reset()
                    var currentNames: List<String>? = null
                    source.windows().collect { window ->
                        if (currentNames !== names.value) {
                            val latest = names.value
                            recognizer.updateNames(latest)
                            currentNames = latest
                        }
                        val isPlaying = withContext(Dispatchers.Main.immediate) { reading }
                        val frame = recognizer.accept(window,isPlaying)
                        withContext(Dispatchers.Main.immediate) {
                            if (frame.voiced && isPlaying) fromSpeaker = true
                            if (call.turn.voiceWindow(frame.voiced,WINDOW * 1000 / INPUT_RATE)) {
                                barge = true
                                stopAudio()
                                changed()
                            }
                            if (!reading) level = frame.level
                            partial = frame.partial
                            frame.final?.let { text ->
                                if (state in setOf(CallState.Offline,CallState.Connecting) && text.isNotBlank()) {
                                    history(java.util.UUID.randomUUID().toString(),true,"尚未发送 · " + text)
                                } else if (!fromSpeaker || barge || (call.turn.hasCards && VoiceTurn.isConfirmation(text))) {
                                    owner.launch { guarded { call.submit(text) } }
                                }
                                partial = ""
                                fromSpeaker = false
                                barge = false
                            }
                        }
                    }
                }
            }
        }
    }
}
