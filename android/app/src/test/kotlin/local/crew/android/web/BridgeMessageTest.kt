package local.crew.android.web

import kotlinx.serialization.json.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.*
import org.junit.Test

class BridgeMessageTest {
    @Test fun decodesSupportedMessagesAndPreservesWorkspace() {
        val actions = mapOf(
            "available" to BridgeAction.Available,
            "notificationsEnabled" to BridgeAction.NotificationsEnabled,
            "requestNotifications" to BridgeAction.RequestNotifications,
            "switchComputer" to BridgeAction.SwitchComputer,
        )
        actions.forEach { (method, action) ->
            assertEquals(BridgeMessage("x", action),
                BridgeMessage.parse("""{"id":"x","method":"$method","params":{}}"""))
        }
        assertEquals(BridgeAction.OpenVoice("语音回归"), BridgeMessage.parse(
            """{"id":"7","method":"openVoice","params":{"project":"语音回归"}}""").action)
    }

    @Test fun invalidParametersAndUnknownMethodReplyWithSameIdAndError() {
        for ((method, params) in listOf("missing" to "{}", "openVoice" to """{"project":4}""",
            "openVoice" to """{"project":" "}""", "available" to "[]")) {
            val failure = assertThrows(BridgeError::class.java) {
                BridgeMessage.parse("""{"id":"7","method":"$method","params":$params}""")
            }
            val reply = Json.parseToJsonElement(BridgeMessage.failure(failure.id, requireNotNull(failure.message))).jsonObject
            assertEquals("7", reply.getValue("id").jsonPrimitive.content)
            assertFalse(reply.getValue("ok").jsonPrimitive.boolean)
            assertTrue(reply.getValue("error").jsonPrimitive.content.isNotBlank())
        }
        assertNull(assertThrows(BridgeError::class.java) { BridgeMessage.parse("[]") }.id)
    }

    @Test fun resultRetainsJsonTypes() {
        val reply = Json.parseToJsonElement(BridgeMessage.success("a", buildJsonObject { put("version", 1) })).jsonObject
        assertEquals("a", reply.getValue("id").jsonPrimitive.content)
        assertTrue(reply.getValue("ok").jsonPrimitive.boolean)
        assertEquals(1, reply.getValue("result").jsonObject.getValue("version").jsonPrimitive.int)
        val permission = Json.parseToJsonElement(BridgeMessage.success("b", JsonPrimitive(false))).jsonObject
        assertFalse(permission.getValue("result").jsonPrimitive.boolean)
    }

    @Test fun notificationTargetsStayOnThePairedOrigin() {
        val origin = "http://192.168.1.3:9010".toHttpUrl()
        assertEquals("http://192.168.1.3:9010/?project=demo#todos", resolvePage(origin, "/?project=demo#todos").toString())
        assertEquals("/api/op/goal.report.view", resolvePage(origin, "/api/op/goal.report.view?project=demo")?.encodedPath)
        for (path in listOf("http://192.168.1.3:9011/", "//192.168.1.3:9011/", "/\\192.168.1.3:9011/",
            "/remote/login?next=/", "/remote/logout", "/\n/test")) assertNull(path, resolvePage(origin, path))
        assertFalse(sameOrigin(origin, "http://192.168.1.3:9011".toHttpUrl()))
        assertFalse(sameOrigin(origin, "https://192.168.1.3:9010".toHttpUrl()))
    }
}
