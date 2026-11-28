package local.crew.android.core

import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.test.runTest
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test

class PairingTests {
    @Test fun privateIpv4AndEightDigits() {
        for (address in listOf("10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.0.1")) {
            assertEquals(PairLink("http://$address:65535", "00123456"),
                PairLink.parse("http://$address:65535/remote/login#code=00123456"))
        }
        assertEquals("http://10.0.0.1", PairLink.origin("10.0.0.1:80"))
        assertEquals("http://127.42.0.1:4000",
            PairLink.parse("http://127.42.0.1:4000/remote/login#code=12345678", allowLoopback = true).baseUrl)
    }

    @Test fun rejectsInvalidLinksEvenWithAValidCode() {
        val origins = listOf("https://192.168.0.1:80", "http://host:80", "http://crew.local:80",
            "http://[::1]:80", "http://[fd00::1]:80", "http://8.8.8.8:80", "http://172.15.0.1:80",
            "http://172.32.0.1:80", "http://192.169.0.1:80", "http://127.0.0.1:80", "http://0.0.0.0:80",
            "http://192.168.0.1:0", "http://192.168.0.1:65536", "http://192.168.0.1:-1", "http://192.168.0.1",
            "http://192.168.256.1:80", "http://010.0.0.1:80", "http://10.0.1:80",
            "http://user@10.0.0.1:80", "http://10.0.0.1:80?bad")
        for (origin in origins) {
            val error = assertThrows(origin, IllegalArgumentException::class.java) {
                PairLink.parse("$origin/remote/login#code=12345678")
            }
            assertTrue(error.message.orEmpty().any { it in '\u4e00'..'\u9fff' })
        }
    }

    @Test fun rejectsWrongPathAndNonAsciiOrWrongLengthCodes() {
        for (tail in listOf("/#code=12345678", "/remote/login?code=12345678", "/remote/login#code=1234567",
            "/remote/login#code=123456789", "/remote/login#code=１２３４５６７８", "/remote/login#code=abcdef12",
            "/remote/login#code=12345678&next=/", "/remote/login?next=/#code=12345678")) {
            assertThrows(IllegalArgumentException::class.java) { PairLink.parse("http://10.0.0.1:5000$tail") }
        }
    }

    @Test fun credentialClientBindsOriginAndDoesNotFollowRedirects() {
        MockWebServer().use { paired ->
            MockWebServer().use { other ->
                paired.enqueue(MockResponse().setResponseCode(307).setHeader("Location", other.url("/target")))
                val session = testSession(paired)
                val client = CrewHttp.client(session)
                val write = Request.Builder().url(paired.url("/api/op"))
                    .header("X-Crew-Request-Id", "caller-supplied").post("{}".toRequestBody()).build()
                client.newCall(write).execute().use { assertEquals(307, it.code) }
                val request = requireNotNull(paired.takeRequest(1, TimeUnit.SECONDS))
                assertEquals("crew_test=example", request.getHeader("Cookie"))
                assertEquals(session.baseUrl, request.getHeader("Origin"))
                assertEquals("1", request.getHeader("X-Crew"))
                assertEquals("application/json", request.getHeader("Content-Type"))
                assertEquals("caller-supplied", request.getHeader("X-Crew-Request-Id"))
                assertThrows(IOException::class.java) {
                    client.newCall(Request.Builder().url(other.url("/target")).build()).execute()
                }
                assertThrows(IOException::class.java) {
                    client.newCall(Request.Builder().url(paired.url("/").newBuilder().scheme("https").build()).build()).execute()
                }
                assertEquals(0, other.requestCount)
                assertEquals(1, paired.requestCount)
            }
        }
    }

    @Test fun freshPairUsesOneUnauthenticatedPostThenProbesWithReturnedCookie() = runTest {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setHeader("Set-Cookie", "crew_abc=xyz; Path=/; HttpOnly")
                .setBody("""{"ok":true,"next":"/"}"""))
            server.enqueue(MockResponse().setBody("""{"device_id":"device-a","host_name":"Workstation","instance_id":"abc","remote":true}"""))
            val origin = "http://127.0.0.1:${server.port}"
            val result = PairingClient(allowLoopback = true).pair(origin, "00012345", "Crew 安卓版 · Pixel")
            val login = requireNotNull(server.takeRequest(1, TimeUnit.SECONDS))
            val context = requireNotNull(server.takeRequest(1, TimeUnit.SECONDS))
            assertEquals("/remote/login", login.path)
            assertEquals("POST", login.method)
            assertNull(login.getHeader("Cookie"))
            assertNull(login.getHeader("Authorization"))
            assertEquals(origin, login.getHeader("Origin"))
            assertEquals("1", login.getHeader("X-Crew"))
            assertEquals("application/json", login.getHeader("Content-Type"))
            assertEquals("""{"kind":"browser","code":"00012345","device_name":"Crew 安卓版 · Pixel"}""", login.body.readUtf8())
            assertEquals("GET", context.method)
            assertEquals("/api/access/context", context.path)
            assertEquals("crew_abc=xyz", context.getHeader("Cookie"))
            assertNull(context.getHeader("Origin"))
            assertEquals("device-a", result.computer.deviceId)
            assertEquals("abc", result.computer.instanceId)
            assertEquals("Workstation", result.computer.hostName)
            assertEquals(origin, result.computer.baseUrl)
            assertEquals("crew_abc", result.computer.cookieName)
            assertEquals("xyz", result.cookie.value)
        }
    }

    @Test fun pairingErrorsKeepOriginalTextAndNeverProbeOrRetry() = runTest {
        MockWebServer().use { server ->
            val service = PairingClient(allowLoopback = true)
            val origin = "http://127.0.0.1:${server.port}"
            val errors = mapOf(400 to "配对码不正确或已过期", 429 to "配对尝试次数过多", 403 to "访问地址不匹配",
                503 to "请稍后再试")
            errors.forEach { (status, message) ->
                server.enqueue(MockResponse().setResponseCode(status).setHeader("Retry-After", "0")
                    .setBody("""{"error":"$message"}"""))
                val failure = try {
                    service.pair(origin, "12345678", "Crew 安卓版 · 模拟器")
                    throw AssertionError("Expected $status")
                } catch (error: CrewResponseException) { error }
                assertEquals(status, failure.status)
                assertEquals(message, failure.message)
            }
            assertEquals(errors.size, server.requestCount)
        }
    }
}

internal fun testSession(server: MockWebServer) = object : ServerSession {
    override val instanceId = "test-instance"
    override val hostName = "Test computer"
    override val baseUrl = server.url("/").toString().removeSuffix("/")
    override fun cookieHeader() = "crew_test=example"
}
