package local.crew.android.core

import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class NotificationStreamTest {
    @Test fun replaysFromProcessedCursorAndSkipsDuplicatesAcrossReconnect() = runTest {
        MockWebServer().use { server ->
            server.enqueue(events(frame("""{"type":"ready","seq":5}""") + ": heartbeat\n\n" + item(6)))
            server.enqueue(events(item(6) + frame("""{"type":"gap","oldest":10}""") + item(10) + item(10)))
            server.enqueue(unauthorized())
            val frames = mutableListOf<NotificationFrame>()
            val saved = mutableListOf<Long>()
            val order = mutableListOf<String>()
            var revoked = false
            NotificationStream(testSession(server)).run(null, processed = {
                frames += it
                order += "post"
            }, saveCursor = {
                order += "save"
                saved += it
            }, connection = {}, disconnected = {}, revoked = { revoked = true })
            assertEquals(listOf(5L, 6L, 9L, 10L), saved)
            assertEquals(listOf("post", "save", "post", "save", "post", "save", "post", "save"), order)
            assertEquals(NotificationFrame.Ready(5), frames[0])
            assertEquals(NotificationFrame.Gap(10), frames[2])
            assertEquals(listOf(6L, 10L), frames.filterIsInstance<NotificationFrame.Item>().map { it.seq })
            assertEquals(10_000, testScheduler.currentTime)
            assertTrue(revoked)
            val first = requireNotNull(server.takeRequest(1, TimeUnit.SECONDS))
            assertEquals("/api/notifications/events", first.path)
            assertNull(first.getHeader("Last-Event-ID"))
            for (after in listOf("6", "10")) {
                val next = requireNotNull(server.takeRequest(1, TimeUnit.SECONDS))
                assertEquals("/api/notifications/events?after=$after", next.path)
                assertEquals(after, next.getHeader("Last-Event-ID"))
                assertEquals("crew_test=example", next.getHeader("Cookie"))
            }
        }
    }

    @Test fun processingFailureDoesNotAdvanceAndRetriedFrameIsDelivered() = runTest {
        MockWebServer().use { server ->
            repeat(2) { server.enqueue(events(item(8))) }
            server.enqueue(unauthorized())
            var deliveries = 0
            val saved = mutableListOf<Long>()
            NotificationStream(testSession(server)).run(7, processed = {
                if (++deliveries == 1) throw IOException("notification failed")
            }, saveCursor = { saved += it }, connection = {}, disconnected = {}, revoked = {})
            assertEquals(2, deliveries)
            assertEquals(listOf(8L), saved)
            assertEquals(listOf("7", "7", "8"), (1..3).map {
                requireNotNull(server.takeRequest(1, TimeUnit.SECONDS)).getHeader("Last-Event-ID")
            })
        }
    }

    @Test fun unauthorizedStopsWithoutRetryAndReportedBackoffIsCapped() = runTest {
        MockWebServer().use { server ->
            server.enqueue(unauthorized())
            var revoked = false
            var errors = 0
            NotificationStream(testSession(server)).run(null, processed = { error("Unexpected frame") },
                saveCursor = { error("Unexpected cursor") }, connection = {}, disconnected = { errors++ },
                revoked = { revoked = true })
            assertTrue(revoked)
            assertEquals(0, errors)
            assertEquals(1, server.requestCount)
            assertEquals(0, testScheduler.currentTime)
        }
        assertEquals(listOf(5_000L, 10_000L, 15_000L, 20_000L, 30_000L, 45_000L, 60_000L, 120_000L, 120_000L),
            (0..8).map(NotificationStream::retryDelayMs))
    }

    @Test fun heartbeatResetsGrowingBackoffWithoutAdvancingCursor() = runTest {
        MockWebServer().use { server ->
            repeat(2) { server.enqueue(events("")) }
            server.enqueue(events(": heartbeat\n\n"))
            server.enqueue(unauthorized())
            val disconnects = mutableListOf<Long>()
            NotificationStream(testSession(server)).run(4, processed = { error("Unexpected frame") },
                saveCursor = { error("Unexpected cursor") }, connection = {},
                disconnected = { disconnects += testScheduler.currentTime }, revoked = {})
            assertEquals(listOf(0L, 5000L, 15000L), disconnects)
            assertEquals(20_000, testScheduler.currentTime)
        }
    }

    private fun unauthorized() = MockResponse().setResponseCode(401).setBody("""{"error":"请先登录 Crew"}""")
    private fun events(body: String) = MockResponse().setHeader("Content-Type", "text/event-stream").setBody(body)
    private fun frame(json: String, id: Long? = null) = (id?.let { "id: $it\n" } ?: "") + "data: $json\n\n"
    private fun item(seq: Long) = frame("""{"type":"notification","seq":$seq,"item_id":"todo:$seq","kind":"todo","project":"demo","title":"复核","body":"内容","url":"/?project=demo#todos","created_at":"2026-11-28"}""", seq)
}
