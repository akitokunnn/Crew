package local.crew.android.web

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.database.SQLException
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.SystemClock
import android.provider.MediaStore
import android.util.Log
import android.webkit.MimeTypeMap
import android.widget.Toast
import androidx.annotation.RequiresApi
import androidx.core.content.FileProvider
import java.io.Closeable
import java.io.File
import java.io.IOException
import java.io.OutputStream
import java.net.ProtocolException
import java.net.SocketTimeoutException
import java.nio.file.Files
import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import local.crew.android.R
import local.crew.android.core.ServerSession
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.ResponseBody

class DownloadFileProvider : FileProvider()

internal object CrewDownloads {
    private const val CHANNEL = "crew_downloads"
    private const val TAG = "CrewDownloads"
    private const val GENERIC_MIME = "application/octet-stream"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun start(app: Context, session: ServerSession, url: HttpUrl) {
        scope.launch {
            val manager = app.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel(CHANNEL, "文件下载", NotificationManager.IMPORTANCE_LOW))
            val tag = UUID.randomUUID().toString()
            val notification = Notification.Builder(app, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentTitle("正在下载")
                .setOnlyAlertOnce(true)
                .setOngoing(true)
                .setProgress(0, 0, true)
            manager.notify(tag, 0, notification.build())
            try {
                requestDownload(session, url).use { response ->
                    val mediaType = response.header("Content-Type")?.toMediaTypeOrNull()
                    val mime = mediaType?.let { "${it.type}/${it.subtype}" } ?: GENERIC_MIME
                    val name = downloadFileName(url, response.header("Content-Disposition"), mime)
                    val body = response.body
                    notification.setContentText(name)
                    manager.notify(tag, 0, notification.build())
                    val write: (OutputStream) -> Unit = { output ->
                        copyWithProgress(body, output) { percent ->
                            manager.notify(tag, 0, notification.setProgress(100, percent, false).build())
                        }
                    }
                    val file = if (Build.VERSION.SDK_INT >= 29) savePublic(app, name, mime, write)
                        else savePrivate(app, name, write)
                    // The files endpoint sends a generic type. Use the filename to select a viewer
                    // without depending on when Android finishes indexing the saved file.
                    val viewMime = if (mime == GENERIC_MIME) {
                        MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substringAfterLast('.', "").lowercase())
                            ?: mime
                    } else mime
                    val view = Intent(Intent.ACTION_VIEW).setDataAndType(file, viewMime)
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    val open = PendingIntent.getActivity(app, 0, view, PendingIntent.FLAG_IMMUTABLE)
                    manager.notify(tag, 0, Notification.Builder(app, CHANNEL)
                        .setSmallIcon(android.R.drawable.stat_sys_download_done)
                        .setContentTitle("下载完成")
                        .setContentText(name)
                        .setContentIntent(open)
                        .setAutoCancel(true)
                        .build())
                    showIfNotificationsOff(app, manager, "下载完成：$name")
                }
            } catch (failure: IOException) {
                val reason = when (failure) {
                    is DownloadException -> requireNotNull(failure.message)
                    is SocketTimeoutException -> "下载超时，请重试"
                    is ProtocolException -> "文件没有传完整，请重试"
                    else -> "连接中断或文件无法保存"
                }
                failed(app, manager, tag, reason, failure)
            } catch (failure: SecurityException) {
                failed(app, manager, tag, "手机不允许保存此文件", failure)
            } catch (failure: SQLException) {
                failed(app, manager, tag, "手机无法保存此文件", failure)
            } catch (failure: IllegalArgumentException) {
                failed(app, manager, tag, "手机无法保存此文件", failure)
            }
        }
    }

    private fun copyWithProgress(body: ResponseBody, output: OutputStream, progress: (Int) -> Unit) {
        val input = body.byteStream()
        val total = body.contentLength()
        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
        var copied = 0L
        var lastUpdate = SystemClock.elapsedRealtime()
        while (true) {
            val count = input.read(buffer)
            if (count == -1) break
            output.write(buffer, 0, count)
            copied += count
            val now = SystemClock.elapsedRealtime()
            if (total > 0 && now - lastUpdate >= 250) {
                progress((100.0 * copied / total).toInt().coerceIn(0, 100))
                lastUpdate = now
            }
        }
    }

    @RequiresApi(29)
    private fun savePublic(app: Context, name: String, mime: String, write: (OutputStream) -> Unit): Uri {
        val resolver = app.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, mime)
            put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: throw DownloadException("手机无法创建下载文件")
        var published = false
        return Closeable { if (!published) resolver.delete(uri, null, null) }.use {
            val output = resolver.openOutputStream(uri) ?: throw DownloadException("手机无法写入下载文件")
            output.use(write)
            val update = ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }
            if (resolver.update(uri, update, null, null) != 1) throw DownloadException("手机无法保存下载文件")
            published = true
            uri
        }
    }

    private fun savePrivate(app: Context, name: String, write: (OutputStream) -> Unit): Uri {
        val downloads = app.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
            ?: throw DownloadException("手机存储暂不可用")
        val directory = Files.createTempDirectory(downloads.toPath(), "crew-").toFile()
        val file = File(directory, name)
        var saved = false
        return Closeable {
            if (!saved) {
                Files.deleteIfExists(file.toPath())
                Files.deleteIfExists(directory.toPath())
            }
        }.use {
            file.outputStream().use(write)
            val uri = FileProvider.getUriForFile(app, "${app.packageName}.downloads", file)
            saved = true
            uri
        }
    }

    private suspend fun failed(app: Context, manager: NotificationManager, tag: String, reason: String, failure: Exception) {
        Log.w(TAG, reason, failure)
        manager.notify(tag, 0, Notification.Builder(app, CHANNEL)
            .setSmallIcon(R.drawable.ic_crew_notification)
            .setContentTitle("下载失败")
            .setContentText(reason)
            .setStyle(Notification.BigTextStyle().bigText(reason))
            .setAutoCancel(true)
            .build())
        showIfNotificationsOff(app, manager, reason)
    }

    private suspend fun showIfNotificationsOff(app: Context, manager: NotificationManager, message: String) {
        if (!manager.areNotificationsEnabled() ||
            manager.getNotificationChannel(CHANNEL).importance == NotificationManager.IMPORTANCE_NONE) {
            withContext(Dispatchers.Main) { Toast.makeText(app, message, Toast.LENGTH_LONG).show() }
        }
    }
}
