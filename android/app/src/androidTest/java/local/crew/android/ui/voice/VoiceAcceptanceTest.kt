package local.crew.android.ui.voice

import android.content.Intent
import android.os.SystemClock
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo
import androidx.test.platform.app.InstrumentationRegistry
import local.crew.android.MainActivity
import local.crew.android.core.ServerSessionsProvider
import local.crew.android.core.VoiceLaunch
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Host supplies a paired instance, project and private WAVs. Commands are scoped to this run. */
class VoiceAcceptanceTest {
    @Test
    fun exerciseCall() {
        val inst = InstrumentationRegistry.getInstrumentation()
        val ctx = inst.targetContext
        val args = InstrumentationRegistry.getArguments()
        val files = File(ctx.filesDir, "voice-acceptance")
        val command = File(files, "action")
        val wav = requireNotNull(args.getString("wav")).split(';').joinToString(";") {
            require('/' !in it && '\\' !in it)
            File(files, it).absolutePath
        }
        val instance = requireNotNull(args.getString("instance"))
        inst.startActivitySync(Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        val sessions = (ctx.applicationContext as ServerSessionsProvider).serverSessions
        val loadDeadline = SystemClock.elapsedRealtime() + 20_000
        while (sessions.byInstance(instance) == null && SystemClock.elapsedRealtime() < loadDeadline)
            SystemClock.sleep(100)
        checkNotNull(sessions.byInstance(instance)) { "Paired instance did not load" }
        val activity = Intent(ctx, VoiceActivity::class.java)
            .putExtra(VoiceLaunch.EXTRA_INSTANCE_ID, instance)
            .putExtra(VoiceLaunch.EXTRA_PROJECT, requireNotNull(args.getString("project")))
            .putExtra("crew.debug.wav", wav)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        inst.startActivitySync(activity)
        var previous = emptyList<String>()
        var sawVoicePage = false
        val deadline = SystemClock.elapsedRealtime() + 360_000
        while (SystemClock.elapsedRealtime() < deadline) {
            val nodes = mutableListOf<AccessibilityNodeInfo>()
            fun visit(node: AccessibilityNodeInfo) {
                nodes.add(node)
                for (i in 0 until node.childCount) node.getChild(i)?.let(::visit)
            }
            inst.uiAutomation.rootInActiveWindow?.let(::visit)
            val labels = nodes.flatMap { listOfNotNull(it.text?.toString(), it.contentDescription?.toString()) }
            if ("工作区助手" in labels && "更多选项" in labels) sawVoicePage = true
            if (labels != previous) {
                Log.i("CrewVoiceProbe", org.json.JSONArray(labels).toString())
                previous = labels
            }
            if (command.exists()) {
                when (val action = command.readText().trim()) {
                    "confirm" -> {
                        val label = nodes.first { it.text?.toString() == "确认执行" }
                        var button = label
                        while (!button.isClickable) button = requireNotNull(button.parent)
                        assertTrue(button.performAction(AccessibilityNodeInfo.ACTION_CLICK))
                        Log.i("CrewVoiceProbe", "clicked confirm")
                    }
                    "finish" -> {
                        assertTrue("Voice page was never observed", sawVoicePage)
                        command.delete()
                        return
                    }
                    else -> error("Unknown test command: $action")
                }
                check(command.delete())
            }
            SystemClock.sleep(110)
        }
        error("Host did not finish the call within six minutes")
    }
}
