import Foundation

/// The invented knowledge base behind `MockBackend`: two topics with notes and sources, one
/// nobody may read, and the vault's own folders. It answers like the real API: an unknown
/// reader or a taken name fails, an upload never replaces a file.
struct MockKnowledge {
    static let bots = [
        ("助理", "aemeath"), ("成绩", "red_ribbon"), ("校园", "luosela"), ("作业", "moning"),
        ("用量", "shouanren"), ("健康", "lily_veil"), ("知识库", "orange_daisy"),
    ]
    static let vaultFolders = [("Bots", "Bots"), ("每日", "每日"), (".", "根目录文件")]

    var agents = ["Claude Code", "Codex"]
    var defaultReaders = ["助理", "知识库"]
    var topics: [String] = ["数据结构", "训练", "私人"]
    var access: [String: [String]] = [
        "知识库/数据结构": ["助理", "作业", "知识库", "Claude Code", "Codex"],
        "知识库/训练": ["助理", "健康"],
        "知识库/私人": [],
        "Bots": [], "每日": ["Claude Code"], ".": [],
    ]
    var files: [KnowledgeFile]
    var organize: KnowledgeOrganize

    init(now: Date) {
        func ago(_ hours: Double) -> Date { now.addingTimeInterval(-hours * 3600) }
        func note(_ path: String, _ title: String, _ content: String, _ sources: [KnowledgeSourceRef], _ hours: Double)
            -> KnowledgeFile
        {
            KnowledgeFile(
                path: path, name: Knowledge.fileName(path), title: title, kind: "note", format: "md",
                size: content.utf8.count, modifiedAt: ago(hours), area: nil, readers: [], content: content,
                sources: sources, organizedBy: "codex", organizedAt: ago(hours))
        }
        func source(
            _ path: String, _ format: String, _ content: String, _ status: String, _ hours: Double,
            notes: [String] = [], size: Int? = nil
        ) -> KnowledgeFile {
            let name = Knowledge.fileName(path)
            return KnowledgeFile(
                path: path, name: name, title: (name as NSString).deletingPathExtension, kind: "source",
                format: format, size: size ?? content.utf8.count, modifiedAt: ago(hours), area: nil, readers: [],
                content: content, extracted: format == "pdf" || format == "image", notes: notes, status: status)
        }
        func file(_ path: String, _ content: String, _ hours: Double) -> KnowledgeFile {
            let name = Knowledge.fileName(path)
            return KnowledgeFile(
                path: path, name: name, title: (name as NSString).deletingPathExtension, kind: "file", format: "md",
                size: content.utf8.count, modifiedAt: ago(hours), area: nil, readers: [], content: content)
        }
        let chapter = "知识库/数据结构/第5章 图.pdf"
        files = [
            note(
                "知识库/数据结构/整理/最短路径.md", "最短路径：Dijkstra",
                """
                每次从还没确定的顶点里取出距离最小的一个，把它定下来，再用它去更新相邻顶点的距离。

                ## 要点

                - 只适用于没有负权边的图；有负权边时用 Bellman-Ford。
                - 用二叉堆存待选顶点，时间复杂度是 **O((V + E) log V)**。
                - 和 [[图的遍历|广度优先遍历]] 的区别：这里按距离出队，而不是按层。

                ## 来源

                - [[\(chapter)]] 第 18 页

                ## 我的批注

                （模拟数据）考前再做一遍课本上的例题。
                """,
                [KnowledgeSourceRef(path: chapter, label: "第 18 页")], 7),
            note(
                "知识库/数据结构/整理/图的遍历.md", "图的遍历",
                """
                深度优先沿一条路走到底再回头，用栈或递归；广度优先一层一层向外，用队列。

                ## 来源

                - [[\(chapter)]] 第 6–9 页

                ## 我的批注
                """,
                [KnowledgeSourceRef(path: chapter, label: "第 6–9 页")], 7),
            source(
                chapter, "pdf", "[第 1 页]\n第 5 章 图\n\n[第 6 页]\n5.3 图的遍历……\n\n[第 18 页]\n5.6 最短路径……",
                "organized", 30,
                notes: ["知识库/数据结构/整理/最短路径.md", "知识库/数据结构/整理/图的遍历.md"], size: 2_400_000),
            source("知识库/数据结构/课堂笔记 10-27.md", "md", "# 课堂笔记\n\n（模拟数据）拓扑排序，下周小测。", "pending", 2),
            source("知识库/数据结构/板书.heic", "image", "", "unreadable", 26, size: 1_800_000),
            note(
                "知识库/训练/整理/有氧和恢复.md", "有氧和恢复",
                "（模拟数据）每周三次轻松跑，心率保持在能说话的强度；前一晚睡得不好时把强度降一档。\n\n## 来源\n\n- [[知识库/训练/跑步计划.md]]\n\n## 我的批注",
                [KnowledgeSourceRef(path: "知识库/训练/跑步计划.md")], 31),
            source("知识库/训练/跑步计划.md", "md", "# 跑步计划\n\n（模拟数据）周一、三、六各跑 5 公里。", "organized", 50),
            source("知识库/私人/想法.md", "md", "（模拟数据）只给自己看的笔记。", "skipped", 70),
            file("Bots/助理/记忆.md", "# 助理的记忆\n\n（模拟数据）用户喜欢先看今天的课。", 20),
            file("每日/今天.md", "# 今天\n\n（模拟数据）", 1),
            file("关于我.md", "# 关于我\n\n（模拟数据）", 100),
        ]
        organize = KnowledgeOrganize(lastRunAt: ago(7), status: "succeeded", nextRunAt: ago(-17))
    }

