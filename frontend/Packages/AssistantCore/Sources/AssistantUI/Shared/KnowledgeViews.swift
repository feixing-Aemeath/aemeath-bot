#if os(iOS) || os(macOS)
    import AssistantCore
    import QuickLook
    import SwiftUI
    import UniformTypeIdentifiers
    #if os(iOS)
        import PhotosUI
    #endif

    /// 知识库 (docs/knowledge.md): the topics with who may read each, the nightly tidy-up, the
    /// vault's own folders, and a search over everything. Opened from 知识库Bot's page: on the
    /// iPhone in a sheet, on the Mac pushed onto it.
    struct KnowledgeHome: View {
        @EnvironmentObject private var store: AppStore
        @State private var overview: KnowledgeOverview?
        @State private var loading = true
        @State private var query = ""
        @State private var results: [KnowledgeSearchResult]?
        @State private var naming = false
        @State private var newName = ""
        @State private var organizing = false

        var body: some View {
            Form {
                if !query.trimmingCharacters(in: .whitespaces).isEmpty {
                    searchResults
                } else if let overview {
                    sections(overview)
                } else if loading {
                    ProgressView().frame(maxWidth: .infinity)
                } else {
                    Text("没有读到知识库。Mac 上的后端更新以后才有这一页。").foregroundStyle(.secondary)
                }
            }
            .settingsPage("知识库")
            .searchable(text: $query, prompt: "搜索资料和整理笔记")
            .task { await load() }
            .refreshable { await load() }
            .task(id: query) { await search() }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        newName = ""
                        naming = true
                    } label: {
                        Label("新建主题", systemImage: "folder.badge.plus")
                    }
                    .disabled(overview == nil)
                }
            }
            .alert("新建主题", isPresented: $naming) {
                TextField("比如：数据结构", text: $newName)
                Button("新建") { Task { await create() } }
                Button("取消", role: .cancel) {}
            } message: {
                Text("在 Mac 的知识库里建一个文件夹，先按「新主题默认谁能读」设置，之后可以改。")
            }
        }

        @ViewBuilder func sections(_ overview: KnowledgeOverview) -> some View {
            Section {
                if overview.topics.isEmpty {
                    Text("还没有主题。点右上角新建一个，或者在 Mac 的「知识库」文件夹里建子文件夹。")
                        .foregroundStyle(.secondary)
                }
                ForEach(overview.topics) { area in
                    NavigationLink {
                        KnowledgeAreaPage(area: area, overview: overview) { changed in replace(changed) }
                    } label: {
                        AreaRow(area: area, overview: overview)
                    }
                }
            } header: {
                Text("主题")
            } footer: {
                Text("每个主题单独设谁能读。谁都不能读的主题只有你能看，也不会发给任何模型。")
            }
            if overview.looseFiles > 0 {
                Section {
                    Label(
                        "知识库最外层有 \(overview.looseFiles) 个文件不在任何主题里，Bot 和 Agent 都读不到。在 Mac 上把它们放进一个主题文件夹。",
                        systemImage: "exclamationmark.triangle"
                    )
                    .foregroundStyle(.orange)
                }
            }
            Section {
                LabeledContent("上次") { Text(lastRun(overview.organize)) }
                if let next = overview.organize.nextRunAt {
                    LabeledContent("下次") { Text(next, format: .dateTime.month().day().hour().minute()) }
                }
                Button {
                    Task { await organize() }
                } label: {
                    Label(
                        overview.pending > 0 ? "现在整理 \(overview.pending) 份新资料" : "现在整理",
                        systemImage: "wand.and.stars")
                }
                .disabled(organizing)
            } header: {
                Text("整理")
            } footer: {
                Text("每晚把新放进来的资料整理成笔记，写在那个主题的「整理」文件夹里，只给能读这个主题的人看。笔记最后「我的批注」一节是你的，整理时不会改。")
            }
            Section {
                ForEach(overview.vaultFolders) { area in
                    NavigationLink {
                        KnowledgeAreaPage(area: area, overview: overview) { changed in replace(changed) }
                    } label: {
                        AreaRow(area: area, overview: overview)
                    }
                }
            } header: {
                Text("Bot 的文件")
            } footer: {
                Text("每个 Bot 都能读这些文件夹，它们的日常工作要用；这里只决定你自己运行的 Agent 能不能读。")
            }
            Section {
                NavigationLink {
                    ReaderPicker(
                        title: "新主题默认谁能读", readers: overview.readers, chosen: overview.defaultReaders,
                        locked: [], note: "只影响以后新建的主题，已有主题不变。"
                    ) { names in
                        let updated = try await store.backend.updateKnowledge(KnowledgePatch(defaultReaders: names))
                        self.overview = updated
                        return updated.defaultReaders
                    }
                } label: {
                    LabeledContent("新主题默认谁能读") {
                        Text(overview.defaultReaders.isEmpty ? "谁都不能" : overview.defaultReaders.joined(separator: "、"))
                            .lineLimit(1)
                    }
                }
            }
        }

        @ViewBuilder var searchResults: some View {
            Section("搜索结果") {
                if let results {
                    if results.isEmpty {
                        Text("没有搜到。").foregroundStyle(.secondary)
                    }
                    ForEach(results) { result in
                        NavigationLink {
                            KnowledgeFilePage(path: result.path)
                        } label: {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(result.title).lineLimit(1)
                                Text(result.excerpt)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                                if let area = result.area {
                                    Text(area == "." ? "根目录文件" : area)
                                        .font(.caption2)
                                        .foregroundStyle(.tertiary)
                                }
                            }
                        }
                    }
                } else {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
        }

        func lastRun(_ organize: KnowledgeOrganize) -> String {
            guard let last = organize.lastRunAt else { return "还没整理过" }
            let time = last.formatted(.dateTime.month().day().hour().minute())
            switch organize.status {
            case "failed": return "\(time) 失败"
            case "running": return "正在整理"
            default: return time
            }
        }

        func replace(_ area: KnowledgeArea) {
            guard let index = overview?.areas.firstIndex(where: { $0.path == area.path }) else { return }
            overview?.areas[index] = area
        }

        func load() async {
            do {
                overview = try await store.backend.knowledge()
            } catch {
                if overview == nil, case .notFound? = error as? APIError {
                    // An older backend has no knowledge base yet; the page says so.
                } else {
                    store.message = "没能读到知识库：\(AppStore.describe(error))"
                }
            }
            loading = false
        }

        func search() async {
            let text = query.trimmingCharacters(in: .whitespaces)
            results = nil
            guard !text.isEmpty else { return }
            // Typing goes on; only search once it pauses.
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled else { return }
            do {
                results = try await store.backend.searchKnowledge(text)
            } catch {
                guard !Task.isCancelled else { return }
                results = []
                store.message = "没能搜索：\(AppStore.describe(error))"
            }
        }

        func create() async {
            let name = newName.trimmingCharacters(in: .whitespaces)
            guard Knowledge.validTopic(name) else {
                store.message = "主题名要 1 到 40 个字，不能有 / \\ :，不能以 . 开头，也不能叫「整理」。"
                return
            }
            do {
                _ = try await store.backend.createTopic(name: name, readers: nil)
                await load()
            } catch APIError.conflict {
                store.message = "已经有叫「\(name)」的主题了。"
            } catch {
                store.message = "没能新建主题：\(AppStore.describe(error))"
            }
        }

        func organize() async {
            organizing = true
            await store.runNow(Knowledge.organizeJob)
            await load()
            organizing = false
        }
    }

    /// A topic or folder in the list: its name, what is in it, and who may read it.
    struct AreaRow: View {
        let area: KnowledgeArea
        let overview: KnowledgeOverview

        var body: some View {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: symbol)
                    .foregroundStyle(area.readers.isEmpty ? .secondary : .primary)
                    .frame(width: 22)
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 6) {
                        Text(area.name)
                        if area.pending > 0 {
                            Text("\(area.pending) 份待整理")
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(.orange)
                        }
                    }
                    Text(area.summary).font(.caption).foregroundStyle(.secondary)
                    ReaderChips(readers: shown, empty: area.isTopic ? "只有你能看" : "只有 Bot 能读")
                }
            }
            .padding(.vertical, 2)
        }

        var symbol: String {
            if !area.isTopic { return area.path == "." ? "doc.on.doc" : "folder" }
            return area.readers.isEmpty ? "lock" : "books.vertical"
        }

        /// A vault folder lists the Agents only; every Bot reads it anyway.
        var shown: [KnowledgeReader] {
            let readers = overview.ordered(area.readers)
            return area.isTopic ? readers : readers.filter { !area.locked.contains($0.id) }
        }
    }

    /// Who may read, as small tags: a Bot in its sticker's color, an Agent in gray.
    struct ReaderChips: View {
        @EnvironmentObject private var store: AppStore
        let readers: [KnowledgeReader]
        var empty = "谁都不能读"

        var body: some View {
            if readers.isEmpty {
                Text(empty).font(.caption2).foregroundStyle(.secondary)
            } else {
                Wrap(spacing: 4) {
                    ForEach(readers) { reader in
                        let color = color(reader)
                        Text(reader.id)
                            .font(.caption2.weight(.medium))
                            .padding(.horizontal, 7)
                            .padding(.vertical, 2)
                            .foregroundStyle(color)
                            .background(Capsule().fill(color.opacity(0.14)))
                    }
                }
            }
        }

        func color(_ reader: KnowledgeReader) -> Color {
            guard reader.isBot, let sticker = reader.sticker else { return .secondary }
            return store.bots.first { $0.look.sticker == sticker }?.accent ?? .secondary
        }
    }

    /// Lays its children out in rows, starting a new row when one is full.
    struct Wrap: Layout {
        var spacing: CGFloat = 4

        func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
            let rows = rows(width: proposal.width ?? .infinity, subviews)
            let width = rows.map(rowWidth).max() ?? 0
            let heights: [CGFloat] = rows.map(rowHeight)
            return CGSize(width: width, height: heights.reduce(0, +) + gaps(rows.count))
        }

        // Kept apart so Xcode 16's type checker gets through sizeThatFits.
        func rowWidth(_ row: [CGSize]) -> CGFloat {
            let widths: [CGFloat] = row.map(\.width)
            return widths.reduce(0, +) + gaps(row.count)
        }

        func rowHeight(_ row: [CGSize]) -> CGFloat {
            row.map(\.height).max() ?? 0
        }

        func gaps(_ count: Int) -> CGFloat {
            spacing * CGFloat(max(count - 1, 0))
        }

        func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
            var index = 0
            var y = bounds.minY
            for row in rows(width: bounds.width, subviews) {
                var x = bounds.minX
                for size in row {
                    subviews[index].place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
                    x += size.width + spacing
                    index += 1
                }
                y += rowHeight(row) + spacing
            }
        }

        func rows(width: CGFloat, _ subviews: Subviews) -> [[CGSize]] {
            var rows: [[CGSize]] = [[]]
            var used: CGFloat = 0
            for subview in subviews {
                let size = subview.sizeThatFits(.unspecified)
                if !rows[rows.count - 1].isEmpty, used + spacing + size.width > width {
                    rows.append([])
                    used = 0
                }
                used += (rows[rows.count - 1].isEmpty ? 0 : spacing) + size.width
                rows[rows.count - 1].append(size)
            }
            return rows
        }
    }

    /// One topic or folder: who may read it, the notes, the sources (with how their tidying
    /// went), and, for a topic, adding a file or photo from this device.
    struct KnowledgeAreaPage: View {
        @EnvironmentObject private var store: AppStore
        @State var area: KnowledgeArea
        let overview: KnowledgeOverview
        let changed: (KnowledgeArea) -> Void
        @State private var files: KnowledgeFiles?
        @State private var importing = false
        @State private var uploading = false
        #if os(iOS)
            @State private var photo: PhotosPickerItem?
        #endif

        var body: some View {
            Form {
                Section {
                    ReaderChips(readers: overview.ordered(area.readers), empty: area.isTopic ? "谁都不能读" : "")
                    NavigationLink("修改谁能读") {
                        ReaderPicker(
                            title: "谁能读「\(area.name)」", readers: overview.readers, chosen: area.readers,
                            locked: area.locked, note: note
                        ) { names in
                            let updated = try await store.backend.setReaders(names, area: area.path)
                            area = updated
                            changed(updated)
                            return updated.readers
                        }
                    }
                } header: {
                    Text("谁能读")
                } footer: {
                    if area.isTopic && area.readers.isEmpty {
                        Text("只有你能看。整理任务不会处理这个主题，里面的东西不会发给任何模型。")
                    }
                }
                if let files {
                    if area.isTopic {
                        Section("整理好的笔记") {
                            if files.notes.isEmpty {
                                Text(area.readers.isEmpty ? "这个主题不整理。" : "还没有。放进资料以后，晚上会整理出来。")
                                    .foregroundStyle(.secondary)
                            }
                            ForEach(files.notes) { entry in row(entry) }
                        }
                    }
                    Section(area.isTopic ? "资料" : "文件") {
                        if files.sources.isEmpty {
                            Text(area.isTopic ? "还没有资料。用右上角添加，或在 Mac 上放进「知识库/\(area.name)」。" : "空的。")
                                .foregroundStyle(.secondary)
                        }
                        ForEach(files.sources) { entry in row(entry) }
                    }
                } else {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
            .settingsPage(area.name)
            .task { await load() }
            .refreshable { await load() }
            .toolbar {
                if area.isTopic {
                    ToolbarItem(placement: .primaryAction) { addMenu }
                }
            }
            .fileImporter(
                isPresented: $importing,
                allowedContentTypes: area.readers.isEmpty ? Self.types + Self.bills : Self.types
            ) { result in
                guard case .success(let url) = result else { return }
                Task { await upload(file: url) }
            }
            #if os(iOS)
                .onChange(of: photo) {
                    guard let item = photo else { return }
                    photo = nil
                    Task { await upload(photo: item) }
                }
            #endif
        }

        var note: String {
            area.isTopic
                ? "Bot 在对话里、Agent 用自己的令牌连接时，后端按这里限定。在 Mac mini 上直接读磁盘的 Agent 不受这里限制。"
                : "Bot 始终能读这个文件夹。Agent 要用自己的令牌连接，才按这里限定。"
        }

        @ViewBuilder var addMenu: some View {
            if uploading {
                ProgressView()
            } else {
                Menu {
                    Button("从文件添加", systemImage: "folder") { importing = true }
                    #if os(iOS)
                        PhotosPicker(selection: $photo, matching: .images) {
                            Label("从照片添加", systemImage: "photo")
                        }
                    #endif
                } label: {
                    Label("添加资料", systemImage: "plus")
                }
            }
        }

        func row(_ entry: KnowledgeEntry) -> some View {
            NavigationLink {
                KnowledgeFilePage(path: entry.path)
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: entry.symbol).foregroundStyle(.secondary).frame(width: 22)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(entry.title).lineLimit(2)
                        HStack(spacing: 6) {
                            if let modified = entry.modifiedAt {
                                Text(modified, format: .dateTime.month().day().hour().minute())
                            }
                            if let status = entry.statusText {
                                Text(status).foregroundStyle(Self.color(entry.status))
                            }
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                }
            }
        }

        static func color(_ status: String?) -> Color {
            switch status {
            case "pending": return .orange
            case "failed", "unreadable": return .red
            default: return .secondary
            }
        }

        static let types: [UTType] = [
            .pdf, .image, .plainText, UTType(filenameExtension: "md") ?? .plainText,
            UTType(filenameExtension: "docx") ?? .data,
        ]

        /// Bill exports for 记账Bot (docs/finance.md): only a topic nobody may read takes them,
        /// since no model ever reads such a topic.
        static let bills: [UTType] = [
            .commaSeparatedText, .zip, UTType(filenameExtension: "xlsx") ?? .spreadsheet,
        ]

        func load() async {
            do {
                files = try await store.backend.knowledgeFiles(path: area.path)
            } catch {
                store.message = "没能读到「\(area.name)」：\(AppStore.describe(error))"
            }
        }

        func upload(file url: URL) async {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            guard let data = try? Data(contentsOf: url) else {
                store.message = "读不到这个文件。"
                return
            }
            await upload(name: url.lastPathComponent, data: data)
        }

        #if os(iOS)
            func upload(photo item: PhotosPickerItem) async {
                guard let data = try? await item.loadTransferable(type: Data.self) else {
                    store.message = "读不到这张照片。"
                    return
                }
                // The library hands over the original, usually HEIC; a JPEG from elsewhere says so.
                let jpeg = data.starts(with: [0xFF, 0xD8, 0xFF])
                let png = data.starts(with: [0x89, 0x50, 0x4E, 0x47])
                let stamp = Date().formatted(.dateTime.year().month(.twoDigits).day(.twoDigits).hour().minute().second())
                    .replacingOccurrences(of: "/", with: "-").replacingOccurrences(of: ":", with: ".")
                await upload(name: "照片 \(stamp).\(jpeg ? "jpg" : png ? "png" : "heic")", data: data)
            }
        #endif

        func upload(name: String, data: Data) async {
            guard let type = Knowledge.contentType(for: name) else {
                store.message = "只能添加 PDF、图片、Markdown、txt 和 Word（docx）文件。"
                return
            }
            guard data.count <= 50 * 1024 * 1024 else {
                store.message = "文件超过 50 MB，放不进去。"
                return
            }
            uploading = true
            defer { uploading = false }
            do {
                _ = try await store.backend.uploadKnowledge(path: "\(area.path)/\(name)", data: data, contentType: type)
                await load()
                area.sources += 1
                area.pending += 1
                changed(area)
            } catch APIError.conflict {
                store.message = "「\(area.name)」里已经有「\(name)」了。改个名字再添加，原来的文件不会被替换。"
            } catch {
                store.message = "没能添加资料：\(AppStore.describe(error))"
            }
        }
    }

    /// One note or source: its text as the Mac read it, who may read it, where a note came
    /// from and which notes a source went into; the original opens in the system's preview.
    struct KnowledgeFilePage: View {
        @EnvironmentObject private var store: AppStore
        let path: String
        @State private var file: KnowledgeFile?
        @State private var failed = false
        @State private var preview: URL?
        @State private var fetching = false

        var body: some View {
            ScrollView {
                if let file {
                    content(file)
                        .padding(.horizontal, 20)
                        .padding(.vertical, 16)
                        .frame(maxWidth: 720, alignment: .leading)
                        .frame(maxWidth: .infinity)
                } else if failed {
                    Text("没能打开这个文件。").foregroundStyle(.secondary).padding(.top, 40)
                } else {
                    ProgressView().padding(.top, 40)
                }
            }
            .background(Palette.page)
            .navigationTitle(file?.title ?? Knowledge.fileName(path))
            #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
            #endif
            .task { await load() }
            .quickLookPreview($preview)
            .toolbar {
                if file?.hasOriginal == true {
                    ToolbarItem(placement: .primaryAction) {
                        if fetching {
                            ProgressView()
                        } else {
                            Button {
                                Task { await openOriginal() }
                            } label: {
                                Label("打开原件", systemImage: "eye")
                            }
                        }
                    }
                }
            }
        }

        @ViewBuilder func content(_ file: KnowledgeFile) -> some View {
            VStack(alignment: .leading, spacing: 14) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(file.title).font(Typography.serif(24))
                    Text(meta(file)).font(.caption).foregroundStyle(.secondary)
                }
                if let status = file.statusText {
                    Label(
                        [status, file.error].compactMap { $0 }.joined(separator: "："),
                        systemImage: file.status == "pending" ? "clock" : "info.circle"
                    )
                    .font(.callout)
                    .foregroundStyle(KnowledgeAreaPage.color(file.status))
                }
                if file.area != nil {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text("谁能读").font(.caption).foregroundStyle(.secondary)
                        ReaderChips(readers: readers(file))
                    }
                }
                if !file.displayText.isEmpty {
                    if file.extracted {
                        Text("下面是 Mac 从\(file.format == "image" ? "图片" : file.format == "pdf" ? " PDF " : "文件")里读出的文字。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    MarkdownText(file.displayText)
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 12).fill(Palette.card))
                        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Palette.hairline))
                    if file.truncated {
                        Text("文字太长，只显示了开头。打开原件看全部。").font(.caption).foregroundStyle(.secondary)
                    }
                }
                if !file.sources.isEmpty {
                    links("来源", file.sources.map { ($0.path, [$0.name, $0.label].compactMap { $0 }.joined(separator: " · ")) })
                }
                if !file.notes.isEmpty {
                    links("用它整理出的笔记", file.notes.map { ($0, (Knowledge.fileName($0) as NSString).deletingPathExtension) })
                }
            }
        }

        func links(_ title: String, _ items: [(path: String, text: String)]) -> some View {
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.subheadline.weight(.semibold))
                ForEach(items, id: \.path) { item in
                    NavigationLink {
                        KnowledgeFilePage(path: item.path)
                    } label: {
                        Label(item.text, systemImage: "arrow.turn.down.right")
                            .font(.callout)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.tint)
                }
            }
        }

        func readers(_ file: KnowledgeFile) -> [KnowledgeReader] {
            // The file page has no overview at hand: a reader is a Bot when the App knows it.
            file.readers.map { name in
                let bot = store.bots.first { $0.name.hasPrefix(name) && $0.kind != .source }
                return KnowledgeReader(id: name, kind: bot == nil ? "agent" : "bot", sticker: bot?.look.sticker)
            }
        }

        func meta(_ file: KnowledgeFile) -> String {
            var parts: [String] = []
            if file.kind == "note" {
                let by = file.organizedBy.map { $0 == "codex" ? "Codex" : $0 == "claude_code" ? "Claude Code" : $0 }
                parts.append("\(by ?? "Agent") 整理")
                if let at = file.organizedAt ?? file.modifiedAt {
                    parts.append(at.formatted(.dateTime.month().day().hour().minute()))
                }
            } else {
                parts.append(Self.formats[file.format] ?? "其他格式")
                parts.append(ByteCountFormatter.string(fromByteCount: Int64(file.size), countStyle: .file))
                if let at = file.modifiedAt {
                    parts.append("修改于 " + at.formatted(.dateTime.month().day().hour().minute()))
                }
            }
            return parts.joined(separator: " · ")
        }

        static let formats = ["pdf": "PDF", "image": "图片", "docx": "Word", "md": "Markdown", "txt": "文本"]

        func load() async {
            do {
                file = try await store.backend.knowledgeFile(path: path)
            } catch {
                failed = true
                store.message = "没能打开「\(Knowledge.fileName(path))」：\(AppStore.describe(error))"
            }
        }

        /// The original into a temporary file of its own name, for the system preview.
        func openOriginal() async {
            fetching = true
            defer { fetching = false }
            do {
                let data = try await store.backend.knowledgeOriginal(path: path)
                let folder = FileManager.default.temporaryDirectory.appendingPathComponent("知识库预览", isDirectory: true)
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                let url = folder.appendingPathComponent(Knowledge.fileName(path))
                try data.write(to: url, options: .atomic)
                preview = url
            } catch {
                store.message = "没能打开原件：\(AppStore.describe(error))"
            }
        }
    }

    /// Who may read something: a switch per Bot and per Agent; one that is always allowed stays
    /// on. Every change is saved straight away and undone if the Mac says no.
    @MainActor
    struct ReaderPicker: View {
        @EnvironmentObject private var store: AppStore
        let title: String
        let readers: [KnowledgeReader]
        @StateObject private var selection: KnowledgeReaderSelection
        let locked: [String]
        let note: String
        let save: ([String]) async throws -> [String]

        init(
            title: String, readers: [KnowledgeReader], chosen: [String], locked: [String],
            note: String, save: @escaping ([String]) async throws -> [String]
        ) {
            self.title = title
            self.readers = readers
            _selection = StateObject(wrappedValue: KnowledgeReaderSelection(chosen: chosen))
            self.locked = locked
            self.note = note
            self.save = save
        }

        var body: some View {
            Form {
                Section("Bot") {
                    ForEach(readers.filter(\.isBot)) { toggle($0) }
                }
                Section {
                    ForEach(readers.filter { !$0.isBot }) { toggle($0) }
                } header: {
                    Text("你自己运行的 Agent")
                } footer: {
                    Text(note)
                }
            }
            .settingsPage(title)
        }

        func toggle(_ reader: KnowledgeReader) -> some View {
            let always = locked.contains(reader.id)
            return Toggle(
                isOn: Binding(
                    get: { always || selection.chosen.contains(reader.id) },
                    set: { on in set(reader.id, on) })
            ) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(reader.id)
                    if always {
                        Text("始终能读").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .disabled(always)
        }

        func set(_ name: String, _ on: Bool) {
            selection.set(name, on, save: save) { error in
                store.message = "没能保存：\(AppStore.describe(error))"
            }
        }
    }
#endif
