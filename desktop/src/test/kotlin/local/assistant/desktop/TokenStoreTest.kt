package local.assistant.desktop

import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.nio.file.Files

class TokenStoreTest {
    @get:Rule
    val temporary = TemporaryFolder()

    private val forbidden = object : SecretCodec {
        override fun encrypt(bytes: ByteArray): ByteArray = error("Native encryption must not be called")
        override fun decrypt(bytes: ByteArray): ByteArray = error("Native decryption must not be called")
    }

    private val reversible = object : SecretCodec {
        override fun encrypt(bytes: ByteArray): ByteArray = bytes.map { (it.toInt() xor 0x5A).toByte() }.toByteArray()
        override fun decrypt(bytes: ByteArray): ByteArray = encrypt(bytes)
    }

    @Test
    fun nonWindowsTokenNeverCreatesADirectoryOrFile() {
        val path = temporary.root.toPath().resolve("absent/token.dpapi")
        val store = TokenStore(path, windows = false, codec = forbidden)
        store.save(" test-secret ")
        assertEquals("test-secret", store.read())
        assertFalse(Files.exists(path.parent))
    }

    @Test
    fun nonWindowsRestartDoesNotRestoreThePreviousSession() {
        val path = temporary.root.toPath().resolve("token.dpapi")
        TokenStore(path, false, forbidden).save("session-secret")
        assertNull(TokenStore(path, false, forbidden).read())
    }

    @Test
    fun nonWindowsNeverReadsAnExistingEncryptedToken() {
        val path = temporary.root.toPath().resolve("token.dpapi")
        Files.write(path, byteArrayOf(1, 2, 3))
        assertNull(TokenStore(path, false, forbidden).read())
        assertArrayEquals(byteArrayOf(1, 2, 3), Files.readAllBytes(path))
    }

    @Test
    fun nonWindowsCanClearTheSessionToken() {
        val store = TokenStore(temporary.root.toPath().resolve("token.dpapi"), false, forbidden)
        store.save("secret")
        store.save("")
        assertNull(store.read())
    }

    @Test
    fun windowsPersistsOnlyTheCodecOutputAndRestoresIt() {
        val path = temporary.root.toPath().resolve("nested/token.dpapi")
        TokenStore(path, true, reversible).save("test-secret")
        assertFalse(Files.readAllBytes(path).decodeToString().contains("test-secret"))
        assertEquals("test-secret", TokenStore(path, true, reversible).read())
        assertEquals(listOf("token.dpapi"), path.parent.toFile().list()!!.toList())
    }

    @Test
    fun encryptionFailureKeepsThePreviouslySavedToken() {
        val path = temporary.root.toPath().resolve("token.dpapi")
        TokenStore(path, true, reversible).save("old-secret")
        assertTrue(runCatching { TokenStore(path, true, forbidden).save("new-secret") }.isFailure)
        assertEquals("old-secret", TokenStore(path, true, reversible).read())
    }

    @Test
    fun unavailableOrCorruptWindowsCredentialsRequireReentry() {
        val path = temporary.root.toPath().resolve("token.dpapi")
        Files.write(path, byteArrayOf(1))
        assertNull(TokenStore(path, true, forbidden).read())
    }

    @Test
    fun windowsClearDeletesTheCredentialFile() {
        val path = temporary.root.toPath().resolve("token.dpapi")
        val store = TokenStore(path, true, reversible)
        store.save("secret")
        store.save(" ")
        assertFalse(Files.exists(path))
        assertNull(store.read())
    }

    @Test
    fun operatingSystemDetectionDoesNotMistakeDarwinForWindows() {
        assertTrue(isWindows("Windows 11"))
        assertFalse(isWindows("Darwin"))
        assertFalse(isWindows("Mac OS X"))
        assertFalse(isWindows("Linux"))
    }

    @Test
    fun preferencesPersistChoiceWithoutTheToken() {
        val directory = temporary.root.toPath()
        val device = DeviceSettings(directory)
        device.chosen = "knowledge"
        device.choice = local.assistant.core.ChatChoice("codex", "gpt-5.4")
        device.tokens.save("unique-private-token")
        val restored = DeviceSettings(directory)
        assertEquals("knowledge", restored.chosen)
        assertEquals("codex/gpt-5.4", restored.choice.stored)
        assertFalse(Files.readString(directory.resolve("device.properties")).contains("unique-private-token"))
    }
}
