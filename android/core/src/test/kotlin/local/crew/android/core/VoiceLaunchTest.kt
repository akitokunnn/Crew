package local.crew.android.core

import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceLaunchTest {
    @Test
    fun intentExtrasHaveDistinctNonBlankKeys() {
        assertTrue(VoiceLaunch.EXTRA_INSTANCE_ID.isNotBlank())
        assertTrue(VoiceLaunch.EXTRA_PROJECT.isNotBlank())
        assertNotEquals(VoiceLaunch.EXTRA_INSTANCE_ID, VoiceLaunch.EXTRA_PROJECT)
    }
}
