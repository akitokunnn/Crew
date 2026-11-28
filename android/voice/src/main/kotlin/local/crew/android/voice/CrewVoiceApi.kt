package local.crew.android.voice

import java.io.IOException
import java.util.UUID
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.buffer
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.json.*
import local.crew.android.core.ServerSession
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.sse.*

class VoiceHttpError(val status: Int, reason: String) : IOException("$status · $reason")
sealed interface Feed {
    data class Open(val execution: String) : Feed
    data class Frame(val data: JsonObject) : Feed
}

class CrewVoiceApi(session: ServerSession, val project: String, client: OkHttpClient) {
    private val base = session.baseUrl.toHttpUrl()
    // A broken response is ambiguous. Reconnect the journal, never replay speech or decisions.
    private val http = client.newBuilder().retryOnConnectionFailure(false).build()
    private fun request(path: String, query: Map<String,String> = emptyMap()): Request.Builder {
        val url = base.newBuilder().addPathSegments(path).addQueryParameter("project", project)
        query.forEach { (k,v) -> url.addQueryParameter(k,v) }
        return Request.Builder().url(url.build())
    }

    private suspend fun json(request: Request): JsonObject = suspendCancellableCoroutine { continuation ->
        val call = http.newCall(request)
        continuation.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) { continuation.resumeWithException(e) }
            override fun onResponse(call: Call, response: Response) {
                response.use {
                    try {
                        val body = it.body.string()
                        if (!it.isSuccessful) {
                            val reason = if (body.trimStart().startsWith("{"))
                                Json.parseToJsonElement(body).jsonObject["error"]?.jsonPrimitive?.content else null
                            throw VoiceHttpError(it.code, (reason ?: body).take(400))
                        }
                        continuation.resume(Json.parseToJsonElement(body).jsonObject)
                    } catch (e: IOException) { continuation.resumeWithException(e) }
                    catch (e: IllegalArgumentException) { continuation.resumeWithException(IOException("电脑返回的数据无法读取", e)) }
                }
            }
        })
    }

    private suspend fun post(op: String, body: JsonObject, id: String = UUID.randomUUID().toString()) =
        json(request("api/op/$op").header("X-Crew-Request-Id",id)
            .post(body.toString().toRequestBody("application/json; charset=utf-8".toMediaType())).build())

    suspend fun open() = post("assistant.open", buildJsonObject { put("project", project) }).string("session_id")
    suspend fun leaderNames(): List<String> = json(request("api/op/assistant.state").build())
        .getValue("leaders").jsonArray.map { it.jsonObject.string("crew_name") }
    suspend fun tail(session: String): Long {
        val history = json(request("api/op/chat.history", mapOf("session_id" to session, "tail" to "1")).build())
        return history.getValue("events").jsonArray.lastOrNull()?.jsonObject?.getValue("seq")?.jsonPrimitive?.long ?: 0
    }

    suspend fun send(session: String, messageId: String, input: TranscriptAction.Send) {
        val result = post("chat.send",buildJsonObject {
            put("session_id",session); put("text",input.text); put("message_id",messageId); put("input_mode","voice")
            input.heard?.let { putJsonObject("heard") { put("reply_id",it.replyId); put("chars",it.chars) } }
        }, messageId)
        if (!result.getValue("accepted").jsonPrimitive.boolean || result.string("message_id") != messageId)
            throw IOException("电脑没有确认收到这句话")
    }

    suspend fun interrupt(session: String) {
        post("chat.interrupt", buildJsonObject { put("session_id",session) })
    }
    suspend fun cards(session: String) = json(request("api/op/interaction.list").build()).getValue("requests").jsonArray
        .map { it.jsonObject }.filter { it.string("session_id") == session }
        .map { Confirmation(it.string("id"), it.string("summary")) }
    suspend fun resolve(id: String, approve: Boolean): String {
        val record = post("interaction.resolve",buildJsonObject { put("project",project); put("id",id); put("approve",approve) })
        return when (record.string("status")) {
            "approved" -> "已确认执行"
            "rejected" -> "已取消"
            "failed" -> "执行失败，" + record.string("error")
            else -> throw IOException("电脑返回了未知的确认状态")
        }
    }

    fun events(session: String, after: Long) = callbackFlow {
        val request = request("api/chat/events",mapOf("session_id" to session, "after" to after.toString()))
            .header("Last-Event-ID", after.toString()).build()
        val streaming = http.newBuilder().readTimeout(30, java.util.concurrent.TimeUnit.SECONDS).build()
        val source = EventSources.createFactory(streaming).newEventSource(request, object : EventSourceListener() {
            override fun onOpen(eventSource: EventSource, response: Response) {
                trySend(Feed.Open(response.header("X-Crew-Execution-Id").orEmpty()))
            }
            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                try { trySend(Feed.Frame(Json.parseToJsonElement(data).jsonObject)) }
                catch (e: IllegalArgumentException) { close(IOException("回复格式错误",e)) }
            }
            override fun onClosed(eventSource: EventSource) { close(IOException("电脑连接已关闭")) }
            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                close(if (response != null && !response.isSuccessful) VoiceHttpError(response.code,response.message)
                    else if (t is VoiceHttpError) t else IOException("电脑连接中断", t))
            }
        })
        awaitClose { source.cancel() }
    }.buffer(Channel.UNLIMITED)
}