    var readers: [KnowledgeReader] {
        Self.bots.map { KnowledgeReader(id: $0.0, kind: "bot", sticker: $0.1) }
            + agents.map { KnowledgeReader(id: $0, kind: "agent") }
    }

    /// The area a vault path belongs to: its topic in 知识库, else its first folder, else ".".
    static func area(of path: String) -> String? {
        let parts = path.split(separator: "/").map(String.init)
        if parts.first == "知识库" { return parts.count > 2 ? "知识库/\(parts[1])" : nil }
        if parts.count > 1, vaultFolders.contains(where: { $0.0 == parts[0] }) { return parts[0] }
        return "."
    }

    func area(_ path: String) -> KnowledgeArea? {
        let botNames = Self.bots.map(\.0)
        let inside = files.filter { Self.area(of: $0.path) == path }
        let readers = access[path] ?? []
        if path.hasPrefix("知识库/") {
            let name = String(path.dropFirst("知识库/".count))
            guard topics.contains(name) else { return nil }
            let sources = inside.filter { $0.kind == "source" }
            return KnowledgeArea(
                path: path, name: name, kind: "topic", readers: order(readers),
                notes: inside.count - sources.count, sources: sources.count,
                pending: sources.filter { $0.status == "pending" }.count,
                failed: sources.filter { $0.status == "failed" || $0.status == "unreadable" }.count,
                updatedAt: inside.compactMap(\.modifiedAt).max())
        }
        guard let folder = Self.vaultFolders.first(where: { $0.0 == path }) else { return nil }
        return KnowledgeArea(
            path: path, name: folder.1, kind: "system", readers: order(botNames + readers), locked: botNames,
            files: inside.count, updatedAt: inside.compactMap(\.modifiedAt).max())
    }

    func order(_ names: [String]) -> [String] {
        readers.map(\.id).filter { names.contains($0) }
    }

    var overview: KnowledgeOverview {
        let areas = topics.sorted().compactMap { area("知识库/\($0)") } + Self.vaultFolders.compactMap { area($0.0) }
        return KnowledgeOverview(
            readers: readers, defaultReaders: defaultReaders, areas: areas,
            looseFiles: files.filter { $0.path.hasPrefix("知识库/") && Self.area(of: $0.path) == nil }.count,
            organize: organize)
    }

    func known(_ names: [String]) throws {
        let all = Set(readers.map(\.id))
        guard names.allSatisfy(all.contains) else { throw APIError.invalidRequest }
    }

