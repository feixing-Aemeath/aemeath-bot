#if !os(watchOS)
    import AssistantCore
    import SwiftUI
    #if os(iOS)
        import UIKit
    #endif

    /// 设置 on the iPhone and the Mac: one row per topic, each opening its own page, so the
    /// first page is a short menu rather than every switch and field at once. Shown inside a
    /// NavigationStack (the iPhone's sheet, the Mac's window).
    struct SettingsHome: View {
        @EnvironmentObject private var store: AppStore
        @AppStorage(CardStyle.storageKey) private var cardStyle = CardStyle.plain
        /// Shows a Bot's page (a message source's, which the iPhone's Bot list leaves out).
        let openBot: (String) -> Void

        var sources: [Bot] { store.bots.filter { $0.kind == .source } }

        var body: some View {
            Form {
                Section {
                    link("连接", "network", detail: connectionText) {
                        Form { ConnectionSection() }.settingsPage("连接")
                    }
                    link("插件", "puzzlepiece.extension", detail: "\(store.plugins.filter(\.enabled).count) 个已启用") {
                        PluginsPage()
                    }
                    link("消息来源", "bubble.left.and.bubble.right", detail: "\(sources.count) 个") {
                        SourcesPage(sources: sources, open: openBot)
                    }
                    #if os(iOS) && canImport(HealthKit)
                        if UIDevice.current.userInterfaceIdiom != .pad {
                            link("健康数据", "heart.text.square", detail: HealthSync.shared.asked ? "已开启" : "未开启") {
                                HealthSyncPage()
                            }
                        }
                    #endif
                }
                Section {
                    link("运行时间表", "clock", detail: nil) {
                        Form {
                            if let settings = store.settings {
                                ScheduleSection(settings: settings).id(settings)
                            } else {
                                Text("还没读到运行设置。").foregroundStyle(.secondary)
                            }
                        }
                        .settingsPage("运行时间表")
                    }
                    link("运行记录", "list.bullet.rectangle", detail: nil) {
                        RunsPage()
                    }
                    link("卡片风格", "paintpalette", detail: cardStyle.name) {
                        CardStylePage()
                    }
                    link("贴图预览", "face.smiling", detail: nil) {
                        MascotGallery().navigationTitle("贴图预览")
                    }
                }
            }
            .settingsPage("设置")
        }

        var connectionText: String {
            if store.connection.useMockData { return "模拟数据" }
            return store.online ? "Mac 在线" : "连不上"
        }

        func link<Destination: View>(
            _ title: String, _ symbol: String, detail: String?, @ViewBuilder destination: () -> Destination
        ) -> some View {
            NavigationLink {
                destination()
            } label: {
                LabeledContent {
                    if let detail { Text(detail) }
                } label: {
                    Label {
                        Text(title)
                    } icon: {
                        // One width for every symbol, so the titles line up (the Mac's grouped
                        // form does not do it for them).
                        Image(systemName: symbol).frame(width: 20)
                    }
                }
            }
        }
    }

    extension View {
        /// A page of 设置: grouped rows on the warm page color, an inline title on the iPhone.
        func settingsPage(_ title: String) -> some View {
            formStyle(.grouped)
                .scrollContentBackground(.hidden)
                .background(Palette.page)
                .navigationTitle(title)
                #if os(iOS)
                    .navigationBarTitleDisplayMode(.inline)
                #endif
        }
    }

    /// 卡片风格: the three looks of the cards, with two cards drawn in the chosen one (助理Bot's
    /// weather, 作业Bot's classes) in their Bots' colors.
    struct CardStylePage: View {
        @AppStorage(CardStyle.storageKey) private var style = CardStyle.plain

        static let samples: [(card: Card, accent: Color)] = [
            (
                Card(
                    type: .stat, title: "上海今天",
                    data: .object([
                        "value": .string("9–21"), "unit": .string("°C"),
                        "label": .string("多云 · 降水概率 10%"), "symbol": .string("cloud.sun.fill"),
                    ])),
                MascotColor(hex: "#E86A9A")!.color
            ),
            (
                Card(
                    type: .list, title: "今天的课",
                    data: .object([
                        "style": .string("timeline"),
                        "items": .array([
                            .object([
                                "title": .string("大学英语（1）"), "subtitle": .string("08:00–09:35"),
                                "state": .string("done"),
                            ]),
                            .object([
                                "title": .string("高等数学I（A）"), "subtitle": .string("09:55–12:20"),
                                "state": .string("now"),
                            ]),
                            .object(["title": .string("思想道德与法治"), "subtitle": .string("15:55–18:20")]),
                        ]),
                    ])),
                MascotColor(hex: "#D65F63")!.color
            ),
        ]

        var body: some View {
            Form {
                Section {
                    Picker("风格", selection: $style) {
                        ForEach(CardStyle.allCases) { Text($0.name).tag($0) }
                    }
                    .pickerStyle(.segmented)
                } footer: {
                    Text(style.summary)
                }
                Section("预览") {
                    ForEach(Array(Self.samples.enumerated()), id: \.offset) { _, sample in
                        CardView(card: sample.card)
                            .tint(sample.accent)
                            .listRowInsets(EdgeInsets(top: 6, leading: 0, bottom: 6, trailing: 0))
                            .listRowBackground(Color.clear)
                    }
                }
                .environment(\.cardStyle, style)
            }
            .settingsPage("卡片风格")
        }
    }

    /// Every plugin with its sticker and how it is doing; each opens its own page.
    struct PluginsPage: View {
        @EnvironmentObject private var store: AppStore

        var body: some View {
            Form {
                if store.plugins.isEmpty {
                    Text("没有读到插件列表。").foregroundStyle(.secondary)
                }
                ForEach(store.plugins) { plugin in
                    NavigationLink {
                        PluginPage(pluginID: plugin.id)
                    } label: {
                        HStack(spacing: 10) {
                            // Still: animating inside list rows re-lays out the whole list.
                            StickerImage(name: StickerLook(plugin: plugin).resolved(in: .main))
                                .frame(width: 30, height: 30)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(plugin.name)
                                Text(store.statusLine(plugin))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            Spacer(minLength: 0)
                            StatusDot(color: plugin.state.color)
                        }
                    }
                }
            }
            .settingsPage("插件")
        }
    }

    /// One plugin: its switch (and preview mode for the screen readers), the chats it reads,
    /// and what its manifest says. What the ⓘ on a Bot's page used to show.
    struct PluginPage: View {
        @EnvironmentObject private var store: AppStore
        let pluginID: String

        var body: some View {
            if let plugin = store.plugin(pluginID) {
                Form {
                    Section {
                        PluginSwitch(plugin: plugin)
                        if PluginConfigEdit.hasPreviewMode(plugin) {
                            PreviewModeSwitch(plugin: plugin)
                        }
                    } footer: {
                        if !plugin.description.isEmpty { Text(plugin.description) }
                    }
                    ForEach(PluginConfigEdit.lists(of: plugin), id: \.key) { list in
                        Section {
                            NameListEditor(plugin: plugin, key: list.key, title: list.title)
                        }
                    }
                    Section("信息") {
                        InfoRow(label: "状态", value: store.statusLine(plugin))
                        InfoRow(label: "ID", value: plugin.id)
                        InfoRow(label: "版本", value: plugin.version)
                        InfoRow(label: "平台", value: plugin.platforms.joined(separator: "、"))
                        InfoRow(label: "提供", value: plugin.provides.joined(separator: "、"))
                        InfoRow(
                            label: "权限",
                            value: plugin.permissions.isEmpty ? "无" : plugin.permissions.joined(separator: "、"))
                        InfoRow(label: "贴图", value: plugin.mascot?.sticker ?? "占位图（还没有贴图）")
                        if let schedule = plugin.schedule {
                            InfoRow(label: "时间表", value: CronText.describe(schedule))
                        }
                    }
                }
                .settingsPage(plugin.name)
            } else {
                Text("这个插件已经不在了。").foregroundStyle(.secondary)
            }
        }
    }

    /// The message sources (QQ, 飞书, 微信): tapping one closes 设置 and shows what it read.
    struct SourcesPage: View {
        @EnvironmentObject private var store: AppStore
        let sources: [Bot]
        let open: (String) -> Void

        var body: some View {
            Form {
                Section {
                    if sources.isEmpty {
                        Text("没有消息来源插件。").foregroundStyle(.secondary)
                    }
                    ForEach(sources) { bot in
                        Button {
                            open(bot.id)
                        } label: {
                            HStack(spacing: 12) {
                                BotAvatar(bot: bot, state: store.botState(bot))
                                    .frame(width: 34, height: 34)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(bot.name).foregroundStyle(.primary)
                                    Text(bot.intro).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer(minLength: 0)
                                Image(systemName: "chevron.right")
                                    .font(.footnote.weight(.semibold))
                                    .foregroundStyle(.tertiary)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                } footer: {
                    Text(sourcesFooter)
                }
            }
            .settingsPage("消息来源")
        }

        var sourcesFooter: String {
            #if os(macOS)
                "点开看它读到的消息（也在左边栏下面）。开关和白名单在「插件」里。"
            #else
                "点开看它读到的消息。开关和白名单在「插件」里。"
            #endif
        }
    }

    /// Every run of the Agent, newest first: a table on the Mac, two lines a run on the iPhone.
    struct RunsPage: View {
        @EnvironmentObject private var store: AppStore

        var body: some View {
            #if os(macOS)
                ScrollView {
                    RunTable(runs: store.runs, calendar: store.calendar).padding(20)
                }
                .background(Palette.page)
                .navigationTitle("运行记录")
            #else
                Form {
                    if store.runs.isEmpty {
                        Text("还没有运行记录").foregroundStyle(.secondary)
                    }
                    ForEach(store.runs) { run in
                        RunRow(run: run, calendar: store.calendar)
                    }
                }
                .settingsPage("运行记录")
                .refreshable { await store.refresh() }
            #endif
        }
    }
#endif
