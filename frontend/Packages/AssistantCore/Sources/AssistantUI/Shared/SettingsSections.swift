#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    struct ConnectionSection: View {
        @EnvironmentObject private var store: AppStore
        @State private var baseURL = ""
        @State private var token = ""
        @State private var useMock = true
        @State private var loaded = false

        var body: some View {
            Section("连接") {
                Toggle("使用模拟数据", isOn: $useMock)
                // Always editable: typing an address or a token means the real backend, so it
                // turns the mock data off (a switch can be hard to hit on a phone).
                TextField("后端地址", text: $baseURL, prompt: Text(ConnectionSettings.defaultURL))
                    .plainEntry(url: true)
                    .onChange(of: baseURL) { _, new in
                        // Not when the saved address is filled in on opening.
                        if new != store.connection.baseURL { useMock = false }
                    }
                SecureField(
                    "令牌", text: $token,
                    prompt: Text(store.hasToken ? "已保存在钥匙串（留空则不修改）" : "粘贴这台设备的令牌（Mac 上 scripts.tokens create）"))
                    .plainEntry(url: false)
                    .onChange(of: token) { _, new in
                        if !new.isEmpty { useMock = false }
                    }
                HStack {
                    Text(
                        store.online
                            ? (store.connection.useMockData ? "正在使用模拟数据" : "已连接 \(store.connection.baseURL)")
                            : "连不上：检查地址、令牌和 Tailscale"
                    )
                    .font(.caption)
                    .foregroundStyle(store.online ? Color.secondary : Color.red)
                    Spacer()
                    Button("保存并连接") {
                        let connection = ConnectionSettings(baseURL: baseURL, useMockData: useMock)
                        let typed = token
                        token = ""
                        Task { await store.connect(connection, token: typed) }
                    }
                    .disabled(!useMock && URL(string: baseURL)?.scheme == nil)
                }
            }
            .onAppear {
                guard !loaded else { return }
                loaded = true
                baseURL = store.connection.baseURL
                useMock = store.connection.useMockData
            }
        }
    }

    extension View {
        /// An address or a token as typed: no capital first letter, no autocorrection (the
        /// iPhone would turn https into Https).
        @ViewBuilder func plainEntry(url: Bool) -> some View {
            #if os(iOS)
                self.textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(url ? .URL : .asciiCapable)
            #else
                self.autocorrectionDisabled()
            #endif
        }
    }

    /// The run schedule: when sources are read, when todos are extracted, the confidence
    /// threshold and which Agent goes first.
    struct ScheduleSection: View {
        @EnvironmentObject private var store: AppStore
        @State private var sourceCron: String
        @State private var extractionCron: String
        @State private var threshold: Double
        @State private var firstAgent: String
        @State private var saved = false
        let settings: RuntimeSettings

        init(settings: RuntimeSettings) {
            self.settings = settings
            _sourceCron = State(initialValue: settings.sourceCron)
            _extractionCron = State(initialValue: settings.extractionCron)
            _threshold = State(initialValue: settings.confidenceThreshold)
            _firstAgent = State(initialValue: settings.harnessOrder.first ?? "claude_code")
        }

        var valid: Bool { CronText.isValid(sourceCron) && CronText.isValid(extractionCron) }

        var patch: SettingsPatch {
            var patch = SettingsPatch()
            if sourceCron != settings.sourceCron { patch.sourceCron = sourceCron }
            if extractionCron != settings.extractionCron { patch.extractionCron = extractionCron }
            if threshold != settings.confidenceThreshold { patch.confidenceThreshold = threshold }
            if firstAgent != settings.harnessOrder.first {
                patch.harnessOrder = [firstAgent] + settings.harnessOrder.filter { $0 != firstAgent }
            }
            return patch
        }

        var changed: Bool {
            patch.sourceCron != nil || patch.extractionCron != nil
                || patch.confidenceThreshold != nil || patch.harnessOrder != nil
        }

        var body: some View {
            Section("运行时间表") {
                LabeledContent("读取消息") {
                    VStack(alignment: .trailing, spacing: 2) {
                        TextField("cron", text: $sourceCron).frame(width: 200)
                        Text(CronText.describe(sourceCron)).font(.caption).foregroundStyle(.secondary)
                    }
                }
                LabeledContent("提炼待办") {
                    VStack(alignment: .trailing, spacing: 2) {
                        TextField("cron", text: $extractionCron).frame(width: 200)
                        Text(CronText.describe(extractionCron)).font(.caption).foregroundStyle(.secondary)
                    }
                }
                LabeledContent("待办最低置信度") {
                    HStack {
                        Slider(value: $threshold, in: 0.5...0.95, step: 0.05).frame(width: 200)
                        Text(String(format: "%.2f", threshold)).monospacedDigit()
                    }
                }
                Picker("优先使用的 Agent", selection: $firstAgent) {
                    ForEach(settings.harnessOrder.isEmpty ? ["claude_code", "codex"] : settings.harnessOrder, id: \.self) { id in
                        Text(Names.harness(id)).tag(id)
                    }
                }
                HStack {
                    Text("时区 \(settings.timezone)；cron 五段依次是：分 时 日 月 星期。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    if saved {
                        Text("已保存").font(.caption).foregroundStyle(.green)
                    }
                    Button("保存时间表") {
                        let patch = self.patch
                        Task { saved = await store.saveSettings(patch) }
                    }
                    .disabled(!valid || !changed)
                }
            }
        }
    }

#endif
