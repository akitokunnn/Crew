package local.crew.android.core

/** 已配对电脑的连接信息；由配对模块实现与持久化，语音、通知只读。 */
interface ServerSession {
    val instanceId: String // 电脑 instance_id（mDNS TXT id / access context）
    val hostName: String
    val baseUrl: String // http://<IPv4>:<port>，无尾斜杠
    fun cookieHeader(): String // 当前 Cookie 请求头值；未配对或已失效时抛 IllegalStateException
}

interface ServerSessions {
    fun current(): ServerSession?
    fun byInstance(instanceId: String): ServerSession?
}
