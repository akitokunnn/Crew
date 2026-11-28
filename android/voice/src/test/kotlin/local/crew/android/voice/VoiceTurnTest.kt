package local.crew.android.voice

import org.junit.Assert.*
import org.junit.Test
import kotlinx.serialization.json.*

class VoiceTurnTest {
    @Test fun connectionAndPlaybackAreIndependent() {
        val t = VoiceTurn()
        assertEquals(CallState.Idle,t.state)
        t.connect(); assertEquals(CallState.Connecting,t.state)
        t.connected(); assertEquals(CallState.Listening,t.state)
        t.voiceWindow(true,32); assertTrue(t.hearing)
        t.voiceEnded(); assertFalse(t.hearing)
        t.transcript("你好"); assertEquals(CallState.Thinking,t.state)
        t.sent(); t.replyStarted()
        val s = Sentence("r","Hello你好🙂。")
        t.sentenceStarted(s); assertEquals(CallState.Speaking,t.state)
        t.result(); t.sent(); assertEquals(CallState.Speaking,t.state)
        t.sentenceFinished(s); assertEquals(CallState.Listening,t.state)
        t.hasCards = true; assertEquals(CallState.AwaitingConfirm,t.state)
        t.mute(true); assertEquals(CallState.Muted,t.state)
        assertSame(TranscriptAction.Ignore,t.transcript("你好"))
        t.disconnected(); assertEquals(CallState.Offline,t.state)
        t.connected(); assertEquals(CallState.Muted,t.state)
        t.mute(false); assertEquals(CallState.AwaitingConfirm,t.state)
        t.hasCards = false; assertEquals(CallState.Listening,t.state)
        t.transcript("开始"); t.failed(); assertEquals(CallState.Listening,t.state)
        assertSame(TranscriptAction.Ignore,t.transcript("  "))
    }
    @Test fun interruptionCountsOnlyCompletelyPlayedSentencesInCodePointsOnce() {
        val t = VoiceTurn(250)
        t.connected(); t.transcript("开始")
        val a = Sentence("a","Hello你好🙂。")
        t.sentenceStarted(a); t.sentenceFinished(a)
        t.sentenceStarted(Sentence("a","还没念完"))
        assertFalse(t.voiceWindow(true,200)); assertFalse(t.voiceWindow(false,100))
        assertFalse(t.voiceWindow(true,249)); assertTrue(t.voiceWindow(true,1))
        assertTrue(t.suppressReading); assertTrue(t.generating); assertFalse(t.speaking)
        assertSame(TranscriptAction.Ignore,t.transcript(""))
        assertEquals(CallState.Listening,t.state)
        t.hasCards = true
        assertSame(TranscriptAction.PointAtCard,t.transcript("嗯"))
        assertEquals(Heard("a",9),(t.transcript("先说重点") as TranscriptAction.Send).heard)
        assertNull((t.transcript("继续") as TranscriptAction.Send).heard)
        t.cancelGeneration(); assertFalse(t.generating)
    }
    @Test fun shortConfirmationsOnlyWhileACardIsPending() {
        val t = VoiceTurn()
        t.connected(); t.hasCards = true
        for (word in VoiceTurn.CONFIRMATIONS + listOf("好，的呀。","确认吧！","嗯，好的。"))
            assertSame(word,TranscriptAction.PointAtCard,t.transcript(word))
        for (text in listOf("确认一下进度","好，先等等","不可以","取消","嗯，我还有事"))
            assertTrue(t.transcript(text) is TranscriptAction.Send)
        t.hasCards = false
        assertTrue(t.transcript("好的") is TranscriptAction.Send)
    }
    @Test fun markdownQuotesEnglishAndStreamingTail() {
        val fence = "\u0060".repeat(3)
        val source = "先看（“第一句。还有。”）再说；Next! Then? End. 好…\n" +
            fence+"kt\nprint(\"不能读。\")\n"+fence+"\n查 https://crew.example/a.b 然后看[链接](https://crew.example)。尾巴"
        val (offset,spoken) = SpeechSentences.split(source,0,false)
        assertEquals(listOf("先看（“第一句。还有。”）","再说；","Next!","Then?","End.","好…","查  然后看。"),spoken)
        assertEquals(listOf("尾巴"),SpeechSentences.split(source,offset,true).second)
        assertTrue(SpeechSentences.split("这是（未完。",0,false).second.isEmpty())
        assertTrue(SpeechSentences.split("End.",0,false).second.isEmpty())
        assertEquals(listOf("End."),SpeechSentences.split("End. ",0,false).second)
        assertEquals(listOf("前面"),SpeechSentences.split("前面"+fence+"code。",0,true).second)
    }
    @Test fun boundariesSnapshotsFailuresAndDuplicateSequence() {
        val j = ReplyJournal(9)
        fun frame(json: String) = j.apply(Json.parseToJsonElement(json).jsonObject)
        j.expect("u")
        assertFalse(frame("""{"seq":10,"type":"text","id":"old","text":"旧的。"}""").reply)
        frame("""{"seq":12,"type":"message","role":"user","id":"u"}""")
        assertEquals(listOf(Sentence("r","开始。")),frame("""{"seq":13,"type":"text","id":"r","text":"开始。旧尾"}""").speech)
        val snapshot = """{"seq":14,"type":"assistant","id":"r","content":[{"type":"tool_use"},{"type":"text","text":"开始。新尾"}]}"""
        frame(snapshot); assertEquals("开始。新尾",j.replies["r"]); assertEquals(JournalChange(),frame(snapshot))
        assertEquals(listOf(Sentence("r","新尾"),Sentence("b","好了！")),frame("""{"seq":16,"type":"text","id":"b","text":"好了！"}""").speech)
        assertEquals("interrupted",frame("""{"seq":17,"type":"turn_result","status":"interrupted","success":false}""").finished)
        j.expect("v")
        frame("""{"seq":18,"type":"message","id":"v"}""")
        assertEquals("出错",frame("""{"seq":19,"type":"error","text":"出错"}""").error)
        assertEquals("failed",frame("""{"seq":20,"type":"turn_result","status":"failed","success":false}""").finished)
        j.expect("x"); frame("""{"seq":21,"type":"message","id":"x"}""")
        assertTrue(frame("""{"seq":22,"type":"message","id":"web"}""").boundary)
        assertFalse(frame("""{"seq":25,"type":"text","id":"web-reply","text":"别人的。"}""").reply)
        assertEquals(25L,j.seq)
    }
    @Test fun replacementInvalidatesQueuedAudioButAnIdenticalSnapshotDoesNot() {
        val j = ReplyJournal(0)
        fun frame(data: String) = j.apply(Json.parseToJsonElement(data).jsonObject)
        j.expect("u")
        frame("""{"seq":1,"type":"message","id":"u"}""")
        frame("""{"seq":2,"type":"text","id":"r","text":"旧结论。"}""")
        val changed = frame("""{"seq":3,"type":"assistant","id":"r","content":[{"type":"text","text":"新结论。"}]}""")
        assertEquals("r",changed.revised)
        assertEquals(listOf(Sentence("r","新结论。")),changed.speech)
        val same = frame("""{"seq":4,"type":"assistant","id":"r","content":[{"type":"text","text":"新结论。"}]}""")
        assertNull(same.revised)
        assertTrue(same.speech.isEmpty())
    }
}
