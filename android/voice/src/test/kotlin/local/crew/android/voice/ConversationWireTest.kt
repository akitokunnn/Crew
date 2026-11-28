package local.crew.android.voice

import java.util.concurrent.*
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import local.crew.android.core.ServerSession
import mockwebserver3.*
import okhttp3.OkHttpClient
import okio.BufferedSink
import org.junit.Assert.*
import org.junit.Test

class ConversationWireTest {
    private fun session(server: MockWebServer) = object : ServerSession {
        override val instanceId = "fake"
        override val hostName = "fake"
        override val baseUrl = server.url("/").toString().trimEnd('/')
        override fun cookieHeader() = "isolated"
    }

    @Test fun readsActualLeaderNamesAndSurfacesExpiredPairing() = runBlocking {
        MockWebServer().use { server ->
            server.start()
            val api = CrewVoiceApi(session(server),"甲",OkHttpClient())
            server.enqueue(MockResponse.Builder().body("""{"leaders":[{"crew_name":"前端负责人"}]}""").build())
            assertEquals(listOf("前端负责人"),api.leaderNames())
            server.enqueue(MockResponse.Builder().code(401).body("""{"error":"配对已撤销"}""").build())
            try { api.open(); fail("必须报告配对失效") }
            catch (e: VoiceHttpError) {
                assertEquals(401,e.status)
                assertTrue(e.message!!.contains("配对已撤销"))
            }
        }
    }
    private class Stream : MockResponseBody {
        val frames = LinkedBlockingQueue<String>()
        override val contentLength = 8192L
        fun frame(json: String) {
            val seq = Json.parseToJsonElement(json).jsonObject.string("seq")
            frames.put("id: $seq\ndata: $json\n\n")
        }
        override fun writeTo(sink: BufferedSink) {
            var remaining = contentLength.toInt()
            sink.writeUtf8(": ready\n\n").flush()
            remaining -= 9
            while (true) {
                val data = frames.poll(10,TimeUnit.SECONDS) ?: break
                if (data.isEmpty()) break
                sink.writeUtf8(data).flush()
                remaining -= data.toByteArray().size
            }
            sink.writeUtf8(":" + " ".repeat(remaining-3) + "\n\n").flush()
        }
    }

