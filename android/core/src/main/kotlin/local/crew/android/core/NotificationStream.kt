package local.crew.android.core

import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import okhttp3.MediaType
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import okio.Buffer
import okio.ForwardingSource
import okio.buffer

@Serializable
sealed interface NotificationFrame {
    @Serializable @SerialName("ready")
    data class Ready(val seq: Long) : NotificationFrame

    @Serializable @SerialName("notification")
    data class Item(
        val seq: Long,
        val item_id: String,
        val kind: String,
        val project: String?,
        val title: String,
        val body: String,
        val url: String,
    ) : NotificationFrame

    @Serializable @SerialName("gap")
    data class Gap(val oldest: Long) : NotificationFrame
}

class NotificationStream(session: ServerSession) {
    private val origin = session.baseUrl
    private val client = CrewHttp.client(session).newBuilder()
        .readTimeout(40, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false)
        .build()
    private val json = Json { ignoreUnknownKeys = true; classDiscriminator = "type" }

    suspend fun run(
        lastSeq: Long?,
        processed: suspend (NotificationFrame) -> Unit,
        saveCursor: suspend (Long) -> Unit,
        connection: (Boolean) -> Unit,
        disconnected: (Throwable) -> Unit,
        revoked: suspend () -> Unit,
    ) {
        var cursor = lastSeq
        var attempt = 0
        while (currentCoroutineContext().isActive) {
            val frames = Channel<WireFrame>(Channel.UNLIMITED)
            val request = Request.Builder().url("$origin/api/notifications/events" +
                (cursor?.let { "?after=$it" } ?: ""))
            cursor?.let { request.header("Last-Event-ID", it.toString()) }
            val source = connect(request.build(), frames)
            try {
                for (wire in frames) {
                    when (wire) {
                        WireFrame.Open -> connection(true)
                        WireFrame.Heartbeat -> attempt = 0
                        is WireFrame.Data -> {
                            val frame = json.decodeFromString<NotificationFrame>(wire.data)
                            attempt = 0
                            val next = when (frame) {
                                is NotificationFrame.Ready -> frame.seq
                                is NotificationFrame.Item -> frame.seq
                                is NotificationFrame.Gap -> frame.oldest - 1
                            }
                            if (cursor != null && next <= cursor) continue
                            processed(frame)
                            saveCursor(next)
                            cursor = next
                        }
                    }
                }
            } catch (error: CrewResponseException) {
                if (error.status == 401) {
                    revoked()
                    return
                }
                disconnected(error)
            } catch (error: IOException) {
                disconnected(error)
            } catch (error: SerializationException) {
                disconnected(error)
            } finally {
                source.cancel()
                frames.cancel()
            }
            connection(false)
            delay(retryDelayMs(attempt))
            attempt = (attempt + 1).coerceAtMost(RETRY_SECONDS.lastIndex)
        }
    }

    private fun connect(request: Request, frames: Channel<WireFrame>): EventSource {
        val observing = client.newBuilder().addInterceptor { chain ->
            val response = chain.proceed(chain.request())
            if (response.isSuccessful && response.body.contentType()?.subtype == "event-stream") {
                response.newBuilder().body(HeartbeatBody(response.body) {
                    frames.trySend(WireFrame.Heartbeat)
                }).build()
            } else response
        }.build()
        return EventSources.createFactory(observing).newEventSource(request, object : EventSourceListener() {
            override fun onOpen(eventSource: EventSource, response: Response) {
                frames.trySend(WireFrame.Open)
            }

            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                frames.trySend(WireFrame.Data(data))
            }

            override fun onClosed(eventSource: EventSource) {
                frames.close(IOException("电脑关闭了提醒连接"))
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                var failure = t
                if (response != null && !response.isSuccessful) {
                    try {
                        response.requireSuccess()
                    } catch (error: IOException) {
                        failure = error
                    }
                }
                frames.close(failure ?: IOException("提醒连接失败"))
            }
        })
    }

    private sealed interface WireFrame {
        data object Open : WireFrame
        data object Heartbeat : WireFrame
        data class Data(val data: String) : WireFrame
    }

    companion object {
        private val RETRY_SECONDS = longArrayOf(5, 10, 15, 20, 30, 45, 60, 120)

        internal fun retryDelayMs(attempt: Int): Long =
            1000 * RETRY_SECONDS[attempt.coerceAtMost(RETRY_SECONDS.lastIndex)]
    }
}

/** okhttp-sse consumes comment lines without calling its listener. Observe those lines in transit. */
private class HeartbeatBody(private val body: ResponseBody, heartbeat: () -> Unit) : ResponseBody() {
    private val observed = object : ForwardingSource(body.source()) {
        private var lineStart = true
        private var comment = false

        override fun read(sink: Buffer, byteCount: Long): Long {
            val offset = sink.size
            val count = super.read(sink, byteCount)
            for (i in offset until offset + count) {
                val byte = sink[i]
                if (lineStart) {
                    comment = byte == ':'.code.toByte()
                    lineStart = false
                }
                if (byte == '\n'.code.toByte()) {
                    if (comment) heartbeat()
                    lineStart = true
                }
            }
            return count
        }
    }.buffer()

    override fun contentType(): MediaType? = body.contentType()
    override fun contentLength(): Long = body.contentLength()
    override fun source() = observed
}
