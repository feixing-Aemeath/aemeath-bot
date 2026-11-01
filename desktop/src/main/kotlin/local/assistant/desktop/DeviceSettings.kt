package local.assistant.desktop

import com.sun.jna.platform.win32.Crypt32Util
import local.assistant.core.ChatChoice
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.util.Properties

fun isWindows(os: String = System.getProperty("os.name")): Boolean = os.startsWith("Windows", ignoreCase = true)

fun atomicWrite(path: Path, bytes: ByteArray) {
    Files.createDirectories(path.parent)
    val temporary = Files.createTempFile(path.parent, ".aemeath-", ".tmp")
    try {
        Files.write(temporary, bytes)
        try {
            Files.move(temporary, path, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } catch (_: java.nio.file.AtomicMoveNotSupportedException) {
            Files.move(temporary, path, StandardCopyOption.REPLACE_EXISTING)
        }
    } finally {
        Files.deleteIfExists(temporary)
    }
}

interface SecretCodec {
    fun encrypt(bytes: ByteArray): ByteArray
    fun decrypt(bytes: ByteArray): ByteArray
}

object WindowsDpapi : SecretCodec {
    // No CRYPTPROTECT_LOCAL_MACHINE flag: only this Windows account can decrypt it.
    override fun encrypt(bytes: ByteArray): ByteArray = Crypt32Util.cryptProtectData(bytes)
    override fun decrypt(bytes: ByteArray): ByteArray = Crypt32Util.cryptUnprotectData(bytes)
}

class TokenStore(
    private val path: Path,
    private val windows: Boolean = isWindows(),
    private val codec: SecretCodec = WindowsDpapi,
) {
    private var session: String? = null

    fun read(): String? {
        if (!windows) return session
        return runCatching {
            if (Files.exists(path)) codec.decrypt(Files.readAllBytes(path)).decodeToString() else null
        }.getOrNull()
    }

    fun save(value: String) {
        val token = value.trim().takeIf { it.isNotEmpty() }
        if (!windows) {
            session = token
            return
        }
        if (token == null) {
            Files.deleteIfExists(path)
        } else {
            atomicWrite(path, codec.encrypt(token.toByteArray(Charsets.UTF_8)))
        }
    }
}

class DeviceSettings(val directory: Path = defaultDirectory()) {
    private val path = directory.resolve("device.properties")
    private val properties = Properties().apply {
        if (Files.exists(path)) runCatching { Files.newInputStream(path).use { load(it) } }
    }
    val tokens = TokenStore(directory.resolve("token.dpapi"))

    private fun set(key: String, value: String) {
        properties.setProperty(key, value)
        val output = java.io.ByteArrayOutputStream()
        properties.store(output, "Aemeath Bot device preferences; credentials are stored separately")
        atomicWrite(path, output.toByteArray())
    }

    var baseUrl: String
        get() = properties.getProperty("url", "http://127.0.0.1:8765")
        set(value) = set("url", value)
    var mock: Boolean
        get() = properties.getProperty("mock", "true").toBoolean()
        set(value) = set("mock", value.toString())
    var chosen: String
        get() = properties.getProperty("chosen", "assistant")
        set(value) = set("chosen", value)
    var choice: ChatChoice
        get() = ChatChoice.restore(properties.getProperty("choice"))
        set(value) = set("choice", value.stored)
    var style: String
        get() = properties.getProperty("style", "plain")
        set(value) = set("style", value)
    val firstLaunch: Long
        get() = properties.getProperty("firstLaunch")?.toLongOrNull() ?: System.currentTimeMillis().also {
            set("firstLaunch", it.toString())
        }

    fun seen(id: String): Long = properties.getProperty("seen.$id")?.toLongOrNull() ?: firstLaunch
    fun markSeen(id: String, at: Long = System.currentTimeMillis()) = set("seen.$id", at.toString())

    companion object {
        fun defaultDirectory(): Path {
            val root = if (isWindows()) System.getenv("LOCALAPPDATA") else null
            return Path.of(root ?: System.getProperty("user.home"), if (root != null) "Aemeath Bot" else ".aemeath-desktop")
        }
    }
}
