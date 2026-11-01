package local.assistant.desktop

import local.assistant.core.MascotState
import java.awt.Color
import java.awt.EventQueue
import java.awt.MenuItem
import java.awt.MouseInfo
import java.awt.Point
import java.awt.PopupMenu
import java.awt.RenderingHints
import java.awt.SystemTray
import java.awt.TrayIcon
import java.awt.event.MouseAdapter
import java.awt.event.MouseEvent
import java.awt.image.BufferedImage

class DesktopTray(
    private val openPopup: (Point) -> Unit,
    private val openMain: () -> Unit,
    private val quit: () -> Unit,
    private val availability: (Boolean) -> Unit,
) : AutoCloseable {
    private var icon: TrayIcon? = null
    private val original = Resources.image("aemeath.png")
    private var lastState: MascotState? = null

    init {
        if (SystemTray.isSupported() && original != null) {
            val menu = PopupMenu().apply {
                add(MenuItem("打开主窗口").apply { addActionListener { openMain() } })
                add(MenuItem("退出").apply { addActionListener { quit() } })
            }
            icon = TrayIcon(still(MascotState.IDLE), "Aemeath Bot", menu).apply {
                isImageAutoSize = true
                addMouseListener(object : MouseAdapter() {
                    override fun mouseClicked(event: MouseEvent) {
                        if (event.button == MouseEvent.BUTTON1) {
                            openPopup(MouseInfo.getPointerInfo()?.location ?: Point(event.x, event.y))
                        }
                    }
                })
            }
            EventQueue.invokeLater {
                availability(runCatching { SystemTray.getSystemTray().add(icon) }.isSuccess)
            }
        }
    }

    private fun still(state: MascotState): BufferedImage {
        val output = BufferedImage(32, 32, BufferedImage.TYPE_INT_ARGB)
        val image = original ?: return output
        var left = image.width
        var top = image.height
        var right = 0
        var bottom = 0
        for (y in 0 until image.height) {
            for (x in 0 until image.width) {
                if ((image.getRGB(x, y) ushr 24) > 8) {
                    left = minOf(left, x)
                    top = minOf(top, y)
                    right = maxOf(right, x + 1)
                    bottom = maxOf(bottom, y + 1)
                }
            }
        }
        if (right <= left || bottom <= top) return output
        val graphics = output.createGraphics()
        graphics.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BICUBIC)
        val scale = minOf(28.0 / (right - left), 28.0 / (bottom - top))
        val width = ((right - left) * scale).toInt()
        val height = ((bottom - top) * scale).toInt()
        graphics.rotate(Math.toRadians(if (state == MascotState.ERROR) -8.0 else if (state == MascotState.OFFLINE) 4.0 else 0.0), 16.0, 16.0)
        if (state == MascotState.OFFLINE) graphics.composite = java.awt.AlphaComposite.getInstance(java.awt.AlphaComposite.SRC_OVER, 0.45f)
        graphics.drawImage(image, (32 - width) / 2, (32 - height) / 2, (32 + width) / 2, (32 + height) / 2, left, top, right, bottom, null)
        graphics.composite = java.awt.AlphaComposite.SrcOver
        graphics.color = when (state) {
            MascotState.WORKING -> Color(217, 154, 67)
            MascotState.ERROR -> Color(217, 102, 89)
            MascotState.OFFLINE -> Color(152, 150, 143)
            else -> Color(114, 150, 122)
        }
        graphics.fillOval(24, 24, 8, 8)
        graphics.dispose()
        return output
    }

    fun update(text: TrayText, state: MascotState) {
        EventQueue.invokeLater {
            icon?.toolTip = text.tooltip
            if (state != lastState) {
                icon?.image = still(state)
                lastState = state
            }
        }
    }

    override fun close() {
        EventQueue.invokeLater { icon?.let { SystemTray.getSystemTray().remove(it) } }
    }
}
