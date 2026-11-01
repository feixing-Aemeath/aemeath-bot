package local.assistant.android.health

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.runBlocking
import local.assistant.android.data.DeviceSettings
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28], qualifiers = "w1280dp-h800dp-land-mdpi")
class TabletHealthTest {
    private val context get() = ApplicationProvider.getApplicationContext<Context>()

    @Before
    fun reset() {
        context.getSharedPreferences("device", 0).edit().clear().commit()
    }

    @Test
    fun tabletNeverRequestsHealthPermissions() {
        assertTrue(DeviceSettings(context).tablet)
        assertTrue(HealthSync(context).permissions().isEmpty())
    }

    @Test
    fun tabletNeverReadsOrUploadsEvenWithOldPhonePreferences() = runBlocking {
        val settings = DeviceSettings(context)
        settings.mock = false
        settings.healthEnabled = true
        assertTrue(HealthSync(context).sync(force = true, background = false))
        assertTrue(HealthSync(context).sync(force = true, background = true))
        assertEquals(0L, settings.healthLast)
        assertTrue(HealthSync(context).granted().isEmpty())
    }

    @Test
    fun tabletSelectionDoesNotOverwritePhoneSelection() {
        val prefs = context.getSharedPreferences("device", 0)
        prefs.edit().putString("chosen", "health").commit()
        val settings = DeviceSettings(context)
        settings.chosen = "knowledge"
        assertEquals("health", prefs.getString("chosen", null))
        assertEquals("knowledge", DeviceSettings(context).chosen)
    }

    @Test
    fun destinationResetClearsAllHealthCursors() {
        val settings = DeviceSettings(context)
        settings.saveHealthCursor("heart", "a")
        settings.saveHealthCursor("sleep", "b")
        settings.healthLast = 100
        settings.resetHealthCursors()
        assertNull(settings.healthCursor("heart"))
        assertNull(settings.healthCursor("sleep"))
        assertEquals(0L, settings.healthLast)
    }
}
