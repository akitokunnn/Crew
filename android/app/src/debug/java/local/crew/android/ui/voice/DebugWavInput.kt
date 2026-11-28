package local.crew.android.ui.voice

import android.content.Intent
import android.util.Log
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.flow
import local.crew.android.voice.*
import java.io.File

class DebugWavInput : LaunchAudioInput {
    override fun from(intent: Intent): AudioInput? {
        val paths = intent.getStringExtra("crew.debug.wav") ?: return null
        return AudioInput {
            flow {
                var window = FloatArray(WINDOW)
                var used = 0
                suspend fun append(samples: FloatArray) {
                    var index = 0
                    while (index < samples.size) {
                        val take = minOf(samples.size-index,WINDOW-used)
                        samples.copyInto(window,used,index,index+take)
                        used += take
                        index += take
                        if (used == WINDOW) {
                            emit(window)
                            delay(WINDOW * 1000L / INPUT_RATE)
                            used = 0
                            window = FloatArray(WINDOW)
                        }
                    }
                }
                for (entry in paths.split(';')) {
                    val gated = entry.endsWith("@wait")
                    val path = if (gated) entry.removeSuffix("@wait") else entry
                    val file = File(path)
                    require(file.isAbsolute) { "测试音频需要绝对路径" }
                    if (gated) {
                        val gate = File(path + ".ready")
                        while (!gate.exists()) delay(25)
                    }
                    val audio = readPcmWave(file, requireInputFormat = true)
                    Log.i("CrewWav", "begin ${file.name}")
                    append(audio.samples)
                    Log.i("CrewWav", "end ${file.name}")
                    append(FloatArray(INPUT_RATE*3/2))
                }
                if (used != 0) emit(window)
            }
        }
    }
}
