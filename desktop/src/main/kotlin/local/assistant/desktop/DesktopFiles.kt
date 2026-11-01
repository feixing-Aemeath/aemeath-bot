package local.assistant.desktop

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.swing.Swing
import kotlinx.coroutines.withContext
import local.assistant.core.APIError
import local.assistant.core.Backend
import local.assistant.core.Knowledge
import local.assistant.core.KnowledgeArea
import local.assistant.core.KnowledgeFile
import java.awt.Desktop
import java.io.File
import java.nio.file.Files
import javax.swing.JFileChooser
import javax.swing.JOptionPane

suspend fun chooseFile(saveAs: String? = null): File? = withContext(Dispatchers.Swing) {
    val chooser = JFileChooser().apply {
        dialogTitle = if (saveAs == null) "从文件添加" else "保存原件"
        if (saveAs != null) selectedFile = File(saveAs)
    }
    val result = if (saveAs == null) chooser.showOpenDialog(null) else chooser.showSaveDialog(null)
    if (result != JFileChooser.APPROVE_OPTION) return@withContext null
    val selected = chooser.selectedFile
    if (saveAs != null && selected.exists()) {
        val confirmed = JOptionPane.showConfirmDialog(null, "替换已有的「${selected.name}」？", "保存原件", JOptionPane.YES_NO_OPTION)
        if (confirmed != JOptionPane.YES_OPTION) return@withContext null
    }
    selected
}

suspend fun uploadFile(backend: Backend, area: KnowledgeArea, file: File) = withContext(Dispatchers.IO) {
    if (file.length() > 50 * 1024 * 1024 || !file.isFile) throw APIError("invalid_request")
    val type = Knowledge.contentType(file.name) ?: if (area.readers.isEmpty()) {
        when (file.extension.lowercase()) {
            "csv" -> "text/csv"
            "xlsx" -> "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            "zip" -> "application/zip"
            else -> null
        }
    } else null
    if (type == null) throw APIError("invalid_request")
    val bytes = file.inputStream().use { it.readNBytes(50 * 1024 * 1024 + 1) }
    if (bytes.size > 50 * 1024 * 1024) throw APIError("invalid_request")
    backend.uploadKnowledge("${area.path}/${file.name}", bytes, type)
}

suspend fun openOriginal(backend: Backend, file: KnowledgeFile) {
    val destination = chooseFile(file.name) ?: return
    withContext(Dispatchers.IO) {
        val temporary = Files.createTempFile(destination.toPath().toAbsolutePath().parent, ".aemeath-", ".download")
        try {
            Files.newOutputStream(temporary).use { backend.copyOriginal(file.path, it) }
            Files.move(temporary, destination.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)
        } finally {
            Files.deleteIfExists(temporary)
        }
        if (Desktop.isDesktopSupported() && Desktop.getDesktop().isSupported(Desktop.Action.OPEN)) {
            Desktop.getDesktop().open(destination)
        }
    }
}
