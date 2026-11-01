package local.assistant.desktop

import local.assistant.core.StickerCatalog
import java.awt.image.BufferedImage
import javax.imageio.ImageIO

object Resources {
    fun bytes(path: String): ByteArray? = javaClass.getResourceAsStream("/$path")?.use { it.readBytes() }

    val catalog: StickerCatalog by lazy {
        val paths = bytes("stickers.index")?.decodeToString()?.lines()?.filter { it.isNotBlank() }?.toSet().orEmpty()
        StickerCatalog.fromPaths(paths) { bytes("stickers/$it")?.decodeToString() }
    }

    fun image(path: String): BufferedImage? = javaClass.getResourceAsStream("/stickers/$path")?.use { ImageIO.read(it) }
}
