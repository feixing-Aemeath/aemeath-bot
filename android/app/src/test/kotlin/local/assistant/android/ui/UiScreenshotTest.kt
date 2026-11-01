package local.assistant.android.ui

import android.app.Application
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.core.app.ApplicationProvider
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.runBlocking
import local.assistant.android.data.AppModel
import local.assistant.core.MockBackend
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File
import java.time.Instant
import java.time.ZoneId
import java.util.TimeZone

@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "zh-rCN-w412dp-h915dp-port-mdpi")
class UiScreenshotTest {
    @get:Rule
    val compose = createComposeRule()

    private var model: AppModel? = null
    private val previousZone = TimeZone.getDefault()

    @After
    fun tearDown() {
        model?.stop()
        TimeZone.setDefault(previousZone)
    }

    private fun capture(name: String, dark: Boolean, bot: String = "assistant", route: Route? = null, drawer: Boolean = false) {
        TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai"))
        val now = Instant.parse("2026-10-31T10:30:00Z")
        val app = ApplicationProvider.getApplicationContext<Application>()
        app.getSharedPreferences("device", 0).edit().clear().commit()
        val mock = MockBackend({ now }, ZoneId.of("Asia/Shanghai"))
        val current = AppModel(app, mock) { now }
        model = current
        current.device.chosen = bot
        runBlocking { current.refresh() }
        compose.setContent {
            val state by current.state.collectAsState()
            AemeathTheme(dark = dark) {
                CompositionLocalProvider(LocalAnimationTime provides now) {
                    AppContent(current, state, route)
                }
            }
        }
        compose.waitForIdle()
        compose.waitUntil(15_000) {
            compose.onAllNodesWithTag("artwork-loading", useUnmergedTree = true).fetchSemanticsNodes().isEmpty()
        }
        if (drawer) {
            compose.onNodeWithContentDescription("所有 Bot").performClick()
            compose.mainClock.advanceTimeBy(400)
        }
        compose.waitForIdle()
        if (bot == "health") compose.onNodeWithText("今天的记录").assertExists()
        val output = File("build/outputs/roborazzi").apply { mkdirs() }
        compose.onRoot().captureRoboImage(File(output, "$name-${if (dark) "dark" else "light"}.png").path)
    }

    @Test
    fun phoneAssistantLight() = capture("phone-assistant", false)

    @Test
    fun phoneAssistantDark() = capture("phone-assistant", true)

    @Test
    fun phoneLilyLight() = capture("phone-lily", false, "health")

    @Test
    fun phoneLilyDark() = capture("phone-lily", true, "health")

    @Test
    fun phoneLibrarianLight() = capture("phone-librarian", false, "knowledge")

    @Test
    fun phoneLibrarianDark() = capture("phone-librarian", true, "knowledge")

    @Test
    fun phoneDrawerLight() = capture("phone-drawer", false, drawer = true)

    @Test
    fun phoneDrawerDark() = capture("phone-drawer", true, drawer = true)

    @Test
    fun phoneSettingsLight() = capture("phone-settings", false, route = Route("settings"))

    @Test
    fun phoneSettingsDark() = capture("phone-settings", true, route = Route("settings"))

    @Test
    fun phoneKnowledgeLight() = capture("phone-knowledge", false, route = Route("knowledge"))

    @Test
    fun phoneKnowledgeDark() = capture("phone-knowledge", true, route = Route("knowledge"))

    @Test
    @Config(qualifiers = "zh-rCN-sw800dp-w1280dp-h800dp-land-mdpi")
    fun tabletLandscapeLight() = capture("tablet-landscape", false)

    @Test
    @Config(qualifiers = "zh-rCN-sw800dp-w1280dp-h800dp-land-mdpi")
    fun tabletLandscapeDark() = capture("tablet-landscape", true)

    @Test
    @Config(qualifiers = "zh-rCN-sw800dp-w1280dp-h800dp-land-mdpi")
    fun tabletLilyLight() = capture("tablet-lily", false, "health")

    @Test
    @Config(qualifiers = "zh-rCN-sw800dp-w1280dp-h800dp-land-mdpi")
    fun tabletLilyDark() = capture("tablet-lily", true, "health")
}
