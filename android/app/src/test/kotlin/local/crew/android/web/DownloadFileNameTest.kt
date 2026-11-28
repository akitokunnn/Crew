package local.crew.android.web

import android.app.Application
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], application = Application::class)
class DownloadFileNameTest {
    private val url = "http://192.168.1.2/files/report.pdf?id=7".toHttpUrl()
    private val mime = "application/pdf"

    @Test fun prefersExtendedFilenameAndKeepsLiteralPlus() {
        assertEquals("中文.pdf", downloadFileName(url, "attachment; filename*=UTF-8''中文.pdf", mime))
        assertEquals("中文+01.pdf", downloadFileName(
            url, "attachment; filename=\"report.pdf\"; filename*=UTF-8''%E4%B8%AD%E6%96%87+01.pdf", mime,
        ))
    }

    @Test fun parsesQuotedNamesIncludingSemicolons() {
        assertEquals("quarter; 4.pdf", downloadFileName(url, "attachment; filename=\"quarter; 4.pdf\"", mime))
        assertEquals("preview.pdf", downloadFileName(url, "inline; filename=\"preview.pdf\"", mime))
        assertEquals("first+second.pdf", downloadFileName(
            url, "attachment; filename=\"first+second.pdf\"; filename*=UTF-8''bad%ZZ.pdf", mime,
        ))
    }

    @Test fun usesPlatformGuessWhenNoUsableHeaderIsPresent() {
        assertEquals("report.pdf", downloadFileName(url, null, mime))
        assertEquals("report.pdf", downloadFileName(url, "attachment; filename=\"..\"", mime))
    }

    @Test fun filenameCannotEscapeTheDestination() {
        assertEquals("_other_report.pdf", downloadFileName(
            url, "attachment; filename*=UTF-8''..%2Fother%2Freport.pdf", mime,
        ))
    }
}
