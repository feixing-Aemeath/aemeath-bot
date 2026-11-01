package local.assistant.core

import kotlinx.serialization.Serializable
import kotlinx.serialization.SerialName
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

@Serializable
data class KnowledgeReader(val id: String, val kind: String, val sticker: String? = null)

@Serializable
data class KnowledgeArea(
    val path: String,
    val name: String,
    val kind: String = "topic",
    val readers: List<String> = emptyList(),
    val locked: List<String> = emptyList(),
    val notes: Int = 0,
    val sources: Int = 0,
    val files: Int = notes + sources,
    val pending: Int = 0,
    val failed: Int = 0,
    @SerialName("updated_at") val updatedAt: String? = null,
) {
    val isTopic get() = kind == "topic"
    val summary
        get() = if (!isTopic) {
            if (files == 0) "空" else "$files 个文件"
        } else if (notes == 0 && sources == 0) "空的，放点资料进来" else listOfNotNull(
            "$notes 篇整理".takeIf { notes > 0 },
            "$sources 份资料".takeIf { sources > 0 }
        ).joinToString(" · ")
}

@Serializable
data class KnowledgeOrganize(
    @SerialName("last_run_at") val lastRunAt: String? = null,
    val status: String? = null,
    @SerialName("next_run_at") val nextRunAt: String? = null
)

@Serializable
data class KnowledgeOverview(
    val readers: List<KnowledgeReader>,
    @SerialName("default_readers") val defaultReaders: List<String> = emptyList(),
    val areas: List<KnowledgeArea>,
    @SerialName("loose_files") val looseFiles: Int = 0,
    val organize: KnowledgeOrganize = KnowledgeOrganize(),
) {
    val topics
        get() = areas.filter { it.isTopic }
    val vaultFolders
        get() = areas.filterNot { it.isTopic }
    val pending
        get() = topics.sumOf { it.pending }

    fun ordered(names: List<String>) = readers.filter { it.id in names }
}

@Serializable
data class KnowledgeEntry(
    val path: String,
    val name: String,
    val title: String,
    val kind: String,
    val format: String,
    val size: Long = 0,
    @SerialName("modified_at") val modifiedAt: String? = null,
    val status: String? = null,
    val error: String? = null,
)

@Serializable
data class KnowledgeFiles(
    val path: String,
    val area: String? = null,
    val readers: List<String> = emptyList(),
    val entries: List<KnowledgeEntry>
)

@Serializable
data class KnowledgeSourceRef(val path: String, val label: String? = null)

@Serializable
data class KnowledgeFile(
    val path: String,
    val name: String,
    val title: String = name,
    val kind: String,
    val format: String = "other",
    val size: Long = 0,
    @SerialName("modified_at") val modifiedAt: String? = null,
    val area: String? = null,
    val readers: List<String> = emptyList(),
    val content: String = "",
    val extracted: Boolean = false,
    val truncated: Boolean = false,
    val sources: List<KnowledgeSourceRef> = emptyList(),
    val notes: List<String> = emptyList(),
    @SerialName("organized_by") val organizedBy: String? = null,
    @SerialName("organized_at") val organizedAt: String? = null,
    val status: String? = null,
    val error: String? = null,
) {
    val displayText get() = Knowledge.readable(if (kind == "note") Knowledge.dropSection("来源", content) else content)
    val hasOriginal get() = format in listOf("pdf", "image", "docx")
}

@Serializable
data class KnowledgeSearchResult(
    val path: String,
    val name: String,
    val title: String,
    val kind: String,
    val area: String? = null,
    val excerpt: String
)

@Serializable
data class KnowledgeSearchPage(val results: List<KnowledgeSearchResult>)

object Knowledge {
    fun validTopic(name: String): Boolean {
        val n = name.trim()
        return n.codePointCount(0, n.length) in 1..40 && !n.startsWith('.') && n != "整理" && n.none { it in "/\\:" }
    }

    fun fileName(path: String) = path.substringAfterLast('/')

    val uploadTypes = mapOf(
        "md" to "text/markdown",
        "txt" to "text/plain",
        "pdf" to "application/pdf",
        "png" to "image/png",
        "jpg" to "image/jpeg",
        "jpeg" to "image/jpeg",
        "heic" to "image/heic",
        "docx" to "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    fun contentType(name: String) = uploadTypes[name.substringAfterLast('.', "").lowercase()]

    fun statusText(status: String?) = mapOf(
        "pending" to "待整理",
        "organized" to "已整理",
        "failed" to "整理失败，下次再试",
        "skipped" to "不整理：这个主题谁都不能读",
        "unreadable" to "读不出文字"
    )[status]

    fun dropSection(title: String, text: String): String {
        var dropping = false
        return text.lines()
            .filter { line ->
                val m = Regex("^ {0,3}(#{1,6}) +(.+?) *#* *$").matchEntire(line)
                if (m != null && m.groupValues[1].length <= 2) dropping =
                    m.groupValues[1].length == 2 && m.groupValues[2] == title
                !dropping
            }
            .joinToString("\n")
            .trim()
    }

    fun readable(text: String) = Regex("!?\\[\\[([^\\]|]+)(?:\\|([^\\]]+))?\\]\\]")
        .replace(text) { it.groups[2]?.value ?: fileName(it.groupValues[1].trim()) }
}

// Serialize whole-selection writes. A slow earlier response must never overwrite a later tap.
class ReaderSelection(initial: List<String>) {
    var chosen = initial
        private set

    private var saved = initial
    private var revision = 0
    private val mutex = Mutex()

    suspend fun set(name: String, on: Boolean, save: suspend (List<String>) -> List<String>) {
        chosen = chosen.filterNot { it == name } + if (on) listOf(name) else emptyList()
        val names = chosen.toList()
        val current =++revision
        mutex.withLock {
            try {
                saved = save(names)
                if (current == revision) chosen = saved
            } catch (e: Exception) {
                if (current == revision) chosen = saved
                throw e
            }
        }
    }
}
