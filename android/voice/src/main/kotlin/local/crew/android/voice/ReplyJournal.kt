package local.crew.android.voice

import kotlinx.serialization.json.*

internal fun JsonObject.string(key: String) = getValue(key).jsonPrimitive.content

data class JournalChange(
    val speech: List<Sentence> = emptyList(),
    val reply: Boolean = false,
    val boundary: Boolean = false,
    val finished: String? = null,
    val error: String? = null,
    val revised: String? = null,
)

/** Source offsets refer to the complete snapshot, even if the spoken text omits markup. */
class ReplyJournal(var seq: Long) {
    var expected: String? = null
        private set
    private var inside = false
    private var ownsPlayback = false
    val replies = linkedMapOf<String, String>()
    private val offsets = mutableMapOf<String, Int>()
    fun expect(message: String) {
        expected = message
        inside = false
        replies.clear()
        offsets.clear()
    }

    fun apply(frame: JsonObject): JournalChange {
        val next = frame.getValue("seq").jsonPrimitive.long
        if (next <= seq) return JournalChange()
        val change = consume(frame)
        seq = next
        return change
    }

    private fun consume(frame: JsonObject): JournalChange {
        val kind = frame.string("type")
        if (kind == "message") {
            val matches = frame.string("id") == expected
            val foreign = (inside || (expected == null && ownsPlayback)) && !matches
            inside = matches
            ownsPlayback = matches
            if (foreign) expected = null
            return JournalChange(boundary = foreign)
        }
        if (!inside) return JournalChange()
        if (kind == "turn_result") {
            val status = if (frame.getValue("success").jsonPrimitive.boolean) "completed"
                else frame["status"]?.jsonPrimitive?.content ?: "failed"
            inside = false
            expected = null
            return JournalChange(if (status == "completed") replies.keys.flatMap { extract(it, true) } else emptyList(), finished = status)
        }
        if (kind == "error") return JournalChange(error = frame.string("text"))
        if (kind != "text" && kind != "assistant") return JournalChange()
        val id = frame.string("id")
        val old = replies[id].orEmpty()
        val updated = if (kind == "text") old + frame.string("text")
            else frame.getValue("content").jsonArray.map { it.jsonObject }
                .filter { it.string("type") == "text" }.joinToString("") { it.string("text") }
        val flush = if (id !in replies) replies.keys.lastOrNull()?.let { extract(it, true) }.orEmpty() else emptyList()
        replies[id] = updated
        val queuedEnd = offsets[id] ?: 0
        val revised = kind == "assistant" && !updated.regionMatches(0,old,0,queuedEnd)
        if (revised) offsets[id] = 0
        return JournalChange(flush + extract(id, false), reply = updated.isNotBlank(), revised = id.takeIf { revised })
    }

    private fun extract(id: String, final: Boolean): List<Sentence> {
        val (end, sentences) = SpeechSentences.split(replies.getValue(id), offsets[id] ?: 0, final)
        offsets[id] = end
        return sentences.map { Sentence(id, it) }
    }
}

object SpeechSentences {
    private val pairs = mapOf('(' to ')', '（' to '）', '[' to ']', '【' to '】', '“' to '”', '‘' to '’', '"' to '"', '《' to '》')
    private val omitted = Regex("""(?s)\x60{3}.*?(?:\x60{3}|$)|\[([^\]]*)\]\([^\s)]*\)|https?://[^\s，。！？；“”]+""")
    private val url = Regex("""https?://[^\s，。！？；“”()（）\[\]]+""")
    private val markdownLink = Regex("""\[[^\]]*\]\([^\s)]*\)""")
    fun split(source: String, start: Int, final: Boolean): Pair<Int,List<String>> {
        val out = mutableListOf<String>()
        val stack = ArrayDeque<Char>()
        var offset = start
        var i = start
        var pending = false
        fun emit(end: Int) {
            val spoken = source.substring(offset,end).replace(omitted, "").trim()
            if (spoken.isNotEmpty()) out += spoken
            offset = end
            pending = false
        }
        while (i < source.length) {
            if (source[i] == '[') {
                val markdown = markdownLink.matchAt(source,i)
                if (markdown != null) {
                    i = markdown.range.last + 1
                    continue
                }
            }
            if (source.startsWith("\u0060\u0060\u0060", i)) {
                val close = source.indexOf("\u0060\u0060\u0060", i+3)
                if (close < 0) break
                i = close + 3
                continue
            }
            val link = if (source.startsWith("http", i)) url.matchAt(source, i) else null
            if (link != null) {
                i = link.range.last + 1
                continue
            }
            val c = source[i]
            val closes = stack.lastOrNull() == c
            if (closes) stack.removeLast() else pairs[c]?.let { stack.addLast(it) }
            val englishEnd = c in ".!?" && (source.getOrNull(i+1)?.isWhitespace() == true || (final && i+1 == source.length))
            if (c in "。！？；…\n" || englishEnd) pending = true
            else if (!closes && !c.isWhitespace()) pending = false
            i++
            if (pending && stack.isEmpty()) emit(i)
        }
        if (final && offset < source.length) emit(source.length)
        return offset to out
    }
}
