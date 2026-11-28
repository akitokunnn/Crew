package local.crew.android.notify

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.util.Log
import java.io.IOException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import local.crew.android.CrewApp
import local.crew.android.MainActivity
import local.crew.android.R
import local.crew.android.core.NotificationFrame
import local.crew.android.core.NotificationStream
import local.crew.android.core.PairedComputer

class NotificationService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val app get() = application as CrewApp
    private val repository get() = app.computers
    private val manager get() = getSystemService(NotificationManager::class.java)
    private var listening = false

    override fun onBind(intent: Intent): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (listening) return START_NOT_STICKY
        val session = repository.current()
        if (session == null || !manager.areNotificationsEnabled()) {
            stopSelf()
            return START_NOT_STICKY
        }
        createChannels()
        val initial = ongoing("正在重新连接 ${session.hostName}…")
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(CONNECTION_ID, initial, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else startForeground(CONNECTION_ID, initial)
        listening = true
        scope.launch {
            repository.state.map { state -> state.records.firstOrNull { it.instanceId == state.currentId } }
                .distinctUntilChanged { old, new ->
                    old?.instanceId == new?.instanceId && old?.deviceId == new?.deviceId && old?.baseUrl == new?.baseUrl
                }
                .collectLatest { computer ->
                    val active = computer?.let { repository.byInstance(it.instanceId) }
                    if (computer == null || active == null) {
                        stopSelf()
                    } else {
                        manager.notify(CONNECTION_ID, ongoing("正在重新连接 ${computer.hostName}…"))
                        NotificationStream(active).run(
                            lastSeq = repository.cursor(computer.instanceId),
                            processed = { postFrame(computer, it) },
                            saveCursor = { repository.markProcessed(computer, it) },
                            connection = { connected ->
                                val title = if (connected) "已连接 ${computer.hostName}"
                                else "正在重新连接 ${computer.hostName}…"
                                manager.notify(CONNECTION_ID, ongoing(title, if (connected) "随时接收提醒" else null))
                            },
                            disconnected = { failure ->
                                Log.w("CrewNotifications", "${failure.javaClass.simpleName}: ${failure.message}")
                            },
                            revoked = {
                                try {
                                    if (repository.remove(computer) != null) {
                                        app.revoked.value = computer
                                    }
                                } catch (failure: IOException) {
                                    Log.e("CrewNotifications", "清除已下线设备失败", failure)
                                    app.revoked.value = computer
                                }
                            },
                        )
                    }
                }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }

    private fun postFrame(computer: PairedComputer, frame: NotificationFrame) {
        if (!manager.areNotificationsEnabled()) throw IOException("手机提醒权限已关闭")
        when (frame) {
            is NotificationFrame.Ready -> Unit
            is NotificationFrame.Gap -> post(computer, "gap", "待办", "有些提醒太旧已经清掉，打开 Crew 看看", "", "/")
            is NotificationFrame.Item -> {
                val channel = when (frame.kind) {
                    "decision" -> "待拍板"
                    "todo" -> "待办"
                    "goal_report" -> "完成"
                    "session_error", "codex_failed" -> "卡住"
                    else -> throw IOException("电脑发来了未知类型的提醒（${frame.kind}）")
                }
                post(computer, frame.item_id, channel, frame.title, frame.body, frame.url)
            }
        }
    }

    private fun post(computer: PairedComputer, itemId: String, channel: String, title: String, body: String, url: String) {
        val id = itemId.hashCode()
        val intent = Intent(this, NotificationOpenActivity::class.java).apply {
            data = Uri.Builder().scheme("crew").authority("notification")
                .appendPath(computer.instanceId).appendPath(itemId).build()
            putExtra(NotificationOpenActivity.INSTANCE, computer.instanceId)
            putExtra(NotificationOpenActivity.URL, url)
        }
        val open = PendingIntent.getActivity(this, id, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val notice = Notification.Builder(this, channel)
            .setSmallIcon(R.drawable.ic_crew_notification)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setOnlyAlertOnce(true)
            .build()
        manager.notify("${computer.instanceId}:$itemId", id, notice)
    }

    private fun ongoing(title: String, subtitle: String? = null): Notification {
        val intent = Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val open = PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CONNECTION_CHANNEL)
            .setSmallIcon(R.drawable.ic_crew_notification)
            .setContentTitle(title)
            .setContentText(subtitle)
            .setContentIntent(open)
            .setShowWhen(false)
            .setOngoing(true)
            .build()
    }

    private fun createChannels() {
        val connection = NotificationChannel(CONNECTION_CHANNEL, "电脑连接", NotificationManager.IMPORTANCE_LOW)
        connection.setShowBadge(false)
        manager.createNotificationChannels(listOf(connection) + listOf("待拍板", "待办", "完成", "卡住").map {
            NotificationChannel(it, it, NotificationManager.IMPORTANCE_HIGH)
        })
    }

    companion object {
        private const val CONNECTION_ID = 1
        private const val CONNECTION_CHANNEL = "computer_connection"
    }
}