    @Test fun fullCallConfirmsByTouchAndResumesAtProcessedSeqWithoutResending() = runBlocking {
        val server = MockWebServer()
        val streams = CopyOnWriteArrayList<Stream>()
        val posts = CopyOnWriteArrayList<JsonObject>()
        val requests = CopyOnWriteArrayList<RecordedRequest>()
        val pending = AtomicBoolean(false)
        val project = "手机 甲"
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request
                fun body(value: String) = MockResponse.Builder().body(value).build()
                return when (request.url.encodedPath.substringAfterLast('/')) {
                    "assistant.open" -> body("""{"session_id":"a"}""")
                    "chat.history" -> body("""{"events":[{"seq":7}]}""")
                    "events" -> {
                        val stream = Stream(); streams += stream
                        MockResponse.Builder().setHeader("Content-Type","text/event-stream").body(stream).build()
                    }
                    "chat.send" -> {
                        val data = Json.parseToJsonElement(request.body!!.utf8()).jsonObject
                        posts += data
                        body("""{"accepted":true,"message_id":"%s"}""".format(data.string("message_id")))
                    }
                    "interaction.list" -> body(if (pending.get())
                        """{"requests":[{"id":"card1","summary":"让前端改登录按钮","session_id":"a"},{"id":"other","summary":"其他助手","session_id":"b"}]}"""
                        else """{"requests":[]}""")
                    "interaction.resolve" -> {
                        pending.set(false); body("""{"status":"approved"}""")
                    }
                    "chat.interrupt" -> body("""{"ok":true}""")
                    else -> MockResponse.Builder().code(404).build()
                }
            }
        }
        server.start()
        val session = session(server)
        val cursor = object : CallCursor {
            var value = 0L
            override fun read() = value
            override fun save(seq: Long) { value = seq }
        }
        val notices = mutableListOf<String>()
        val spoken = mutableListOf<Sentence>()
        var flashes = 0
        val output = object : CallOutput {
            override fun changed() = Unit
            override fun stopAudio() = Unit
            override fun revise(id: String) = Unit
            override fun queue(sentence: Sentence) { spoken += sentence }
            override fun history(key: String,user: Boolean,text: String) = Unit
            override fun problem(text: String) = Unit
            override fun notice(text: String) { notices += text }
            override fun flash() { flashes++ }
        }
        val call = VoiceConversation(CrewVoiceApi(session,project,OkHttpClient()),cursor,output)
        suspend fun until(check: () -> Boolean) = withTimeout(6000) { while (!check()) delay(10) }
        val listening = launch { call.listen() }
        try {
            until { call.turn.state == CallState.Listening }
            call.submit("进展怎么样")
            val first = posts.single()
            val user = first.string("message_id")
            assertEquals("voice",first.string("input_mode"))
            assertEquals("a",first.string("session_id"))
            assertNull(first["heard"])
            pending.set(true)
            val a = streams.single()
            a.frame("""{"seq":8,"type":"message","id":"$user"}""")
            a.frame("""{"seq":9,"type":"text","id":"reply","text":"好🙂。下一句。"}""")
            until { spoken.size == 2 && call.cards.size == 1 }
            assertEquals("让前端改登录按钮",call.cards.single().summary)
            assertTrue("列好了，请点屏幕确认" in notices)
            call.turn.sentenceStarted(spoken[0]); call.turn.sentenceFinished(spoken[0])
            call.turn.sentenceStarted(spoken[1])
            repeat(7) { assertFalse(call.turn.voiceWindow(true,32)) }
            assertTrue(call.turn.voiceWindow(true,32))
            call.submit("确认！"); assertEquals(1,flashes)
            assertTrue("请在屏幕上点确认" in notices); assertEquals(1,posts.size)
            a.frames.put("")
            until { streams.size == 2 && call.turn.state == CallState.AwaitingConfirm }
            val b = streams.last()
            b.frame("""{"seq":9,"type":"text","id":"reply","text":"重复。"}""")
            call.submit("先说重点")
            assertEquals(2,posts.size)
            val second = posts.last()
            assertEquals("""{"reply_id":"reply","chars":3}""",second.getValue("heard").toString())
            val secondId = second.string("message_id")
            b.frame("""{"seq":11,"type":"message","id":"$secondId"}""")
            b.frame("""{"seq":12,"type":"assistant","id":"r2","content":[{"type":"tool_use"},{"type":"text","text":"已列好"}]}""")
            b.frame("""{"seq":13,"type":"turn_result","success":true}""")
            until { cursor.read() == 13L }
            assertEquals(listOf("好🙂。","下一句。","已列好"),spoken.map { it.text })
            call.decide("card1",true); call.decide("card1",true)
            assertTrue("已确认执行" in notices); assertTrue(call.cards.isEmpty())
            val paths = requests.groupBy { it.url.encodedPath.substringAfterLast('/') }
            assertEquals(1,paths.getValue("assistant.open").size)
            assertEquals(listOf("7","9"),paths.getValue("events").map { it.url.queryParameter("after") })
            assertEquals(listOf("7","9"),paths.getValue("events").map { it.headers["Last-Event-ID"] })
            assertEquals(listOf(user,secondId),paths.getValue("chat.send").map { it.headers["X-Crew-Request-Id"] })
            assertTrue(requests.all { it.url.queryParameter("project") == project })
            assertEquals("""{"project":"手机 甲","id":"card1","approve":true}""",paths.getValue("interaction.resolve").single().body!!.utf8())
            call.stopReading()
            assertTrue(requests.none { it.url.encodedPath.endsWith("chat.interrupt") })
            call.submit("再讲讲")
            assertNull(posts.last()["heard"])
            call.cancelGeneration()
            call.cancelGeneration()
            assertEquals(1,requests.count { it.url.encodedPath.endsWith("chat.interrupt") })
        } finally {
            listening.cancelAndJoin()
            streams.forEach { it.frames.offer("") }
            server.close()
        }
    }
}
