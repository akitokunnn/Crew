package local.crew.android

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import java.security.KeyStore
import java.security.GeneralSecurityException
import java.security.ProviderException
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import local.crew.android.core.PairedComputer
import local.crew.android.core.PairingResult
import local.crew.android.core.ServerSession
import local.crew.android.core.ServerSessions

private val Context.computerData by preferencesDataStore("paired_computers")

internal interface PairedComputers : ServerSessions {
    val state: kotlinx.coroutines.flow.StateFlow<ComputerState>
    suspend fun load()
    suspend fun save(result: PairingResult): ServerSession
    suspend fun select(computer: PairedComputer): Boolean
    suspend fun confirm(computer: PairedComputer, hostName: String): PairedComputer?
    suspend fun remove(computer: PairedComputer): ServerSession?
    fun cursor(instanceId: String): Long?
    suspend fun markProcessed(computer: PairedComputer, seq: Long)
}

internal data class ComputerState(
    val records: List<PairedComputer> = emptyList(),
    val currentId: String? = null,
)

internal class ComputerRepository(context: Context) : PairedComputers {
    private val data = context.computerData
    private val writes = Mutex()
    private val changes = MutableStateFlow(ComputerState())
    override val state = changes.asStateFlow()
    @Volatile private var prefs: Preferences? = null

    override suspend fun load() {
        writes.withLock {
            if (prefs == null) publish(data.data.first())
        }
    }

    override fun current(): ServerSession? = prefs?.let { values ->
        values[CURRENT]?.let { id -> entry(values, id)?.let(::liveSession) }
    }

    override fun byInstance(instanceId: String): ServerSession? =
        prefs?.let { entry(it, instanceId)?.let(::liveSession) }

    override suspend fun save(result: PairingResult): ServerSession {
        val computer = result.computer
        val encrypted = withContext(Dispatchers.IO) { CookieVault.encrypt(computer, result.cookie.value) }
        change {
            it[recordKey(computer.instanceId)] = Json.encodeToString(Entry(computer, encrypted))
            it[CURRENT] = computer.instanceId
        }
        return liveSession(Entry(computer, encrypted))
    }

    override suspend fun select(computer: PairedComputer): Boolean {
        var selected = false
        change {
            if (matches(entry(it, computer.instanceId), computer)) {
                it[CURRENT] = computer.instanceId
                selected = true
            }
        }
        return selected
    }

    override suspend fun confirm(computer: PairedComputer, hostName: String): PairedComputer? {
        var updated: PairedComputer? = null
        change {
            val entry = entry(it, computer.instanceId)
            if (entry != null && matches(entry, computer)) {
                val record = entry.computer.copy(hostName = hostName, lastOkAt = System.currentTimeMillis())
                it[recordKey(computer.instanceId)] = Json.encodeToString(entry.copy(computer = record))
                updated = record
            }
        }
        return updated
    }

    override suspend fun remove(computer: PairedComputer): ServerSession? {
        var removed: Entry? = null
        change {
            val entry = entry(it, computer.instanceId)
            if (entry != null && matches(entry, computer)) {
                it.remove(recordKey(computer.instanceId))
                it.remove(cursorKey(computer.instanceId))
                if (it[CURRENT] == computer.instanceId) it.remove(CURRENT)
                removed = entry
            }
        }
        return removed?.let { detached -> session(detached) { decrypt(detached) } }
    }

    override fun cursor(instanceId: String): Long? = prefs?.get(cursorKey(instanceId))

    override suspend fun markProcessed(computer: PairedComputer, seq: Long) {
        change {
            if (matches(entry(it, computer.instanceId), computer)) {
                val key = cursorKey(computer.instanceId)
                if (seq > (it[key] ?: -1)) it[key] = seq
            }
        }
    }

    private suspend fun change(update: (androidx.datastore.preferences.core.MutablePreferences) -> Unit) {
        writes.withLock { withContext(NonCancellable) { publish(data.edit { update(it) }) } }
    }

    private fun publish(values: Preferences) {
        val computers = values.asMap().keys.filter { it.name.startsWith(RECORD) }
            .map { key -> requireNotNull(entry(values, key.name.removePrefix(RECORD))).computer }
            .sortedByDescending { it.lastOkAt }
        prefs = values
        changes.value = ComputerState(computers, values[CURRENT])
    }

    private fun liveSession(initial: Entry) = session(initial) {
        val latest = prefs?.let { entry(it, initial.computer.instanceId) }
        check(latest != null && matches(latest, initial.computer)) { "这台电脑的配对已失效" }
        decrypt(latest)
    }

    private fun decrypt(entry: Entry): String = try {
        CookieVault.decrypt(entry)
    } catch (error: GeneralSecurityException) {
        throw IllegalStateException("手机无法读取配对凭据（${error.javaClass.simpleName}）", error)
    } catch (error: ProviderException) {
        throw IllegalStateException("手机无法读取配对凭据（${error.javaClass.simpleName}）", error)
    }

    private fun session(entry: Entry, cookieValue: () -> String) = object : ServerSession {
        override val instanceId = entry.computer.instanceId
        override val hostName = entry.computer.hostName
        override val baseUrl = entry.computer.baseUrl
        override fun cookieHeader() = "${entry.computer.cookieName}=${cookieValue()}"
    }

    private fun matches(entry: Entry?, computer: PairedComputer) =
        entry?.computer?.deviceId == computer.deviceId && entry.computer.baseUrl == computer.baseUrl

    private fun entry(values: Preferences, id: String): Entry? =
        values[recordKey(id)]?.let { Json.decodeFromString<Entry>(it) }

    companion object {
        private const val RECORD = "computer."
        private val CURRENT = stringPreferencesKey("current")
        private fun recordKey(id: String) = stringPreferencesKey("$RECORD$id")
        private fun cursorKey(id: String) = longPreferencesKey("cursor.$id")
    }
}

@Serializable
private data class Entry(val computer: PairedComputer, val encrypted: String)

private object CookieVault {
    private const val KEY = "crew.paired-computers"
    private const val IV_BYTES = 12

    @Synchronized
    private fun key(create: Boolean): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(KEY, null) as SecretKey?)?.let { return it }
        check(create) { "手机已无法读取配对记录，请重新配对" }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(KEY, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build())
        }.generateKey()
    }

    private fun cipher() = Cipher.getInstance("AES/GCM/NoPadding")

    private fun binding(computer: PairedComputer): ByteArray = with(computer) {
        "$instanceId\n$baseUrl\n$deviceId\n$cookieName".toByteArray()
    }

    fun encrypt(computer: PairedComputer, secret: String): String {
        val cipher = cipher()
        cipher.init(Cipher.ENCRYPT_MODE, key(create = true))
        cipher.updateAAD(binding(computer))
        return Base64.encodeToString(cipher.iv + cipher.doFinal(secret.toByteArray()), Base64.NO_WRAP)
    }

    fun decrypt(entry: Entry): String {
        val bytes = Base64.decode(entry.encrypted, Base64.NO_WRAP)
        val cipher = cipher()
        cipher.init(Cipher.DECRYPT_MODE, key(create = false), GCMParameterSpec(128, bytes, 0, IV_BYTES))
        cipher.updateAAD(binding(entry.computer))
        return cipher.doFinal(bytes, IV_BYTES, bytes.size - IV_BYTES).decodeToString()
    }
}