    mutating func update(_ patch: KnowledgePatch) throws {
        if let agents = patch.agents {
            let bots = Set(Self.bots.map(\.0))
            guard agents.allSatisfy({ (1...40).contains($0.count) && !bots.contains($0) }) else {
                throw APIError.invalidRequest
            }
            let gone = Set(self.agents).subtracting(agents)
            self.agents = agents
            access = access.mapValues { $0.filter { !gone.contains($0) } }
            defaultReaders.removeAll { gone.contains($0) }
        }
        if let readers = patch.defaultReaders {
            try known(readers)
            defaultReaders = order(readers)
        }
    }

    mutating func setReaders(_ names: [String], area path: String) throws -> KnowledgeArea {
        guard area(path) != nil else { throw APIError.notFound }
        try known(names)
        let locked = Set(area(path)?.locked ?? [])
        access[path] = order(names.filter { !locked.contains($0) })
        return area(path)!
    }

    mutating func createTopic(name: String, readers: [String]?) throws -> KnowledgeArea {
        let name = name.trimmingCharacters(in: .whitespaces)
        guard Knowledge.validTopic(name) else { throw APIError.invalidRequest }
        guard !topics.contains(name) else { throw APIError.conflict }
        try known(readers ?? [])
        topics.append(name)
        access["知识库/\(name)"] = order(readers ?? defaultReaders)
        return area("知识库/\(name)")!
    }

    func list(_ path: String) throws -> KnowledgeFiles {
        let area = path == "知识库" ? nil : self.area(path)
        guard path == "知识库" || area != nil else { throw APIError.notFound }
        let entries = files
            .filter { path == "知识库" ? $0.path.hasPrefix("知识库/") && Self.area(of: $0.path) == nil : Self.area(of: $0.path) == path }
            .sorted { ($0.modifiedAt ?? .distantPast) > ($1.modifiedAt ?? .distantPast) }
            .map {
                KnowledgeEntry(
                    path: $0.path, name: $0.name, title: $0.title, kind: $0.kind, format: $0.format, size: $0.size,
                    modifiedAt: $0.modifiedAt, status: $0.status, error: $0.error)
            }
        return KnowledgeFiles(path: path, area: area?.path, readers: area?.readers ?? [], entries: entries)
    }

    func file(_ path: String) throws -> KnowledgeFile {
        guard var file = files.first(where: { $0.path == path }) else { throw APIError.notFound }
        let area = Self.area(of: path).flatMap { self.area($0) }
        file.area = area?.path
        file.readers = area?.readers ?? []
        if file.status == "unreadable" { file.error = "这张图片里没有识别出文字" }
        return file
    }

    func search(_ query: String) throws -> [KnowledgeSearchResult] {
        let query = query.trimmingCharacters(in: .whitespaces)
        guard (1...500).contains(query.count) else { throw APIError.invalidRequest }
        return files.compactMap { file in
            guard let range = file.content.range(of: query) ?? file.title.range(of: query) else { return nil }
            let text = file.content.range(of: query) == nil ? file.title : file.content
            let start = text.index(range.lowerBound, offsetBy: -20, limitedBy: text.startIndex) ?? text.startIndex
            let end = text.index(range.upperBound, offsetBy: 40, limitedBy: text.endIndex) ?? text.endIndex
            return KnowledgeSearchResult(
                path: file.path, name: file.name, title: file.title, kind: file.kind,
                area: Self.area(of: file.path), excerpt: String(text[start..<end]).replacingOccurrences(of: "\n", with: " "))
        }
    }

    mutating func upload(path: String, size: Int, now: Date) throws -> KnowledgeEntry {
        let parts = path.split(separator: "/").map(String.init)
        guard parts.count >= 3, parts[0] == "知识库", topics.contains(parts[1]), !parts.contains("整理"),
            let type = Knowledge.contentType(for: path)
        else { throw APIError.invalidRequest }
        guard !files.contains(where: { $0.path == path }) else { throw APIError.conflict }
        let format = type.hasPrefix("image/") ? "image" : (path as NSString).pathExtension.lowercased()
        let name = Knowledge.fileName(path)
        files.append(
            KnowledgeFile(
                path: path, name: name, title: (name as NSString).deletingPathExtension, kind: "source",
                format: format, size: size, modifiedAt: now, area: nil, readers: [], content: "", status: "pending"))
        return try list("知识库/\(parts[1])").entries.first { $0.path == path }!
    }
}
