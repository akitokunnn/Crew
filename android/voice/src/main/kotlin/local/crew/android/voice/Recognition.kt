package local.crew.android.voice

import android.content.res.AssetManager
import com.k2fsa.sherpa.onnx.*
import java.util.Locale
import kotlin.math.sqrt

const val END_SILENCE_SECONDS = 1.1f
data class RecognitionFrame(val voiced: Boolean, val level: Float, val partial: String, val final: String?)

/** One instance per active capture. Overlapping readers must never share a native stream. */
class Recognition(
    assets: AssetManager,
    names: List<String>,
    private val listeningThreshold: Float = .50f,
    private val playbackThreshold: Float = .70f,
) : AutoCloseable {
    private val asrDir = "models/asr"
    private val cfg = OnlineRecognizerConfig(
        modelConfig = OnlineModelConfig(transducer = OnlineTransducerModelConfig(
            "$asrDir/encoder.int8.onnx", "$asrDir/decoder.onnx", "$asrDir/joiner.int8.onnx"),
            tokens = "$asrDir/tokens.txt", modelType = "zipformer2", numThreads = 2,
            modelingUnit = "bpe", bpeVocab = "x-asr.vocab"),
        decodingMethod = "modified_beam_search", enableEndpoint = false)
    private val vadCfg = VadModelConfig(sileroVadModelConfig = SileroVadModelConfig(
        model = "models/silero_vad.onnx", minSilenceDuration = END_SILENCE_SECONDS))
    init {
        for (path in listOf(cfg.modelConfig.transducer.encoder, cfg.modelConfig.transducer.decoder,
            cfg.modelConfig.transducer.joiner,cfg.modelConfig.tokens,cfg.modelConfig.bpeVocab,vadCfg.sileroVadModelConfig.model))
            assets.open(path).close()
    }
    private val recognizer = OnlineRecognizer(assets,cfg)
    private val vad = Vad(assets,vadCfg)
    private fun hotwords(names: List<String>) = names.map { it.replace(Regex("[/:#\\r\\n]"), " ").trim().uppercase(Locale.ROOT) }
        .filter { it.isNotEmpty() }.distinct().joinToString("/")
    private var words = hotwords(names)
    private var stream = recognizer.createStream(words)
    private val preRoll = ArrayDeque<FloatArray>()
    private var active = false
    private var silence = 0
    private var partial = ""
    fun updateNames(names: List<String>) {
        val next = hotwords(names)
        if (next == words) return
        words = next
        if (!active) reset()
    }
    fun accept(samples: FloatArray, playing: Boolean): RecognitionFrame {
        require(samples.size == WINDOW)
        val voiced = vad.compute(samples) >= if (playing) playbackThreshold else listeningThreshold
        val rms = sqrt(samples.sumOf { (it*it).toDouble() } / WINDOW).toFloat()
        if (!active && !voiced) {
            if (preRoll.size == 6) preRoll.removeFirst()
            preRoll.addLast(samples)
            return RecognitionFrame(false,rms,"",null)
        }
        if (!active) {
            active = true
            preRoll.forEach { stream.acceptWaveform(it,INPUT_RATE) }
            preRoll.clear()
        }
        stream.acceptWaveform(samples,INPUT_RATE)
        silence = if (voiced) 0 else silence + samples.size
        val endpoint = silence >= INPUT_RATE * END_SILENCE_SECONDS
        if (endpoint) stream.inputFinished()
        while (recognizer.isReady(stream)) recognizer.decode(stream)
        partial = recognizer.getResult(stream).text.trim()
        val result = RecognitionFrame(voiced,rms,partial,if (endpoint) partial else null)
        if (endpoint) reset()
        return result
    }
    fun reset() {
        stream.release()
        stream = recognizer.createStream(words)
        vad.reset()
        active = false
        silence = 0
        partial = ""
        preRoll.clear()
    }
    override fun close() { stream.release(); vad.release(); recognizer.release() }
}
