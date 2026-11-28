package local.crew.android

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.util.Log
import java.net.Inet4Address
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import local.crew.android.core.PairLink

internal data class NearbyComputer(val instanceId: String, val hostName: String, val baseUrl: String)

internal class DiscoveryException(message: String) : IllegalStateException(message)

internal class ComputerDiscovery(private val context: Context) {
    private val nsd = context.getSystemService(NsdManager::class.java)
    private val wifi = context.applicationContext.getSystemService(WifiManager::class.java)

    @OptIn(ExperimentalCoroutinesApi::class)
    @Suppress("DEPRECATION")
    fun nearby(): Flow<List<NearbyComputer>> = callbackFlow {
        val lock = wifi.createMulticastLock("Crew discovery").apply { setReferenceCounted(false); acquire() }
        val executor = context.mainExecutorCompat()
        val candidates = linkedMapOf<String, NearbyComputer>()
        val watching = mutableMapOf<String, NsdManager.ServiceInfoCallback>()
        val legacyQueue = linkedMapOf<String, NsdServiceInfo>()
        val discovered = mutableSetOf<String>()
        var resolving = false
        var started = false
        var closing = false

        fun publish() {
            trySend(candidates.values.distinctBy { it.instanceId to it.baseUrl }.toList())
        }

        fun found(key: String, info: NsdServiceInfo) {
            if (key !in discovered) return
            val ipv4 = if (Build.VERSION.SDK_INT >= 34) info.hostAddresses.filterIsInstance<Inet4Address>()
            else listOfNotNull(info.host as? Inet4Address)
            val id = info.attributes["id"]?.decodeToString()
            val name = info.attributes["name"]?.decodeToString()
            val version = info.attributes["v"]?.decodeToString()
            val origin = ipv4.firstNotNullOfOrNull { address ->
                try {
                    PairLink.origin("${address.hostAddress}:${info.port}")
                } catch (error: IllegalArgumentException) {
                    Log.d("CrewDiscovery", "忽略不支持的地址：${error.message}")
                    null
                }
            }
            if (id.isNullOrBlank() || name.isNullOrBlank() || version != "1" || origin == null) {
                candidates.remove(key)
                publish()
                return
            }
            candidates[key] = NearbyComputer(id, name, origin)
            publish()
        }

        fun resolveNext() {
            if (closing || resolving || legacyQueue.isEmpty()) return
            val (key, service) = legacyQueue.entries.first()
            legacyQueue.remove(key)
            resolving = true
            nsd.resolveService(service, object : NsdManager.ResolveListener {
                override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {
                    executor.execute {
                        Log.w("CrewDiscovery", "读取附近电脑地址失败（$errorCode）")
                        resolving = false
                        resolveNext()
                    }
                }
                override fun onServiceResolved(info: NsdServiceInfo) {
                    executor.execute {
                        found(key, info)
                        resolving = false
                        resolveNext()
                    }
                }
            })
        }

        val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(type: String) = executor.execute {
                started = true
                if (closing) nsd.stopServiceDiscovery(this)
            }
            override fun onDiscoveryStopped(type: String) = Unit
            override fun onStartDiscoveryFailed(type: String, errorCode: Int) {
                close(DiscoveryException("找不到附近的电脑（$errorCode）"))
            }
            override fun onStopDiscoveryFailed(type: String, errorCode: Int) {
                Log.w("CrewDiscovery", "停止寻找电脑失败（$errorCode）")
            }

            override fun onServiceFound(service: NsdServiceInfo) = executor.execute {
                val key = service.serviceName + if (Build.VERSION.SDK_INT >= 33) "@${service.network}" else ""
                if (closing || !discovered.add(key)) return@execute
                if (Build.VERSION.SDK_INT >= 34) {
                    val callback = object : NsdManager.ServiceInfoCallback {
                        override fun onServiceInfoCallbackRegistrationFailed(errorCode: Int) {
                            watching.remove(key)
                            discovered.remove(key)
                            Log.w("CrewDiscovery", "读取附近电脑信息失败（$errorCode）")
                        }
                        override fun onServiceUpdated(info: NsdServiceInfo) = found(key, info)
                        override fun onServiceLost() {
                            candidates.remove(key)
                            publish()
                        }
                        override fun onServiceInfoCallbackUnregistered() = Unit
                    }
                    watching[key] = callback
                    nsd.registerServiceInfoCallback(service, executor, callback)
                } else {
                    legacyQueue[key] = service
                    resolveNext()
                }
            }

            override fun onServiceLost(service: NsdServiceInfo) = executor.execute {
                val key = service.serviceName + if (Build.VERSION.SDK_INT >= 33) "@${service.network}" else ""
                discovered.remove(key)
                legacyQueue.remove(key)
                if (Build.VERSION.SDK_INT >= 34) watching.remove(key)?.let(nsd::unregisterServiceInfoCallback)
                candidates.remove(key)
                publish()
            }
        }
        try {
            nsd.discoverServices("_crew._tcp", NsdManager.PROTOCOL_DNS_SD, listener)
            awaitClose()
        } finally {
            closing = true
            discovered.clear()
            if (started) nsd.stopServiceDiscovery(listener)
            if (Build.VERSION.SDK_INT >= 34) watching.values.forEach(nsd::unregisterServiceInfoCallback)
            lock.release()
        }
    }
}

private fun Context.mainExecutorCompat() = androidx.core.content.ContextCompat.getMainExecutor(this)
