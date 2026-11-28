package local.crew.android.voice

import android.content.Context
import android.content.res.AssetManager
import android.media.*
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import com.k2fsa.sherpa.onnx.*
import java.io.File
import java.io.IOException
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*

interface SpeechSynth : AutoCloseable {
    fun synth(text: String): Flow<PcmChunk>
    fun cancel()
}

private const val AISHELL_SPEAKER = 11

val speechAttributes: AudioAttributes get() = AudioAttributes.Builder()
    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()

// sherpa JNI looks up invoke(float[]): Integer on the concrete callback class.
internal class SynthesisCallback(private val active: () -> Boolean) : (FloatArray) -> Int {
    override fun invoke(samples: FloatArray): Int = if (active()) 1 else 0
}

class BuiltInSpeech(assets: AssetManager) : SpeechSynth {
    private val base = "models/tts"
    private val generation = AtomicLong()
    init {
        listOf("model.onnx","tokens.txt","lexicon.txt","phone.fst","date.fst","number.fst").forEach { assets.open("$base/$it").close() }
    }
    private val engine = OfflineTts(assets,OfflineTtsConfig(
        model = OfflineTtsModelConfig(vits = OfflineTtsVitsModelConfig(model = "$base/model.onnx",
            lexicon = "$base/lexicon.txt",tokens = "$base/tokens.txt",noiseScale = .1f,noiseScaleW = .1f),numThreads = 2),
        ruleFsts = listOf("phone","date","number").joinToString(",") { "$base/$it.fst" }))
    override fun synth(text: String) = flow {
        val ticket = generation.get()
        val job = currentCoroutineContext().job
        val audio = synchronized(engine) {
            val callback = SynthesisCallback { job.isActive && generation.get() == ticket }
            engine.generateWithConfigAndCallback(text, GenerationConfig(sid = AISHELL_SPEAKER, speed = 1f), callback)
        }
        currentCoroutineContext().ensureActive()
        if (generation.get() == ticket) {
            check(audio.samples.isNotEmpty()) { "内置音色没有生成声音" }
            emit(PcmChunk(audio.samples,audio.sampleRate))
        }
    }.flowOn(Dispatchers.Default)
    override fun cancel() { generation.incrementAndGet() }
    override fun close() { cancel(); synchronized(engine) { engine.release() } }
}

class SystemSpeech private constructor(private val engine: TextToSpeech, private val dir: File) : SpeechSynth {
    private val completions = ConcurrentHashMap<String,CompletableDeferred<Unit>>()
    init {
        engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String) = Unit
            override fun onDone(id: String) { completions[id]?.complete(Unit) }
            @Deprecated("Platform callback") override fun onError(id: String) { onError(id,TextToSpeech.ERROR) }
            override fun onError(id: String, code: Int) {
                completions[id]?.completeExceptionally(IOException("系统朗读失败 ($code)"))
            }
            override fun onStop(id: String, interrupted: Boolean) { completions[id]?.cancel() }
        })
    }
    override fun synth(text: String) = flow {
        // Some engines reject long input even when the whole reply is a single unpunctuated sentence.
        var start = 0
        val length = text.codePointCount(0,text.length).toFloat()
        while (start < text.length) {
            var end = minOf(text.length,start+TextToSpeech.getMaxSpeechInputLength())
            if (end < text.length && text[end-1].isHighSurrogate()) end--
            val id = UUID.randomUUID().toString()
            val done = CompletableDeferred<Unit>()
            val file = File.createTempFile("voice-", ".wav",dir)
            try {
                completions[id] = done
                check(engine.synthesizeToFile(text.substring(start,end),Bundle(),file,id) == TextToSpeech.SUCCESS) { "系统没有接受朗读请求" }
                done.await()
                emit(readPcmWave(file).copy(sentenceStart = text.codePointCount(0,start)/length,
                    sentenceEnd = text.codePointCount(0,end)/length))
            } finally {
                completions.remove(id)
                if (!file.delete() && file.exists()) android.util.Log.w("CrewVoice","未能清理朗读缓存")
            }
            start = end
        }
    }.flowOn(Dispatchers.IO)
    override fun cancel() { engine.stop(); completions.values.forEach { it.cancel() } }
    override fun close() { cancel(); engine.shutdown() }
    companion object {
        suspend fun discover(context: Context, diagnostic: (String) -> Unit): SystemSpeech? {
            val ready = CompletableDeferred<Int>()
            val tts = TextToSpeech(context.applicationContext) { ready.complete(it) }
            var transfer = false
            try {
                val result = ready.await()
                if (result != TextToSpeech.SUCCESS) { diagnostic("系统语音服务未就绪 ($result)"); return null }
                val voice = tts.voices.orEmpty().filter {
                    it.locale.language == "zh" && !it.isNetworkConnectionRequired &&
                        TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED !in it.features.orEmpty()
                }.maxByOrNull { it.quality }
                if (voice == null) { diagnostic("未安装可离线使用的中文系统音色"); return null }
                check(tts.setVoice(voice) == TextToSpeech.SUCCESS) { "无法启用系统中文音色" }
                check(tts.setAudioAttributes(speechAttributes) == TextToSpeech.SUCCESS) { "无法设置系统朗读的通话音频" }
                diagnostic("系统音色 · " + voice.name)
                val synth = SystemSpeech(tts,context.cacheDir)
                transfer = true
                return synth
            } finally { if (!transfer) tts.shutdown() }
        }
    }
}
