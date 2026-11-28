package local.crew.android.web

import android.webkit.URLUtil
import java.io.IOException
import java.net.URLDecoder
import local.crew.android.core.CrewHttp
import local.crew.android.core.ServerSession
import okhttp3.HttpUrl
import okhttp3.Request
import okhttp3.Response

internal class DownloadException(message: String) : IOException(message)

internal fun requestDownload(session: ServerSession, url: HttpUrl): Response {
    val response = CrewHttp.client(session).newCall(Request.Builder().url(url).get().build()).execute()
    if (response.isSuccessful) return response
    response.use {
        if (it.code in 300..399) throw DownloadException("下载地址跳转到了别处，已停止")
        throw DownloadException("电脑未能提供文件（${it.code}）")
    }
}

internal fun downloadFileName(url: HttpUrl, disposition: String?, mimeType: String): String {
    if (disposition != null) {
        val parameters = dispositionParameter.findAll(disposition.substringAfter(';', "")).associate { match ->
            val (key, quoted, unquoted) = match.destructured
            key.lowercase() to if (match.groups[2] != null) quotedPair.replace(quoted, "$1") else unquoted.trim()
        }
        val extended = parameters["filename*"]?.let(::decodeExtendedFileName)?.let(::safeFileName)
        if (!extended.isNullOrEmpty()) return extended
        val simple = parameters["filename"]?.let(::safeFileName)
        if (!simple.isNullOrEmpty()) return simple
    }
    return safeFileName(URLUtil.guessFileName(url.toString(), null, mimeType)).ifEmpty { "download" }
}

private val dispositionParameter = Regex("""(?:^|;)\s*([^=;\s]+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;]*))""")
private val quotedPair = Regex("""\\(.)""")
private val invalidPercentEscape = Regex("%(?![0-9a-fA-F]{2})")

private fun decodeExtendedFileName(value: String): String? {
    val fields = value.split('\'', limit = 3)
    if (fields.size != 3) return null
    val charset = when {
        fields[0].equals("UTF-8", ignoreCase = true) -> "UTF-8"
        fields[0].equals("ISO-8859-1", ignoreCase = true) -> "ISO-8859-1"
        else -> return null
    }
    val encoded = fields[2]
    if (invalidPercentEscape.containsMatchIn(encoded)) return null
    // A plus is literal in RFC 5987. URLDecoder treats it as a form-encoded space.
    return URLDecoder.decode(encoded.replace("+", "%2B"), charset)
}

private fun safeFileName(name: String): String = name.map {
    if (it.isISOControl() || it in "/\\:*?\"<>|") '_' else it
}.joinToString("").trim { it.isWhitespace() || it == '.' }
