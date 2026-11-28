package local.crew.android.core

data class PairLink(val baseUrl: String, val code: String) {
    companion object {
        private val link = Regex("""http://([^/]+)/remote/login#code=([0-9]{8})""")
        private val endpoint = Regex("""((?:[0-9]{1,3}\.){3}[0-9]{1,3}):([0-9]{1,5})""")

        fun parse(text: String, allowLoopback: Boolean = false): PairLink {
            val match = requireNotNull(link.matchEntire(text.trim())) {
                "配对链接必须使用 http://，并包含 8 位数字配对码"
            }
            return PairLink(origin(match.groupValues[1], allowLoopback), match.groupValues[2])
        }

        fun origin(address: String, allowLoopback: Boolean = false): String {
            val match = requireNotNull(endpoint.matchEntire(address.trim())) {
                "请输入数字 IPv4 地址和端口，例如 192.168.1.5:8765"
            }
            val parts = match.groupValues[1].split('.')
            val octets = parts.map(String::toInt)
            require(parts == octets.map(Int::toString) && octets.all { it in 0..255 }) {
                "IPv4 地址无效"
            }
            val (first, second) = octets
            require(first == 10 || first == 172 && second in 16..31 ||
                first == 192 && second == 168 || allowLoopback && first == 127) {
                "请使用电脑的局域网 IPv4 地址"
            }
            val port = match.groupValues[2].toInt()
            require(port in 1..65535) { "端口必须在 1 到 65535 之间" }
            return "http://${octets.joinToString(".")}" + if (port == 80) "" else ":$port"
        }
    }
}
