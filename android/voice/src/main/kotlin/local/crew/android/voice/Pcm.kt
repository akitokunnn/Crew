package local.crew.android.voice

import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder

data class PcmChunk(val samples: FloatArray, val sampleRate: Int, val sentenceStart: Float = 0f, val sentenceEnd: Float = 1f)

/** RIFF chunks can contain metadata or padding before data (not a fixed 44-byte header). */
fun readPcmWave(file: File, requireInputFormat: Boolean = false): PcmChunk = RandomAccessFile(file,"r").use { input ->
    fun tag() = ByteArray(4).also(input::readFully).toString(Charsets.US_ASCII)
    fun u32() = Integer.reverseBytes(input.readInt()).toLong() and 0xffffffffL
    fun u16() = java.lang.Short.reverseBytes(input.readShort()).toInt() and 65535
    require(tag() == "RIFF") { "不是 RIFF 音频" }
    u32()
    require(tag() == "WAVE") { "不是 WAV 音频" }
    var rate = 0
    var bits = 0
    var channels = 0
    var format = 0
    while (input.filePointer + 8 <= input.length()) {
        val name = tag()
        val size = u32()
        val end = input.filePointer + size
        require(end <= input.length()) { "WAV 数据不完整" }
        when (name) {
            "fmt " -> {
                require(size >= 16)
                format = u16(); channels = u16(); rate = u32().toInt()
                u32(); u16(); bits = u16()
            }
            "data" -> {
                require(!requireInputFormat || (rate == INPUT_RATE && channels == 1 && bits == 16 && format == 1)) {
                    "测试音频需要 16 kHz 单声道 16 位 PCM"
                }
                require(rate > 0 && channels in 1..2 && ((format == 1 && bits in listOf(8,16)) || (format == 3 && bits == 32))) {
                    "不支持的朗读音频 ($format, $bits bit, $channels 声道)"
                }
                require(size <= Int.MAX_VALUE && size % (channels*bits/8) == 0L)
                val data = ByteBuffer.wrap(ByteArray(size.toInt()).also(input::readFully)).order(ByteOrder.LITTLE_ENDIAN)
                val samples = FloatArray(size.toInt()/(channels*bits/8)) {
                    var sum = 0f
                    repeat(channels) {
                        sum += when (bits) {
                            8 -> ((data.get().toInt() and 255)-128)/128f
                            16 -> data.short/32768f
                            else -> data.float
                        }
                    }
                    sum/channels
                }
                return@use PcmChunk(samples,rate)
            }
        }
        input.seek(end + (size and 1))
    }
    throw IOException("WAV 缺少声音数据")
}
