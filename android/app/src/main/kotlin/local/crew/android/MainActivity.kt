package local.crew.android

import android.Manifest
import android.app.Activity
import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.core.view.doOnLayout
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.webkit.JavaScriptReplyProxy
import java.io.IOException
import java.security.GeneralSecurityException
import java.security.ProviderException
import kotlin.coroutines.resume
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import local.crew.android.core.CrewResponseException
import local.crew.android.core.PairLink
import local.crew.android.core.PairedComputer
import local.crew.android.core.PairingClient
import local.crew.android.core.ServerSession
import local.crew.android.core.VoiceLaunch
import local.crew.android.notify.NotificationService
import local.crew.android.ui.BusyText
import local.crew.android.ui.CodePage
import local.crew.android.ui.ConnectionIssue
import local.crew.android.ui.PairTarget
import local.crew.android.ui.PairingPage
import local.crew.android.ui.ReconnectPage
import local.crew.android.ui.SIGNED_OUT
import local.crew.android.ui.ScannerPage
import local.crew.android.ui.theme.CrewTheme
import local.crew.android.ui.theme.LocalCrewColors
import local.crew.android.ui.voice.VoiceActivity
import local.crew.android.web.BridgeAction
import local.crew.android.web.BridgeMessage
import local.crew.android.web.CrewWebView
import local.crew.android.web.clearWebCookie
import local.crew.android.web.installWebCookie
import local.crew.android.web.resolvePage
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

class MainActivity : ComponentActivity() {
    private val app get() = application as CrewApp
    private val repository get() = app.computers
    private val discovery by lazy { ComputerDiscovery(this) }
    private val loopback by lazy {
        applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0 && Build.HARDWARE in setOf("ranchu", "goldfish")
    }
    private val pairing by lazy { PairingClient(loopback) }

