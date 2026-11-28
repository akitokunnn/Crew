package local.crew.android.core

import okhttp3.OkHttpClient

object CrewHttp {
    /** 只发往该会话的 origin，自动加 Cookie、Origin 和 X-Crew 请求头，禁止跟随重定向。 */
    fun client(session: ServerSession): OkHttpClient = TODO("配对任务实现")
}
