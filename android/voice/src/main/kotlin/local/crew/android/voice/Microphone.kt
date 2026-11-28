package local.crew.android.voice

import android.annotation.SuppressLint
import android.content.Intent
import android.media.*
import android.media.audiofx.*
import android.os.Build
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import java.io.IOException

const val INPUT_RATE = 16000
const val WINDOW = 512
fun interface AudioInput { fun windows(): Flow<FloatArray> }
/** Optional source set contribution, discovered only if packaged with the build. */
fun interface LaunchAudioInput { fun from(intent: Intent): AudioInput? }

class Microphone(private val diagnostics: (String) -> Unit) : AudioInput {
    @SuppressLint("MissingPermission")
    override fun windows() = flow {
        val min = AudioRecord.getMinBufferSize(INPUT_RATE,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT)
        check(min > 0) { "麦克风缓冲区不可用 ($min)" }
        val record = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, INPUT_RATE,
            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min, WINDOW*4))
        val effects = mutableListOf<AudioEffect>()
        try {
            check(record.state == AudioRecord.STATE_INITIALIZED) { "麦克风初始化失败" }
            fun effect(label: String, available: Boolean, create: () -> AudioEffect?) {
                if (!available) { diagnostics("$label · 设备不支持"); return }
                fun failed(e: RuntimeException) = diagnostics("$label · " + e.javaClass.simpleName + " " + e.message)
                try {
                    val fx = create()
                    if (fx == null) { diagnostics("$label · 可用，创建失败"); return }
                    effects += fx
                    val status = fx.setEnabled(true)
                    diagnostics(if (status == AudioEffect.SUCCESS && fx.enabled) "$label · 已启用" else "$label · 启用失败 ($status)")
                } catch (e: UnsupportedOperationException) { failed(e) }
                catch (e: IllegalStateException) { failed(e) }
                catch (e: IllegalArgumentException) { failed(e) }
                catch (e: SecurityException) { failed(e) }
            }
            effect("回声消除", AcousticEchoCanceler.isAvailable()) { AcousticEchoCanceler.create(record.audioSessionId) }
            effect("噪声抑制", NoiseSuppressor.isAvailable()) { NoiseSuppressor.create(record.audioSessionId) }
            record.startRecording()
            check(record.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "未能开始录音" }
            val pcm = ShortArray(WINDOW)
            while (true) {
                var used = 0
                while (used < pcm.size) {
                    currentCoroutineContext().ensureActive()
                    val n = record.read(pcm, used, pcm.size-used, AudioRecord.READ_BLOCKING)
                    if (n <= 0) throw IOException("麦克风读取失败 ($n)")
                    used += n
                }
                emit(FloatArray(WINDOW) { pcm[it]/32768f })
            }
        } finally {
            if (record.recordingState == AudioRecord.RECORDSTATE_RECORDING) record.stop()
            effects.forEach { it.release() }
            record.release()
        }
    }
}

class CallAudioRoute(private val manager: AudioManager) : AutoCloseable {
    private val oldMode = manager.mode
    @Suppress("DEPRECATION") private val oldSpeaker = manager.isSpeakerphoneOn
    private val previous = if (Build.VERSION.SDK_INT >= 31) manager.communicationDevice else null
    init { manager.mode = AudioManager.MODE_IN_COMMUNICATION }
    @Suppress("DEPRECATION")
    fun speaker(on: Boolean) {
        if (Build.VERSION.SDK_INT >= 31) {
            val type = if (on) AudioDeviceInfo.TYPE_BUILTIN_SPEAKER else AudioDeviceInfo.TYPE_BUILTIN_EARPIECE
            val device = manager.availableCommunicationDevices.firstOrNull { it.type == type }
                ?: throw IOException(if (on) "没有可用的扬声器" else "这台设备没有可用的听筒")
            check(manager.setCommunicationDevice(device)) { "声音输出切换失败" }
        } else manager.isSpeakerphoneOn = on
    }
    @Suppress("DEPRECATION")
    override fun close() {
        if (Build.VERSION.SDK_INT >= 31) {
            manager.clearCommunicationDevice()
            if (previous != null && manager.availableCommunicationDevices.any { it.id == previous.id })
                manager.setCommunicationDevice(previous)
        } else manager.isSpeakerphoneOn = oldSpeaker
        manager.mode = oldMode
    }
}
