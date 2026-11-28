package local.crew.android.voice

import java.io.IOException
import java.util.UUID
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.*

interface CallOutput {
    fun changed()
    fun stopAudio()
    fun revise(id: String)
    fun queue(sentence: Sentence)
    fun notice(text: String)
    fun flash()
    fun history(key: String, user: Boolean, text: String)
    fun problem(text: String)
}

interface CallCursor {
    fun read(): Long
    fun save(seq: Long)
}

/** An Activity owns exactly one of these, across foreground/background reconnects. */
class VoiceConversation(private val api: CrewVoiceApi, private val cursor: CallCursor, private val output: CallOutput) {
    val turn = VoiceTurn()
    var cards = emptyList<Confirmation>()
        private set
    private var session: String? = null
    private var journal: ReplyJournal? = null
    private var execution: String? = null
    private var latestReply: String? = null
    private val cardRefresh = Channel<Unit>(Channel.CONFLATED)
    private val cardLock = Mutex()
    private val sending = Mutex()
    private val deciding = mutableSetOf<String>()

    suspend fun listen() = coroutineScope {
        turn.connect()
        output.changed()
        while (isActive) {
            try {
                val id = session ?: api.open().also { session = it }
                val log = journal ?: ReplyJournal(api.tail(id)).also { journal = it; cursor.save(it.seq) }
                coroutineScope {
                    val updates = launch { pollCards(id) }
                    try {
                        api.events(id,cursor.read()).collect { event ->
                            when (event) {
                                is Feed.Open -> {
                                    if (execution != null && execution != event.execution) {
                                        journal = null
                                        stopReading()
                                        turn.cancelGeneration()
                                        throw IOException("对话记录已切换，正在重新连接")
                                    }
                                    execution = event.execution
                                    refresh(id)
                                    turn.connected()
                                    output.changed()
                                }
                                is Feed.Frame -> receive(log,event.data)
                            }
                        }
                    } finally { updates.cancel() }
                }
            } catch (e: IOException) {
                if (e is VoiceHttpError && e.status in 400..499 && e.status !in listOf(408,429)) throw e
                if (journal == null) execution = null
                turn.disconnected()
                output.problem(e.message ?: e.javaClass.simpleName)
                output.changed()
                delay(1200)
            }
        }
    }

    private fun receive(log: ReplyJournal, data: JsonObject) {
        val before = log.seq
        val change = log.apply(data)
        if (before == log.seq) return
        val kind = data.string("type")
        change.revised?.let { id -> turn.revise(id); output.revise(id) }
        if (kind == "text" || kind == "assistant") {
            val reply = data.string("id")
            if (change.reply) {
                latestReply = reply
                output.history(reply,false,log.replies.getValue(reply))
                turn.replyStarted()
            } else if (kind == "text" && data.string("text").isNotBlank() ||
                kind == "assistant" && data.getValue("content").jsonArray.any { it.jsonObject.string("type") == "text" }) {
                latestReply = reply
            }
            if (change.revised != null && !change.reply) output.history(reply,false,"")
        }
        if (change.boundary) {
            stopReading()
            turn.cancelGeneration()
            turn.newBoundary()
        }
        if (!turn.suppressReading) change.speech.forEach(output::queue)
        if (change.finished != null) {
            turn.result()
            if (change.finished != "completed") {
                stopReading()
                output.problem(if (change.finished == "interrupted") "已取消生成" else "助手未能完成回复")
            }
        }
        change.error?.let(output::problem)
        if (kind in setOf("text","assistant","turn_result","tool_result")) cardRefresh.trySend(Unit)
        cursor.save(log.seq)
        output.changed()
    }

    suspend fun submit(text: String) = sending.withLock {
        when (val action = turn.transcript(text)) {
            TranscriptAction.Ignore -> Unit
            TranscriptAction.PointAtCard -> { output.flash(); output.notice("请在屏幕上点确认") }
            is TranscriptAction.Send -> {
                output.stopAudio()
                val id = UUID.randomUUID().toString()
                val heard = action.heard?.takeIf { it.replyId == latestReply }
                val log = checkNotNull(journal)
                log.expect(id)
                output.history(id,true,text)
                output.changed()
                try {
                    api.send(checkNotNull(session),id,action.copy(heard = heard))
                    turn.sent()
                } catch (e: IOException) {
                    turn.failed()
                    output.problem("发送未确认 · " + e.message)
                    if (e is VoiceHttpError && e.status in listOf(401,403)) throw e
                }
            }
        }
        output.changed()
    }

    fun stopReading() { turn.stopReading(); output.stopAudio(); output.changed() }
    suspend fun cancelGeneration() = sending.withLock {
        if (!turn.generating) return@withLock
        api.interrupt(checkNotNull(session))
        turn.cancelGeneration()
        output.stopAudio()
        output.changed()
    }
    private suspend fun pollCards(id: String) {
        while (currentCoroutineContext().isActive) {
            if (cards.isEmpty()) cardRefresh.receive()
            else withTimeoutOrNull(3000L) { cardRefresh.receive() }
            refresh(id)
            // Coalesce text deltas into at most two refreshes per second.
            delay(500)
        }
    }
    private suspend fun refresh(session: String) = cardLock.withLock {
        val next = api.cards(session)
        val newCard = next.any { card -> cards.none { it.id == card.id } }
        cards = next
        turn.hasCards = next.isNotEmpty()
        if (newCard) output.notice("列好了，请点屏幕确认")
        output.changed()
    }
    suspend fun decide(id: String, approve: Boolean) {
        if (cards.none { it.id == id } || !deciding.add(id)) return
        try {
            output.notice(api.resolve(id,approve))
            refresh(checkNotNull(session))
        } finally {
            deciding.remove(id)
            cardRefresh.trySend(Unit)
        }
    }
}
