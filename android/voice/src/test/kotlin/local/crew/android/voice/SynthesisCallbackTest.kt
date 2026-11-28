package local.crew.android.voice

import org.junit.Assert.assertEquals
import org.junit.Test

class SynthesisCallbackTest {
    @Test fun nativeCallbackHasSherpaSignatureAndHonorsCancellation() {
        var active = true
        val callback = SynthesisCallback { active }
        val invoke = callback.javaClass.getMethod("invoke", FloatArray::class.java)
        assertEquals(Int::class.javaObjectType, invoke.returnType)
        assertEquals(1, invoke.invoke(callback, floatArrayOf()))
        active = false
        assertEquals(0, invoke.invoke(callback, floatArrayOf()))
    }
}
