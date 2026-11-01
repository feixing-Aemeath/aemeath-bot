import java.awt.RenderingHints
import java.awt.image.BufferedImage
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import javax.imageio.ImageIO
import org.jetbrains.compose.desktop.application.dsl.TargetFormat

plugins {
    kotlin("jvm") version "2.2.20"
    kotlin("plugin.serialization") version "2.2.20"
    id("org.jetbrains.kotlin.plugin.compose") version "2.2.20"
    id("org.jetbrains.compose") version "1.9.1"
}

version = "1.0.0"
kotlin {
    jvmToolchain(17)
}

dependencies {
    implementation(project(":core"))
    implementation(compose.desktop.currentOs)
    implementation(compose.material)
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-swing:1.10.2")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.8.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("net.java.dev.jna:jna-platform:5.17.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
}

val stickerRoot = file("../frontend/Design/stickers")
val appIcon = file("../frontend/App/AppIcon.icon/Assets/aemeath.png")
val generatedAssets = layout.buildDirectory.dir("generated/desktop")
val generateDesktopAssets by tasks.registering {
    inputs.dir(stickerRoot)
    inputs.file(appIcon)
    outputs.dir(generatedAssets)
    doLast {
        val output = generatedAssets.get().asFile
        output.mkdirs()
        output.resolve("stickers.index").writeText(
            stickerRoot.walkTopDown().filter { it.isFile }
                .map { it.relativeTo(stickerRoot).invariantSeparatorsPath }
                .sorted().joinToString("\n"),
        )
        val original = checkNotNull(ImageIO.read(appIcon))
        val sizes = listOf(16, 32, 48, 64, 128, 256)
        val images = sizes.map { size ->
            val image = BufferedImage(size, size, BufferedImage.TYPE_INT_ARGB)
            val graphics = image.createGraphics()
            graphics.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BICUBIC)
            graphics.drawImage(original, 0, 0, size, size, null)
            graphics.dispose()
            ByteArrayOutputStream().also { ImageIO.write(image, "png", it) }.toByteArray()
        }
        val header = ByteBuffer.allocate(6 + 16 * images.size).order(ByteOrder.LITTLE_ENDIAN)
        header.putShort(0).putShort(1).putShort(images.size.toShort())
        var offset = header.capacity()
        images.forEachIndexed { index, bytes ->
            val size = sizes[index]
            header.put((size % 256).toByte()).put((size % 256).toByte())
                .put(0.toByte()).put(0.toByte()).putShort(1).putShort(32)
                .putInt(bytes.size).putInt(offset)
            offset += bytes.size
        }
        output.resolve("aemeath.ico").outputStream().use { stream ->
            stream.write(header.array())
            images.forEach { stream.write(it) }
        }
    }
}

tasks.processResources {
    dependsOn(generateDesktopAssets)
    from(stickerRoot) { into("stickers") }
    from(generatedAssets) { include("stickers.index") }
}

tasks.test {
    useJUnit()
    systemProperty("java.awt.headless", "true")
}

compose.desktop {
    application {
        mainClass = "local.assistant.desktop.MainKt"
        nativeDistributions {
            targetFormats(TargetFormat.Msi)
            packageName = "Aemeath Bot"
            packageVersion = "1.0.0"
            // ASCII only: WiX builds the MSI in code page 1252, where Chinese fails the package.
            description = "Aemeath Bot desktop assistant"
            vendor = "Aemeath"
            // Includes desktop, preferences, SSL and JNA's dependencies in the bundled runtime.
            includeAllModules = true
            windows {
                iconFile.set(generateDesktopAssets.map { generatedAssets.get().file("aemeath.ico") })
                upgradeUuid = "8aef6340-3d05-45a7-94a7-caa29d50d9ba"
                menuGroup = "Aemeath Bot"
                shortcut = true
                menu = true
                perUserInstall = true
                dirChooser = true
            }
        }
    }
}

tasks.matching { it.name.startsWith("package") || it.name.startsWith("createDistributable") }
    .configureEach { dependsOn(generateDesktopAssets) }
