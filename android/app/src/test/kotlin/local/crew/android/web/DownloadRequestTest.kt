package local.crew.android.web

import java.io.IOException
import java.util.concurrent.TimeUnit
import local.crew.android.core.ServerSession
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class DownloadRequestTest {
    @Test fun requiresSameSchemeHostAndPortWithoutUserInfo() {
        val origin = "http://192.168.1.2:8080".toHttpUrl()
        assertTrue(sameOrigin(origin, origin.resolve("/files/report.pdf")))
        for (url in listOf(
            "https://192.168.1.2:8080/files/report.pdf",
            "http://192.168.1.3:8080/files/report.pdf",
            "http://192.168.1.2:8081/files/report.pdf",
            "http://user:password@192.168.1.2:8080/files/report.pdf",
            "blob:http://192.168.1.2:8080/report",
            "data:text/plain,report",
        )) assertFalse(url, sameOrigin(origin, url.toHttpUrlOrNull()))
    }

    @Test fun getsFileWithPairedCookie() {
        MockWebServer().use { paired ->
            paired.enqueue(MockResponse().setHeader("Content-Type", "application/pdf")
                .setHeader("Content-Disposition", "attachment; filename=\"report.pdf\"")
                .setBody("report contents"))
            requestDownload(session(paired), paired.url("/files/report")).use { response ->
                assertEquals("application/pdf", response.header("Content-Type"))
                assertEquals("attachment; filename=\"report.pdf\"", response.header("Content-Disposition"))
                assertEquals("report contents", response.body.string())
            }
            val request = requireNotNull(paired.takeRequest(1, TimeUnit.SECONDS))
            assertEquals("GET", request.method)
            assertEquals("/files/report", request.path)
            assertEquals("crew_test=example", request.getHeader("Cookie"))
        }
    }

    @Test fun stopsOnRedirectsEvenWhenLocationIsSameOrigin() {
        MockWebServer().use { paired ->
            MockWebServer().use { other ->
                val session = session(paired)
                for ((status, location) in listOf(
                    302 to paired.url("/next"),
                    307 to other.url("/next"),
                    304 to paired.url("/next"),
                )) {
                    paired.enqueue(MockResponse().setResponseCode(status).setHeader("Location", location))
                    val failure = assertThrows(DownloadException::class.java) {
                        requestDownload(session, paired.url("/files/report")).close()
                    }
                    assertEquals("下载地址跳转到了别处，已停止", failure.message)
                    assertEquals("/files/report", paired.takeRequest(1, TimeUnit.SECONDS)?.path)
                }
                assertEquals(3, paired.requestCount)
                assertEquals(0, other.requestCount)
                assertThrows(IOException::class.java) { requestDownload(session, other.url("/next")).close() }
                assertEquals(0, other.requestCount)
            }
        }
    }

    @Test fun reportsHttpFailureBeforeReadingTheBody() {
        MockWebServer().use { paired ->
            paired.enqueue(MockResponse().setResponseCode(404).setBody("missing"))
            val failure = assertThrows(DownloadException::class.java) {
                requestDownload(session(paired), paired.url("/files/missing")).close()
            }
            assertEquals("电脑未能提供文件（404）", failure.message)
        }
    }

    private fun session(server: MockWebServer) = object : ServerSession {
        override val instanceId = "test-instance"
        override val hostName = "Test computer"
        override val baseUrl = server.url("/").toString().removeSuffix("/")
        override fun cookieHeader() = "crew_test=example"
    }
}
