package local.assistant.android.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import local.assistant.core.*

/** AES-GCM key stays in Android Keystore; only the encrypted token goes in preferences. */
class DeviceSettings(context: Context) {
    private val prefs = context.getSharedPreferences("device", Context.MODE_PRIVATE)
    val tablet = DeviceProfile.isTablet(context)
    var baseUrl: String
        get() = prefs.getString("url", "http://127.0.0.1:8765")!!
        set(value) {
            prefs.edit().putString("url", value).apply()
        }

    var mock: Boolean
        get() = prefs.getBoolean("mock", true)
        set(value) {
            prefs.edit().putBoolean("mock", value).apply()
        }

    var chosen: String
        get() = prefs.getString(PadPresentation.storageKey(tablet), "assistant")!!
        set(value) {
            prefs.edit().putString(PadPresentation.storageKey(tablet), value).apply()
        }

    fun healthCursor(type: String): String? = prefs.getString("health.cursor.$type", null)

    fun saveHealthCursor(type: String, token: String) {
        check(prefs.edit().putString("health.cursor.$type", token).commit())
    }

    fun resetHealthCursors() {
        val edit = prefs.edit()
        prefs.all.keys.filter { it.startsWith("health.cursor.") }.forEach { edit.remove(it) }
        check(edit.putLong("health_last", 0).commit())
    }

    var choice: ChatChoice
        get() = ChatChoice.restore(prefs.getString("choice", null))
        set(value) {
            prefs.edit().putString("choice", value.stored).apply()
        }

    var style: String
        get() = prefs.getString("style", "plain")!!
        set(value) {
            prefs.edit().putString("style", value).apply()
        }

    var healthEnabled: Boolean
        get() = prefs.getBoolean("health_enabled", false)
        set(value) {
            prefs.edit().putBoolean("health_enabled", value).apply()
        }

    var healthLast: Long
        get() = prefs.getLong("health_last", 0)
        set(value) {
            prefs.edit().putLong("health_last", value).apply()
        }

    var healthCount: Int
        get() = prefs.getInt("health_count", 0)
        set(value) {
            prefs.edit().putInt("health_count", value).apply()
        }

    var healthProblem: String?
        get() = prefs.getString("health_problem", null)
        set(value) {
            prefs.edit().putString("health_problem", value).apply()
        }

    val firstLaunch: Long
        get() {
            if (!prefs.contains("first_launch"))
                prefs.edit().putLong("first_launch", System.currentTimeMillis()).apply()
            return prefs.getLong("first_launch", 0)
        }

    fun seen(id: String) = prefs.getLong("seen.$id", firstLaunch)

    fun markSeen(id: String) {
        prefs.edit().putLong("seen.$id", System.currentTimeMillis()).apply()
    }

    val hasToken get() = prefs.contains("token.cipher")

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey("aemeath.device.token", null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
            .apply {
                init(
                    KeyGenParameterSpec.Builder(
                        "aemeath.device.token",
                        KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
                    )
                        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .build()
                )
            }
            .generateKey()
    }

    fun saveToken(value: String) {
        if (value.isBlank()) return
        val c = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val encrypted = c.doFinal(value.trim().toByteArray())
        check(
            prefs.edit()
                .putString("token.cipher", Base64.encodeToString(encrypted, Base64.NO_WRAP))
                .putString("token.iv", Base64.encodeToString(c.iv, Base64.NO_WRAP))
                .commit()
        )
    }

    fun token(): String? {
        val encrypted = prefs.getString("token.cipher", null) ?: return null
        val iv = prefs.getString("token.iv", null) ?: return null
        return try {
            Cipher.getInstance("AES/GCM/NoPadding")
                .apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP))) }
                .doFinal(Base64.decode(encrypted, Base64.NO_WRAP))
                .decodeToString()
        } catch (_: Exception) {
            null
        }
    }

    fun backend(): Backend {
        if (mock) return MockBackend()
        val credential = token()
        return LiveBackend(baseUrl, { credential })
    }
}
