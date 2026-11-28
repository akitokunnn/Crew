package local.crew.android.ui.voice

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import local.crew.android.core.*
import local.crew.android.ui.theme.CrewTheme
import local.crew.android.voice.*
import java.util.ServiceLoader

fun interface SessionLookup { fun byInstance(instanceId: String): ServerSession? }

/** The manifest's VoiceActivity subclasses this and supplies the paired Application lookup. */
abstract class VoiceCallActivity : ComponentActivity() {
    // Integration: SessionLookup((application as ServerSessionsProvider).serverSessions::byInstance)
    protected abstract val sessionLookup: SessionLookup
    private var granted by mutableStateOf(false)
    private var asked = false
    private val permission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted = it }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val instance = intent.getStringExtra(VoiceLaunch.EXTRA_INSTANCE_ID)
        val project = intent.getStringExtra(VoiceLaunch.EXTRA_PROJECT)
        val session = try { instance?.let(sessionLookup::byInstance) }
        catch (e: IllegalStateException) { returnToText("无法读取配对信息 · " + e.message); return }
        if (session == null || project.isNullOrBlank()) { returnToText("连接已失效，请从工作区重新打开"); return }
        try { session.cookieHeader() }
        catch (e: IllegalStateException) { returnToText("连接已失效 · " + e.message); return }
        enableEdgeToEdge()
        val input = ServiceLoader.load(LaunchAudioInput::class.java, classLoader).singleOrNull()?.from(intent)
        val prefs = getSharedPreferences("voice-progress",MODE_PRIVATE)
        // A fresh call starts at the current tail. Resuming this call reuses its own processed sequence.
        val cursorKey = java.util.UUID.randomUUID().toString()
        val cursor = object : CallCursor {
            override fun read() = prefs.getLong(cursorKey,0)
            override fun save(seq: Long) { prefs.edit().putLong(cursorKey,seq).apply() }
        }
        lifecycle.addObserver(object : androidx.lifecycle.DefaultLifecycleObserver {
            override fun onDestroy(owner: androidx.lifecycle.LifecycleOwner) { prefs.edit().remove(cursorKey).apply() }
        })
        // OkHttp interceptors must report I/O failures to their callback, including credentials revoked during a call.
        val transportSession = object : ServerSession by session {
            override fun cookieHeader() = try { session.cookieHeader() }
            catch (e: IllegalStateException) { throw VoiceHttpError(401,e.message ?: "配对已失效") }
        }
        val controller = VoiceCallController(this,project,CrewVoiceApi(session,project,CrewHttp.client(transportSession)),cursor,input,::returnToText)
        granted = hasMic()
        setContent {
            CrewTheme {
                Surface(Modifier.fillMaxSize(),color = MaterialTheme.colorScheme.background) {
                    if (granted || input != null) {
                        val lifecycle = LocalLifecycleOwner.current.lifecycle
                        var attempt by remember { mutableIntStateOf(0) }
                        LaunchedEffect(controller,lifecycle,attempt) { lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) { controller.visible() } }
                        VoiceScreen(controller,::finish) { attempt++ }
                    } else {
                        Column(Modifier.safeDrawingPadding().padding(24.dp),verticalArrangement = Arrangement.Center,
                            horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("允许使用麦克风",style = MaterialTheme.typography.headlineSmall)
                            Text("开启后就能说话。离开通话页，录音就会停止。",Modifier.padding(vertical = 16.dp))
                            Button(onClick = { startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,Uri.parse("package:" + packageName))) }) { Text("去设置打开") }
                            TextButton(onClick = ::finish) { Text("改用文字") }
                        }
                    }
                }
            }
        }
        if (!granted && input == null && savedInstanceState == null && !asked) {
            asked = true
            permission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }
    private fun hasMic() = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
    override fun onResume() { super.onResume(); granted = hasMic() }
    private fun returnToText(reason: String) { Toast.makeText(this,reason,Toast.LENGTH_LONG).show(); finish() }
}
