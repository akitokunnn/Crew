package local.crew.android.core

import java.io.IOException
import java.net.Proxy
import java.util.concurrent.TimeUnit
import okhttp3.ConnectionPool
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient

internal fun HttpUrl.toHttpOrigin(): String = newBuilder()
    .encodedPath("/").query(null).fragment(null).build().toString().removeSuffix("/")

object CrewHttp {
    // Crew answers HTTP/1.0 and closes each socket; reusing an idle one fails with "unexpected end of stream".
    private val transport = OkHttpClient.Builder()
        .connectionPool(ConnectionPool(0, 1, TimeUnit.SECONDS))
        .proxy(Proxy.NO_PROXY)
        .followRedirects(false)
        .followSslRedirects(false)
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .build()

    /** 只发往会话的 origin，禁止跟随重定向。 */
    fun client(session: ServerSession): OkHttpClient {
        val origin = session.baseUrl.toHttpUrl()
        return transport.newBuilder().addInterceptor { chain ->
            val request = chain.request()
            val url = request.url
            if (url.scheme != origin.scheme || url.host != origin.host || url.port != origin.port ||
                url.username.isNotEmpty() || url.password.isNotEmpty()) {
                throw IOException("请求地址与已配对电脑不一致")
            }
            val cookie = try {
                session.cookieHeader()
            } catch (error: IllegalStateException) {
                throw IOException(error.message, error)
            }
            val authorized = request.newBuilder()
                .header("Cookie", cookie)
                .header("X-Crew", "1")
                .header("Content-Type", "application/json")
            if (request.method == "POST") authorized.header("Origin", origin.toHttpOrigin())
            chain.proceed(authorized.build())
        }.build()
    }
}
