package local.crew.android.voice

import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.Assert.*
import org.junit.Test

class PcmTest {
    private fun wave(rate: Int, channels: Int, use: (File) -> Unit) {
        val pcm = ByteBuffer.allocate(60).order(ByteOrder.LITTLE_ENDIAN)
        fun tag(name: String) { pcm.put(name.toByteArray(Charsets.US_ASCII)) }
        tag("RIFF"); pcm.putInt(52); tag("WAVE")
        tag("JUNK"); pcm.putInt(3); pcm.put(byteArrayOf(1,2,3,0))
        tag("fmt "); pcm.putInt(16); pcm.putShort(1); pcm.putShort(channels.toShort())
        pcm.putInt(rate); pcm.putInt(rate*2*channels); pcm.putShort((channels*2).toShort()); pcm.putShort(16)
        tag("data"); pcm.putInt(4); pcm.putShort(16384); pcm.putShort(-16384)
        val file = File.createTempFile("voice-",".wav")
        try { file.writeBytes(pcm.array()); use(file) } finally { file.delete() }
    }
    @Test fun debugWavHandlesMetadataAndSampleSigns() = wave(16000,1) {
        val chunk = readPcmWave(it,requireInputFormat = true)
        assertEquals(16000,chunk.sampleRate)
        assertArrayEquals(floatArrayOf(.5f,-.5f),chunk.samples,.00001f)
    }
    @Test fun systemOutputCanBeStereoButInjectedMicMustBe16kMono() = wave(22050,2) {
        assertArrayEquals(floatArrayOf(0f),readPcmWave(it).samples,.00001f)
        assertThrows(IllegalArgumentException::class.java) { readPcmWave(it,true) }
        it.writeBytes(it.readBytes().dropLast(1).toByteArray())
        assertThrows(IllegalArgumentException::class.java) { readPcmWave(it) }
    }
}
