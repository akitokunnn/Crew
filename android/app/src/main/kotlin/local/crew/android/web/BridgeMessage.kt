package local.crew.android.web

import kotlinx.serialization.json.*

internal class BridgeError(val id: String?, message: String) : IllegalArgumentException(message)

internal data class BridgeMessage(val id: String, val action: BridgeAction) {
    companion object {
        fun parse(raw: String): BridgeMessage {
            val value = try {
                Json.parseToJsonElement(raw) as? JsonObject
            } catch (_: kotlinx.serialization.SerializationException) {
                null
            } ?: throw BridgeError(null, "消息格式不正确")
            val id = value.string("id")?.takeIf { it.isNotBlank() && it.length <= 128 }
                ?: throw BridgeError(null, "消息缺少编号")
            val method = value.string("method") ?: throw BridgeError(id, "消息缺少操作")
            val params = value["params"] as? JsonObject ?: throw BridgeError(id, "消息参数不正确")
            val action = when (method) {
                "available" -> BridgeAction.Available
                "openVoice" -> {
                    val project = params.string("project")?.takeIf { it.isNotBlank() }
                        ?: throw BridgeError(id, "请先选择工作区")
                    BridgeAction.OpenVoice(project)
                }
                "notificationsEnabled" -> BridgeAction.NotificationsEnabled
                "requestNotifications" -> BridgeAction.RequestNotifications
                "switchComputer" -> BridgeAction.SwitchComputer
                else -> throw BridgeError(id, "这版 Crew 不支持该操作")
            }
            return BridgeMessage(id, action)
        }

        fun success(id: String, result: JsonElement = JsonNull) = buildJsonObject {
            put("id", id)
            put("ok", true)
            put("result", result)
        }.toString()

        fun failure(id: String?, error: String) = buildJsonObject {
            put("id", id)
            put("ok", false)
            put("error", error)
        }.toString()

        private fun JsonObject.string(key: String): String? =
            (get(key) as? JsonPrimitive)?.takeIf { it.isString }?.content
    }
}

internal sealed interface BridgeAction {
    data object Available : BridgeAction
    data class OpenVoice(val project: String) : BridgeAction
    data object NotificationsEnabled : BridgeAction
    data object RequestNotifications : BridgeAction
    data object SwitchComputer : BridgeAction
}