    private var page by mutableStateOf<Page>(Page.Loading)
    private var issue by mutableStateOf<ConnectionIssue?>(null)
    private var busy by mutableStateOf(false)
    private var nearby by mutableStateOf<List<NearbyComputer>>(emptyList())
    private var searching by mutableStateOf(false)
    private var discoveryError by mutableStateOf<String?>(null)
    private var discoveryJob: Job? = null
    private var connectionJob: Job? = null
    private var cameraGranted by mutableStateOf(false)
    private var scannerTarget: PairTarget? = null
    private var web: CrewWebView? = null
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var permissionPending = false
    private var notificationReply: ((Boolean) -> Unit)? = null

    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        cameraGranted = it
    }
    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        permissionPending = false
        syncNotifications()
        notificationReply?.invoke(notificationsEnabled())
        notificationReply = null
    }
    private val filePicker = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val clips = result.data?.clipData
        val files = if (result.resultCode == Activity.RESULT_OK && clips != null) {
            (0 until clips.itemCount).mapNotNull { clips.getItemAt(it).uri }.toTypedArray()
        } else WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
        fileCallback?.onReceiveValue(files)
        fileCallback = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.auto(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.auto(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT),
        )
        setContent {
            CrewTheme {
                val computers by repository.state.collectAsStateWithLifecycle()
                LaunchedEffect(page is Page.Pair) {
                    if (page is Page.Pair) lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
                        search()
                        try {
                            kotlinx.coroutines.awaitCancellation()
                        } finally {
                            discoveryJob?.cancel()
                        }
                    }
                }
                BackHandler { goBack() }
                Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
                    Box(Modifier.fillMaxSize().safeDrawingPadding()) {
                        when (val screen = page) {
                            Page.Loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                BusyText("正在打开 Crew…")
                            }
                            is Page.Pair -> PairingPage(
                                computers, nearby, searching, discoveryError, issue, screen.manual,
                                onScan = { openScanner(null) },
                                onNearby = ::selectNearby,
                                onSelect = { connect(it) },
                                onForget = ::forget,
                                onSearch = ::search,
                                onManual = ::enterAddress,
                                onReturn = screen.back?.let { { connect(it) } },
                            )
                            is Page.Code -> CodePage(screen.target, issue, busy, { pair(screen.target, it) },
                                { openScanner(screen.target) }, { showPair() })
                            Page.Scan -> ScannerPage(cameraGranted, busy, issue, ::scan, ::openAppSettings,
                                { scannerTarget?.let { showCode(it) } ?: showPair(manual = true) },
                                { showPair() }, { issue = null })
                            is Page.Reconnect -> ReconnectPage(
                                screen.computer, requireNotNull(issue), busy,
                                { connect(screen.computer, searchForMoved = true, path = screen.path) },
                                { showPair(back = screen.computer) },
                            )
                            is Page.Work -> Workbench(screen)
                        }
                    }
                }
            }
        }

        lifecycleScope.launch {
            try {
                repository.load()
                val open = app.notificationOpen.value
                if (open != null) openNotification(open)
                else {
                    val state = repository.state.value
                    val current = state.records.find { it.instanceId == state.currentId }
                    if (current == null) showPair() else connect(current)
                }
            } catch (failure: IOException) {
                showPair(ConnectionIssue("读不到配对记录（${failure.javaClass.simpleName}）"))
            } catch (failure: SerializationException) {
                showPair(ConnectionIssue("配对记录无法读取（${failure.javaClass.simpleName}）"))
            }
            app.notificationOpen.collect { if (it != null) openNotification(it) }
        }
        lifecycleScope.launch {
            app.revoked.collect { removed ->
                if (removed != null) {
                    val current = activeComputer()
                    if (current?.deviceId == removed.deviceId && current.instanceId == removed.instanceId ||
                        current == null && page is Page.Pair) {
                        showPair(ConnectionIssue(SIGNED_OUT))
                        removeCookie(removed)
                    }
                    app.revoked.compareAndSet(removed, null)
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        cameraGranted = granted(Manifest.permission.CAMERA)
        syncNotifications()
    }

    @Composable
    private fun Workbench(work: Page.Work) {
        val colors = LocalCrewColors.current
        Column(Modifier.fillMaxSize()) {
            Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(start = 16.dp, end = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Text(work.computer.hostName, Modifier.weight(1f), color = colors.text2,
                    style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                TextButton(onClick = { showPair(back = work.computer) }) { Text("换电脑") }
            }
            HorizontalDivider(color = colors.line)
            AndroidView(
                factory = { makeWeb(work) },
                modifier = Modifier.weight(1f).fillMaxWidth(),
                onRelease = {
                    if (web === it) {
                        web = null
                        fileCallback?.onReceiveValue(null)
                        fileCallback = null
                    }
                    it.release()
                },
            )
        }
    }

    private fun showPair(problem: ConnectionIssue? = null, manual: Boolean = false, back: PairedComputer? = null) {
        connectionJob?.cancel()
        busy = false
        page = Page.Pair(manual, back)
        issue = problem
    }

    private fun showCode(target: PairTarget, problem: ConnectionIssue? = null) {
        connectionJob?.cancel()
        busy = false
        page = Page.Code(target)
        issue = problem
    }

    private fun search() {
        discoveryJob?.cancel()
        nearby = emptyList()
        discoveryError = null
        searching = true
        discoveryJob = lifecycleScope.launch {
            launch { delay(8000); searching = false }
            try {
                discovery.nearby().collect { nearby = it; if (it.isNotEmpty()) searching = false }
            } catch (failure: DiscoveryException) {
                discoveryError = failure.message
                searching = false
            } catch (failure: SecurityException) {
                discoveryError = "系统限制了附近电脑搜索（${failure.javaClass.simpleName}）"
                searching = false
            }
        }
    }

    private fun enterAddress(address: String) {
        try {
            showCode(PairTarget(PairLink.origin(address, loopback), null))
        } catch (failure: IllegalArgumentException) {
            issue = ConnectionIssue(requireNotNull(failure.message))
        }
    }

    private fun selectNearby(found: NearbyComputer) {
        val existing = repository.state.value.records.find { it.instanceId == found.instanceId }
        when {
            existing == null -> showCode(PairTarget(found.baseUrl, found.hostName, found.instanceId))
            existing.baseUrl == found.baseUrl -> connect(existing)
            else -> showMoved(found)
        }
    }

    private fun showMoved(found: NearbyComputer) {
        showCode(PairTarget(found.baseUrl, found.hostName, found.instanceId),
            ConnectionIssue("这台电脑换了地址，请重新配对"))
    }

    private fun openScanner(target: PairTarget?) {
        issue = null
        scannerTarget = target
        page = Page.Scan
        cameraGranted = granted(Manifest.permission.CAMERA)
        if (!cameraGranted) cameraPermission.launch(Manifest.permission.CAMERA)
    }

    private fun openAppSettings() {
        startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null)))
    }

    private fun scan(text: String) {
        if (busy || page != Page.Scan) return
        try {
            val link = PairLink.parse(text, loopback)
            val target = scannerTarget?.takeIf { it.baseUrl == link.baseUrl } ?: PairTarget(link.baseUrl, null)
            pair(target, link.code)
        } catch (failure: IllegalArgumentException) {
            issue = ConnectionIssue(requireNotNull(failure.message))
        }
    }

    private fun pair(target: PairTarget, code: String) {
        if (busy) return
        busy = true
        issue = null
        connectionJob = lifecycleScope.launch {
            try {
                val result = pairing.pair(target.baseUrl, code, "Crew 安卓版 · ${Build.MODEL}")
                if (target.instanceId != null && target.instanceId != result.computer.instanceId) {
                    throw IOException("配对的电脑和选中的电脑不一致")
                }
                val session = repository.save(result)
                showWork(result.computer, session, "/")
                requestNotifications()
            } catch (failure: CrewResponseException) {
                issue = ConnectionIssue(requireNotNull(failure.message), invalidCode = failure.status == 400)
            } catch (failure: IOException) {
                issue = networkIssue(failure)
            } catch (failure: GeneralSecurityException) {
                issue = storageIssue(failure)
            } catch (failure: ProviderException) {
                issue = storageIssue(failure)
            } catch (failure: IllegalStateException) {
                issue = storageIssue(failure)
            } finally {
                if (isActive) busy = false
            }
        }
    }

    private fun connect(computer: PairedComputer, searchForMoved: Boolean = false, path: String = "/") {
        connectionJob?.cancel()
        val retryPage = page is Page.Reconnect
        if (!retryPage) page = Page.Loading
        busy = true
        connectionJob = lifecycleScope.launch {
            try {
                val session = repository.byInstance(computer.instanceId)
                if (session == null) {
                    showPair(ConnectionIssue(SIGNED_OUT))
                    return@launch
                }
                val context = pairing.context(session)
                if (context.instance_id != computer.instanceId || context.device_id != computer.deviceId) {
                    throw IOException("当前地址和已配对电脑不一致")
                }
                val updated = repository.confirm(computer, context.host_name)
                if (updated == null || !repository.select(updated)) {
                    showPair(ConnectionIssue(SIGNED_OUT))
                    return@launch
                }
                showWork(updated, session, path)
            } catch (failure: CrewResponseException) {
                if (failure.status == 401) invalidate(computer)
                else showReconnect(computer, ConnectionIssue(requireNotNull(failure.message)), path)
            } catch (failure: IOException) {
                if (searchForMoved) {
                    val moved = findMoved(computer)
                    if (moved != null) {
                        showMoved(moved)
                        return@launch
                    }
                }
                showReconnect(computer, networkIssue(failure), path)
            } catch (failure: IllegalStateException) {
                showReconnect(computer, storageIssue(failure), path)
            } finally {
                if (isActive) busy = false
            }
        }
    }

    private suspend fun findMoved(computer: PairedComputer): NearbyComputer? = try {
        withTimeoutOrNull(5000) {
            discovery.nearby().first { list -> list.any { it.instanceId == computer.instanceId && it.baseUrl != computer.baseUrl } }
                .first { it.instanceId == computer.instanceId && it.baseUrl != computer.baseUrl }
        }
    } catch (failure: DiscoveryException) {
        Log.w("CrewPairing", "重新寻找电脑失败：${failure.message}")
        null
    } catch (failure: SecurityException) {
        Log.w("CrewPairing", "重新寻找电脑失败", failure)
        null
    }

    private fun showReconnect(computer: PairedComputer, problem: ConnectionIssue, path: String) {
        page = Page.Reconnect(computer, path)
        issue = problem
    }

    private suspend fun showWork(computer: PairedComputer, session: ServerSession, path: String) {
        val header = withContext(Dispatchers.IO) { session.cookieHeader() }
        clearCookieJar()
        installWebCookie(session, header)
        page = Page.Work(computer, session, path)
        issue = null
        syncNotifications()
    }

    private suspend fun clearCookieJar() {
        val jar = android.webkit.CookieManager.getInstance()
        suspendCancellableCoroutine { continuation ->
            jar.removeAllCookies { continuation.resume(Unit) }
        }
    }

    private fun makeWeb(work: Page.Work): CrewWebView = CrewWebView(this, work.session,
        isActive = {
            page === work && repository.current()?.let {
                it.instanceId == work.session.instanceId && it.baseUrl == work.session.baseUrl
            } == true && repository.state.value.records.any {
                it.instanceId == work.computer.instanceId && it.deviceId == work.computer.deviceId
            }
        },
        disconnected = { message, revalidate ->
            val path = web?.url?.toHttpUrlOrNull()?.let { parsed ->
                parsed.encodedPath + (parsed.encodedQuery?.let { "?$it" } ?: "") +
                    (parsed.encodedFragment?.let { "#$it" } ?: "")
            } ?: work.path
            showReconnect(work.computer, ConnectionIssue(message, network = !revalidate), path)
            if (revalidate) connect(work.computer, path = path)
        },
        revoked = { lifecycleScope.launch { invalidate(work.computer) } },
        bridge = { request, reply -> dispatchBridge(work, request, reply) },
        chooseFile = ::chooseFile,
    ).also { view ->
        web = view
        view.doOnLayout { view.open(work.path) }
    }

    private fun chooseFile(callback: ValueCallback<Array<Uri>>, params: WebChromeClient.FileChooserParams): Boolean {
        fileCallback?.onReceiveValue(null)
        fileCallback = callback
        try {
            filePicker.launch(params.createIntent())
        } catch (_: ActivityNotFoundException) {
            fileCallback = null
            callback.onReceiveValue(null)
            toast("手机上没有能选择文件的应用")
        }
        return true
    }

    private suspend fun invalidate(computer: PairedComputer) {
        try {
            repository.remove(computer)
        } catch (failure: IOException) {
            Log.e("CrewPairing", "清除已下线设备失败", failure)
        }
        showPair(ConnectionIssue(SIGNED_OUT))
        removeCookie(computer)
        syncNotifications()
    }

    private fun forget(computer: PairedComputer) {
        lifecycleScope.launch {
            try {
                val session = repository.remove(computer) ?: return@launch
                removeCookie(computer)
                syncNotifications()
                pairing.logout(session)
            } catch (failure: CrewResponseException) {
                toast(requireNotNull(failure.message))
            } catch (failure: IOException) {
                toast("没能通知电脑退出登录（${failure.javaClass.simpleName}）")
            } catch (failure: IllegalStateException) {
                toast("没能通知电脑退出登录（${failure.javaClass.simpleName}）")
            }
        }
    }

    private suspend fun removeCookie(computer: PairedComputer) {
        try {
            clearWebCookie(computer.baseUrl, computer.cookieName)
        } catch (failure: IOException) {
            Log.w("CrewPairing", "清除网页登录失败", failure)
        }
    }

    private fun openNotification(open: NotificationOpen) {
        app.notificationOpen.compareAndSet(open, null)
        val computer = repository.state.value.records.find { it.instanceId == open.instanceId }
        if (computer == null) {
            if (page == Page.Loading) showPair(ConnectionIssue(SIGNED_OUT))
            else toast("这台电脑需要重新配对")
            return
        }
        if (resolvePage(computer.baseUrl.toHttpUrl(), open.url) == null) {
            if (page == Page.Loading) connect(computer)
            toast("这条提醒的地址无效")
            return
        }
        val work = page as? Page.Work
        val view = web
        if (view != null && work?.computer?.deviceId == computer.deviceId && work.computer.instanceId == computer.instanceId) {
            view.open(open.url)
        } else connect(computer, path = open.url)
    }

    private fun dispatchBridge(work: Page.Work, request: BridgeMessage, reply: JavaScriptReplyProxy) {
        fun send(result: kotlinx.serialization.json.JsonElement = kotlinx.serialization.json.JsonNull) {
            if (page === work) reply.postMessage(BridgeMessage.success(request.id, result))
        }
        when (val action = request.action) {
            BridgeAction.Available -> send(buildJsonObject { put("version", 1) })
            is BridgeAction.OpenVoice -> {
                startActivity(Intent(this, VoiceActivity::class.java)
                    .putExtra(VoiceLaunch.EXTRA_INSTANCE_ID, work.computer.instanceId)
                    .putExtra(VoiceLaunch.EXTRA_PROJECT, action.project))
                send()
            }
            BridgeAction.NotificationsEnabled -> send(JsonPrimitive(notificationsEnabled()))
            BridgeAction.RequestNotifications -> {
                if (permissionPending) reply.postMessage(BridgeMessage.failure(request.id, "请先完成当前的提醒授权"))
                else requestNotifications { send(JsonPrimitive(it)) }
            }
            BridgeAction.SwitchComputer -> {
                send()
                showPair(back = work.computer)
            }
        }
    }

    private fun granted(permission: String) =
        ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

    private fun notificationsEnabled() = getSystemService(NotificationManager::class.java).areNotificationsEnabled()

    private fun requestNotifications(reply: ((Boolean) -> Unit)? = null) {
        if (Build.VERSION.SDK_INT >= 33 && !granted(Manifest.permission.POST_NOTIFICATIONS)) {
            notificationReply = reply
            permissionPending = true
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            syncNotifications()
            reply?.invoke(notificationsEnabled())
        }
    }

    private fun syncNotifications() {
        val service = Intent(this, NotificationService::class.java)
        if (!notificationsEnabled() || repository.current() == null) {
            stopService(service)
        } else if (page is Page.Work && lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) {
            ContextCompat.startForegroundService(this, service)
        }
    }

    private fun goBack() {
        when (val screen = page) {
            is Page.Work -> if (web?.canGoBack() == true) web?.goBack() else moveTaskToBack(true)
            is Page.Code -> showPair(manual = screen.target.hostName == null)
            Page.Scan -> scannerTarget?.let { showCode(it) } ?: showPair()
            is Page.Reconnect -> showPair(back = screen.computer)
            is Page.Pair -> screen.back?.let { connect(it) } ?: moveTaskToBack(true)
            Page.Loading -> {
                if (busy) showPair() else moveTaskToBack(true)
            }
        }
    }

    private fun activeComputer(): PairedComputer? = when (val screen = page) {
        is Page.Work -> screen.computer
        is Page.Reconnect -> screen.computer
        is Page.Pair -> screen.back
        else -> null
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    private fun networkIssue(failure: IOException): ConnectionIssue {
        Log.w("CrewPairing", "${failure.javaClass.simpleName}: ${failure.message}")
        val detail = if (failure is java.net.SocketException || failure is java.io.InterruptedIOException)
            "连不上这台电脑（${failure.javaClass.simpleName}）"
        else failure.message ?: "连不上这台电脑（${failure.javaClass.simpleName}）"
        return ConnectionIssue(detail, network = true)
    }

    private fun storageIssue(failure: Throwable): ConnectionIssue {
        Log.e("CrewPairing", "配对记录无法读取或保存", failure)
        return ConnectionIssue("手机无法读取或保存配对记录（${failure.javaClass.simpleName}）")
    }

    private sealed interface Page {
        data object Loading : Page
        data class Pair(val manual: Boolean, val back: PairedComputer?) : Page
        data class Code(val target: PairTarget) : Page
        data object Scan : Page
        data class Reconnect(val computer: PairedComputer, val path: String) : Page
        class Work(val computer: PairedComputer, val session: ServerSession, val path: String) : Page
    }
}
