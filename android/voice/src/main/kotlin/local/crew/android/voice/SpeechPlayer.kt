package local.crew.android.voice

import android.media.*
import java.io.IOException
import kotlinx.coroutines.*

/** Writes never block. stop() pauses and flushes the device on the same dispatcher as play(). */
class SpeechPlayer {
    private var track: AudioTrack? = null
    fun stop() { track?.let { it.pause(); it.flush() } }
    suspend fun play(chunk: PcmChunk, progress: (Float,Float) -> Unit) {
        val pcm = chunk.samples
        if (pcm.isEmpty()) throw IOException("朗读没有声音")
        val minimum = AudioTrack.getMinBufferSize(chunk.sampleRate, AudioFormat.CHANNEL_OUT_MONO,AudioFormat.ENCODING_PCM_FLOAT)
        check(minimum > 0) { "设备不支持此朗读采样率" }
        val output = AudioTrack.Builder().setAudioAttributes(speechAttributes)
            .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_FLOAT)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).setSampleRate(chunk.sampleRate).build())
            .setBufferSizeInBytes(maxOf(minimum,chunk.sampleRate/10*4)).setTransferMode(AudioTrack.MODE_STREAM).build()
        try {
            track = output
            output.play()
            var sent = 0
            // Pad very short clips so that the device starts its playback head.
            val total = maxOf(output.bufferSizeInFrames,pcm.size)
            val data = if (total == pcm.size) pcm else pcm.copyOf(total)
            while (true) {
                currentCoroutineContext().ensureActive()
                if (sent < total) {
                    val n = output.write(data,sent,minOf(total-sent,chunk.sampleRate/20),AudioTrack.WRITE_NON_BLOCKING)
                    if (n < 0) throw IOException("扬声器播放失败 ($n)")
                    sent += n
                }
                val played = output.playbackHeadPosition.coerceIn(0,pcm.size)
                progress(played.toFloat()/pcm.size,kotlin.math.abs(pcm[(played-1).coerceAtLeast(0)]))
                if (played == pcm.size) break
                delay(12)
            }
        } finally {
            output.pause(); output.flush(); output.release()
            if (track === output) track = null
        }
    }
}
