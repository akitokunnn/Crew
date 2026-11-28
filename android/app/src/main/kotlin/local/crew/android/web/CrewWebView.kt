package local.crew.android.web

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Message
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import androidx.webkit.WebMessageCompat
import java.io.ByteArrayInputStream
import java.io.IOException
import kotlin.coroutines.resume
import kotlinx.coroutines.suspendCancellableCoroutine
import local.crew.android.core.ServerSession
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

internal fun sameOrigin(origin: HttpUrl, target: HttpUrl?) =
    target != null && origin.scheme == target.scheme && origin.host == target.host &&
        origin.port == target.port && target.username.isEmpty() && target.password.isEmpty()

internal fun resolvePage(origin: HttpUrl, path: String): HttpUrl? {
    if (!path.startsWith('/') || path.startsWith("//") || '\\' in path ||
        path.any { it.isISOControl() }) return null
    val url = origin.resolve(path)
    return url?.takeIf { sameOrigin(origin, it) && it.encodedPath !in setOf("/remote/login", "/remote/logout") }
}

internal suspend fun installWebCookie(session: ServerSession, header: String) {
    val jar = CookieManager.getInstance()
    val inserted = suspendCancellableCoroutine { continuation ->
        jar.setCookie(session.baseUrl, "$header; Path=/; HttpOnly; SameSite=Strict") { continuation.resume(it) }
    }
    if (!inserted) throw IOException("手机无法保存网页的配对信息")
    jar.flush()
}

internal suspend fun clearWebCookie(baseUrl: String, cookieName: String) {
    val jar = CookieManager.getInstance()
    val removed = suspendCancellableCoroutine { continuation ->
        jar.setCookie(baseUrl, "$cookieName=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict") { continuation.resume(it) }
    }
    if (!removed) throw IOException("手机无法清除旧的网页登录")
    jar.flush()
}

internal class CrewWebView(
    context: Context,
    private val session: ServerSession,
    private val isActive: () -> Boolean,
    private val disconnected: (String, Boolean) -> Unit,
    private val revoked: () -> Unit,
    private val bridge: (BridgeMessage, JavaScriptReplyProxy) -> Unit,
    private val chooseFile: (ValueCallback<Array<Uri>>, WebChromeClient.FileChooserParams) -> Boolean,
) : WebView(context) {
    private val origin = session.baseUrl.toHttpUrl()
    private val messagesSupported = WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)

    init {
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.useWideViewPort = true
        settings.allowFileAccess = false
        settings.allowContentAccess = false
        settings.setSupportMultipleWindows(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(this, false)
        webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
                handleNavigation(request.url, request.isForMainFrame)

            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                val url = request.url.toString().toHttpUrlOrNull()
                if (sameOrigin(origin, url) || request.url.scheme in setOf("data", "blob", "about")) return null
                return WebResourceResponse("text/plain", "UTF-8", 403, "Blocked", emptyMap(), ByteArrayInputStream(byteArrayOf()))
            }

            override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
                val target = url.toHttpUrlOrNull()
                if (isLogin(target) || !sameOrigin(origin, target)) {
                    stopLoading()
                    handleNavigation(Uri.parse(url), true)
                }
            }

            override fun onPageCommitVisible(view: WebView, url: String) {
                if (!isActive() || !sameOrigin(origin, url.toHttpUrlOrNull())) return
                // Some WebViews report zero for CSS viewport units despite having a visible viewport.
                evaluateJavascript("""
                    (() => {
                      const viewport = window.visualViewport;
                      const resize = () => document.documentElement.style.setProperty('--crew-webview-height', viewport.height + 'px');
                      viewport.addEventListener('resize', resize);
                      resize();
                    })();
                """.trimIndent(), null)
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame && isActive() && sameOrigin(origin, request.url.toString().toHttpUrlOrNull())) {
                    disconnected("连不上这台电脑（${error.description}）", false)
                }
            }

            override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
                if (response.statusCode == 401 && isActive() && sameOrigin(origin, request.url.toString().toHttpUrlOrNull())) {
                    disconnected("电脑需要重新验证配对", true)
                }
            }
        }
        webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean =
                isActive() && chooseFile(callback, params)

            override fun onCreateWindow(view: WebView, isDialog: Boolean, isUserGesture: Boolean, resultMsg: Message): Boolean {
                if (!isUserGesture || !isActive()) return false
                val popup = WebView(context)
                popup.settings.blockNetworkLoads = true
                popup.webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                        if (!handleNavigation(request.url, true)) loadUrl(request.url.toString())
                        this@CrewWebView.post { popup.destroy() }
                        return true
                    }
                }
                (resultMsg.obj as WebViewTransport).webView = popup
                resultMsg.sendToTarget()
                return true
            }
        }
        if (messagesSupported) {
            WebViewCompat.addWebMessageListener(this, "CrewAndroid", setOf(session.baseUrl)) { _, message, source, main, reply ->
                if (main && isActive() && sameOrigin(origin, source.toString().toHttpUrlOrNull())) {
                    val request = try {
                        if (message.type != WebMessageCompat.TYPE_STRING) throw BridgeError(null, "消息格式不正确")
                        BridgeMessage.parse(message.data.orEmpty())
                    } catch (error: BridgeError) {
                        reply.postMessage(BridgeMessage.failure(error.id, requireNotNull(error.message)))
                        null
                    }
                    if (request != null) bridge(request, reply)
                }
            }
        } else {
            showMessage("手机的系统网页组件版本太旧，请更新后再打开语音")
        }
        setDownloadListener { _, _, _, _, _ ->
            if (isActive()) showMessage("手机端暂时不能下载，请在电脑上打开附件")
        }
    }

    fun open(path: String) {
        val page = resolvePage(origin, path)
        if (page != null && isActive()) loadUrl(page.toString())
        else if (page == null) showMessage("这条提醒的地址无效")
    }

    fun release() {
        stopLoading()
        if (messagesSupported) WebViewCompat.removeWebMessageListener(this, "CrewAndroid")
        destroy()
    }

    private fun handleNavigation(uri: Uri, main: Boolean): Boolean {
        if (!isActive()) return true
        val url = uri.toString().toHttpUrlOrNull()
        if (isLogin(url) && main) {
            revoked()
            return true
        }
        if (sameOrigin(origin, url)) return false
        if (main && uri.scheme in setOf("http", "https", "mailto", "tel")) {
            try {
                context.startActivity(Intent(Intent.ACTION_VIEW, uri))
            } catch (_: ActivityNotFoundException) {
                showMessage("手机上没有能打开此链接的应用")
            }
        }
        return true
    }

    private fun isLogin(url: HttpUrl?) = sameOrigin(origin, url) && url?.encodedPath == "/remote/login"

    private fun showMessage(message: String) = Toast.makeText(context, message, Toast.LENGTH_LONG).show()
}
