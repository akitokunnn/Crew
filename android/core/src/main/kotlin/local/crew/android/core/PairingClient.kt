package local.crew.android.core

import java.io.IOException
import java.net.Proxy
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.Cookie
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okio.BufferedSink

@Serializable
data class PairedComputer(
    val instanceId: String,
    val hostName: String,
    val baseUrl: String,
    val deviceId: String,
    val cookieName: String,
    val pairedAt: Long,
    val lastOkAt: Long,
)

interface ServerSessionsProvider {
    val serverSessions: ServerSessions
}

data class PairingResult(val computer: PairedComputer, val cookie: Cookie) {
    override fun toString(): String = "PairingResult(computer=$computer)"
}

@Serializable
data class AccessContext(val device_id: String, val host_name: String, val instance_id: String)

class CrewResponseException(val status: Int, message: String) : IOException(message)

private val responseJson = Json { ignoreUnknownKeys = true }

internal fun Response.requireSuccess() {
    if (isSuccessful) return
    val raw = body.string()
    val message = try {
        responseJson.decodeFromString<AccessError>(raw).error
    } catch (error: SerializationException) {
        "电脑返回了错误（$code）"
    }
    throw CrewResponseException(code, message)
}

@Serializable
private data class AccessError(val error: String)

class PairingClient(private val allowLoopback: Boolean = false) {
    private val firstRequest = OkHttpClient.Builder()
        .proxy(Proxy.NO_PROXY)
        .followRedirects(false)
        .followSslRedirects(false)
        .retryOnConnectionFailure(false)
        .callTimeout(8, TimeUnit.SECONDS)
        .build()

    suspend fun pair(candidateBaseUrl: String, code: String, deviceName: String): PairingResult =
        withContext(Dispatchers.IO) {
            require(candidateBaseUrl.startsWith("http://")) { "请使用电脑的本地连接地址" }
            val address = candidateBaseUrl.removePrefix("http://")
            val baseUrl = PairLink.origin(if (':' in address) address else "$address:80", allowLoopback)
            require(code.matches(Regex("[0-9]{8}"))) { "请输入 8 位数字配对码" }
            val body = buildJsonObject {
                put("kind", "browser")
                put("code", code)
                put("device_name", deviceName)
            }
            val payload = body.toString().encodeToByteArray()
            val request = Request.Builder()
                .url("$baseUrl/remote/login")
                .header("Origin", baseUrl)
                .header("X-Crew", "1")
                .header("Content-Type", "application/json")
                .post(object : RequestBody() {
                    override fun contentType() = null
                    override fun contentLength() = payload.size.toLong()
                    override fun isOneShot() = true
                    override fun writeTo(sink: BufferedSink) { sink.write(payload) }
                })
                .build()
            val cookie = firstRequest.newCall(request).execute().use { response ->
                response.requireSuccess()
                Cookie.parseAll(request.url, response.headers).singleOrNull()
                    ?: throw IOException("电脑没有返回配对凭据")
            }
            val temporarySession = object : ServerSession {
                override val instanceId = ""
                override val hostName = ""
                override val baseUrl = request.url.toHttpOrigin()
                override fun cookieHeader() = "${cookie.name}=${cookie.value}"
            }
            val context = context(temporarySession)
            val now = System.currentTimeMillis()
            PairingResult(
                PairedComputer(context.instance_id, context.host_name, baseUrl, context.device_id, cookie.name, now, now),
                cookie,
            )
        }

    suspend fun context(session: ServerSession): AccessContext = withContext(Dispatchers.IO) {
        val request = Request.Builder().url("${session.baseUrl}/api/access/context").build()
        CrewHttp.client(session).newCall(request).execute().use {
            it.requireSuccess()
            val access = try {
                responseJson.decodeFromString<AccessContext>(it.body.string())
            } catch (error: SerializationException) {
                throw IOException("电脑未返回完整的配对信息", error)
            }
            if (access.instance_id.isBlank() || access.host_name.isBlank() || access.device_id.isBlank()) {
                throw IOException("电脑未返回完整的配对信息")
            }
            access
        }
    }

    suspend fun logout(session: ServerSession) = withContext(Dispatchers.IO) {
        val request = Request.Builder().url("${session.baseUrl}/remote/logout")
            .header("X-Crew-Request-Id", UUID.randomUUID().toString())
            .post("{}".toRequestBody()).build()
        CrewHttp.client(session).newCall(request).execute().use { it.requireSuccess() }
    }
}
