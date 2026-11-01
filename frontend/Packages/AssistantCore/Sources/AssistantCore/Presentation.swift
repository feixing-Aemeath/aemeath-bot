import Foundation

// Texts and groupings the screens show. Plain functions of API data, unit tested, so the
// SwiftUI views stay thin.

/// Display names of plugins and Agents.
public enum Names {
    public static func plugin(_ id: String) -> String {
        switch id {
        case "qq_screen": return "QQ"
        case "wechat_screen": return "微信"
        case "feishu": return "飞书"
        case "sms": return "短信"
        case "mail": return "邮件"
        case "photos": return "照片"
        case "health": return "健康"
        case "homework": return "作业"
        case "campus": return "请假与学校活动"
        case "grades": return "学分成绩"
        case "weather": return "天气"
        case "timetable": return "课表"
        case "goodnotes": return "GoodNotes"
        case "class_photos": return "课堂照片"
        case "video_notes": return "视频"
        case "finance": return "记账"
        // Lines typed to a Bot (POST /api/v1/bots/{id}/messages) are messages of this source.
        case "chat": return "对话"
        default: return id
        }
    }

    /// "qq_screen,feishu" → "QQ · 飞书".
    public static func plugins(_ ids: String) -> String {
        ids.split(separator: ",").map { plugin(String($0)) }.joined(separator: " · ")
    }

    public static func harness(_ id: String?) -> String {
        guard let id else { return "—" }
        switch id {
        case "claude_code": return "Claude Code"
        case "codex": return "Codex"
        case "deepseek": return "DeepSeek"
        default: return id
        }
    }

    /// Job IDs of docs/api.md in words.
    public static func job(_ id: String) -> String {
        switch id {
        case "extract_todos": return "提炼待办"
        case "apple_sync": return "同步提醒事项"
        case "backup": return "备份"
        case "memory_index": return "整理记忆索引"
        case "daily_notes": return "整理每日文件夹"
        case "knowledge_organize": return "整理知识库"
        default: break
        }
        if id.hasPrefix("fetch:") {
            return "读取\(plugin(String(id.dropFirst("fetch:".count))))"
        }
        if id.hasPrefix("chat:") {
            let bot = String(id.dropFirst("chat:".count))
            if bot == Bot.knowledgeID { return "回复对话 · 知识库" }
            return bot == Bot.assistantID ? "回复对话" : "回复对话 · \(plugin(bot))"
        }
        if id.hasPrefix("plugin:") {
            let parts = id.split(separator: ":").map(String.init)
            guard parts.count >= 3 else { return id }
            let task = parts[2...].joined(separator: ":")
            let words: [String: String] = [
                "morning_report": "晨间简报", "weekly_report": "周报", "mcp": "工具调用",
                "mcp_tools": "注册工具", "cards": "卡片", "setup": "启动", "limits": "读取用量限额",
                "read": "读取", "daily": "整理每日文件夹", "forecast": "读取天气预报", "brief": "今日提醒",
            ]
            return "\(plugin(parts[1])) · \(words[task] ?? task)"
        }
        return id
    }
}

/// "刚刚", "8 分钟前", "3 小时前", "昨天 21:05", "10月12日 08:30".
public enum RelativeTime {
    public static func describe(_ date: Date, now: Date, calendar: Calendar) -> String {
        let seconds = now.timeIntervalSince(date)
        if seconds < 60 { return "刚刚" }
        if seconds < 3600 { return "\(Int(seconds / 60)) 分钟前" }
        if calendar.isDate(date, inSameDayAs: now) { return "\(Int(seconds / 3600)) 小时前" }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
            calendar.isDate(date, inSameDayAs: yesterday)
        {
            return "昨天 " + DueText.format(date, "HH:mm", calendar)
        }
        return DueText.format(date, "M月d日 HH:mm", calendar)
    }

    /// "45 秒", "2 分 05 秒", "1 小时 3 分".
    public static func duration(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds.rounded()))
        if total < 60 { return "\(total) 秒" }
        if total < 3600 { return String(format: "%ld 分 %02ld 秒", total / 60, total % 60) }
        return "\(total / 3600) 小时 \((total % 3600) / 60) 分"
    }
}

/// Where a todo came from: "QQ · 软件工程2班 · 李老师", plus how many other places.
public enum TodoSourceText {
    public static func describe(_ todo: Todo) -> String {
        guard let sources = todo.sources, let first = sources.first else {
            return Names.plugins(todo.sourcePlugin)
        }
        var parts = [Names.plugin(first.plugin), first.chatName]
        if !first.sender.isEmpty && first.sender != first.chatName {
            parts.append(first.sender)
        }
        var text = parts.joined(separator: " · ")
        let places = Set(sources.map { "\($0.plugin)\u{1f}\($0.chatName)" }).count
        if places > 1 {
            text += " 等 \(places) 处"
        }
        return text
    }
}

/// Whether a todo reached the Reminders list.
public enum SyncState: Hashable, Sendable {
    case synced
    case pending
    case failed(String)
    /// The watchdog's notes about a broken data source never go to Reminders.
    case appOnly

    public init(_ todo: Todo) {
        if todo.sourcePlugin == "watchdog" {
            self = .appOnly
        } else if let error = todo.syncError, !error.isEmpty {
            self = .failed(error)
        } else if todo.syncStatus == .synced {
            self = .synced
        } else {
            self = .pending
        }
    }

    public var text: String {
        switch self {
        case .synced: return "已同步到提醒事项"
        case .pending: return "等待同步到提醒事项"
        case .failed: return "同步提醒事项失败"
        case .appOnly: return "只在 App 里，不进提醒事项"
        }
    }
}

/// Run results in words.
public enum RunText {
    public static func status(_ status: RunStatus) -> String {
        switch status {
        case .running: return "运行中"
        case .succeeded: return "成功"
        case .failed: return "失败"
        case .cancelled: return "已取消"
        }
    }

    /// Error categories the backend records (never CLI output).
    public static func error(_ code: String?) -> String? {
        guard let code, !code.isEmpty else { return nil }
        let words: [String: String] = [
            "quota_exhausted": "额度用完",
            "authentication_required": "需要重新登录",
            "cli_unavailable": "找不到 Agent 命令",
            "timeout": "超时",
            "agent_failed": "Agent 运行失败",
            "agent_incomplete": "Agent 没有做完",
            "messages_remaining": "还有消息没处理完",
            "all_harnesses_failed": "两个 Agent 都失败了",
            "harness_exception": "Agent 适配器出错",
            "agent_runner_unavailable": "Agent 运行器不可用",
            "report_not_written": "简报没有写出来",
            "apple_sync_failed": "同步提醒事项失败",
            "apple_helper_failed": "提醒事项工具出错",
            "screen_helper_failed": "截图助手出错",
            "screen_history_gap": "没接上上次读到的位置",
            "screen_chat_mismatch": "打开的会话不对",
            "screen_not_conversation_list": "没停在消息列表（点回「消息」）",
            "screen_recording_denied": "没有屏幕录制权限",
            "screen_accessibility_denied": "没有辅助功能权限",
            "screen_app_not_running": "聊天软件没有打开",
            "screen_window_not_found": "找不到主窗口（不要最小化）",
            "screen_capture_failed": "截图失败",
            "screen_chat_not_in_list": "会话列表里找不到这个会话",
            "screen_chat_not_open": "没能打开这个会话",
            "feishu_oauth_failed": "飞书授权失败",
            "feishu_history_failed": "读取飞书消息失败",
            "plugin_setup_failed": "插件启动失败",
            "plugin_tool_failed": "插件工具出错",
            "plugin_cards_failed": "插件卡片出错",
            "job_failed": "任务失败",
            "interrupted": "后端重启时中断",
            "internal_error": "内部错误",
        ]
        return words[code] ?? code
    }

    /// How long a run took, or has been running.
    public static func duration(_ run: Run, now: Date) -> String {
        RelativeTime.duration((run.finishedAt ?? now).timeIntervalSince(run.startedAt))
    }
}

/// The one-line status under a plugin's sticker: "上次运行在 10 分钟前，提取了 3 条待办".
/// The line under 知识库Bot's name: the knowledge base being organized, one of her inputs that
/// could not start, or which inputs bring material in.
public enum KnowledgeStatusLine {
    public static func describe(inputs: [PluginView], runs: [Run], now: Date, calendar: Calendar) -> String {
        if let running = runs.first(where: { $0.job == "knowledge_organize" && $0.status == .running }) {
            return "正在整理知识库，已经 \(RunText.duration(running, now: now))"
        }
        if let broken = inputs.first(where: { $0.state == .error }) {
            let line = PluginStatusLine.describe(broken, runs: runs, todos: [], now: now, calendar: calendar)
            return "\(Names.plugin(broken.id))\(line)"
        }
        guard !inputs.isEmpty else { return "整理知识库里的资料" }
        let on = inputs.filter { $0.state == .enabled }
        if on.isEmpty { return "资料来源都还没打开" }
        return "资料来源：" + on.map { Names.plugin($0.id) }.joined(separator: "、")
    }
}

public enum PluginStatusLine {
    /// The plugin's own runs (`fetch:<id>`, `plugin:<id>:*`), newest first.
    public static func runs(of plugin: String, in runs: [Run]) -> [Run] {
        runs.filter { $0.job == "fetch:\(plugin)" || $0.job.hasPrefix("plugin:\(plugin):") }
    }

    public static func describe(
        _ plugin: PluginView, runs: [Run], todos: [Todo], now: Date, calendar: Calendar
    ) -> String {
        switch plugin.state {
        case .disabled: return "已停用"
        case .unsupported: return "这台电脑上不能运行"
        case .error: return "出错：" + (RunText.error(plugin.error) ?? "未知原因")
        case .enabled: break
        }
        let own = Self.runs(of: plugin.id, in: runs)
        guard let last = own.first else { return "还没有运行过" }
        if last.status == .running {
            return "正在运行，已经 \(RunText.duration(last, now: now))"
        }
        let when = RelativeTime.describe(last.finishedAt ?? last.startedAt, now: now, calendar: calendar)
        if last.status != .succeeded {
            let reason = RunText.error(last.error) ?? RunText.status(last.status)
            return "上次运行在 \(when)，\(reason)"
        }
        // Only message sources have todos extracted from them.
        guard plugin.provides.contains("source") else { return "上次运行在 \(when)" }
        let extracted = todos.filter { todo in
            todo.status != .deleted && todo.createdAt >= last.startedAt
                && todo.sourcePlugin.split(separator: ",").contains(Substring(plugin.id))
        }.count
        return "上次运行在 \(when)，提取了 \(extracted) 条待办"
    }
}

/// The digest page: per source, per day, what was read and which todos came out of it.
public struct DigestDay: Hashable, Identifiable, Sendable {
    public var id: String { date }
    /// `YYYY-MM-DD`.
    public var date: String
    /// The source's fetch runs that day, oldest first.
    public var reads: [Run]
    /// One entry per batch the Agent summarized.
    public var notes: [String]
    public var todos: [Todo]
}

public struct DigestSection: Hashable, Identifiable, Sendable {
    public var id: String { plugin }
    public var plugin: String
    public var name: String
    public var days: [DigestDay]
}

public enum DigestSections {
    public static func build(
        digests: [Digest], todos: [Todo], runs: [Run], calendar: Calendar
    ) -> [DigestSection] {
        func day(_ date: Date) -> String { DueText.format(date, "yyyy-MM-dd", calendar) }
        var plugins: [String] = []
        for digest in digests where !plugins.contains(digest.plugin) {
            plugins.append(digest.plugin)
        }
        return plugins.sorted { Names.plugin($0) < Names.plugin($1) }.map { plugin -> DigestSection in
            let own = digests.filter { $0.plugin == plugin }
            let dates = Set(own.map(\.date)).sorted(by: >)
            let days = dates.map { date -> DigestDay in
                let notes = own.filter { $0.date == date }
                    .flatMap { $0.summary.split(separator: "\n") }
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                    .filter { !$0.isEmpty }
                let reads = runs.filter { $0.job == "fetch:\(plugin)" && day($0.startedAt) == date }
                    .sorted { $0.startedAt < $1.startedAt }
                let extracted = todos.filter { todo in
                    todo.status != .deleted && day(todo.createdAt) == date
                        && todo.sourcePlugin.split(separator: ",").contains(Substring(plugin))
                }
                return DigestDay(date: date, reads: reads, notes: notes, todos: extracted)
            }
            return DigestSection(plugin: plugin, name: Names.plugin(plugin), days: days)
        }
    }
}

/// The run schedule in words, for the common cron forms the settings use.
public enum CronText {
    /// Five fields: minute hour day month weekday.
    public static func isValid(_ cron: String) -> Bool {
        cron.split(separator: " ", omittingEmptySubsequences: true).count == 5
    }

    public static func describe(_ cron: String) -> String {
        let fields = cron.split(separator: " ", omittingEmptySubsequences: true).map(String.init)
        guard fields.count == 5 else { return "格式不对：需要 5 段（分 时 日 月 星期）" }
        let (minute, hour, day, month, weekday) = (fields[0], fields[1], fields[2], fields[3], fields[4])
        guard day == "*", month == "*", weekday == "*" else { return cron }
        if minute.hasPrefix("*/"), hour == "*", let step = Int(minute.dropFirst(2)) {
            return "每 \(step) 分钟"
        }
        guard let start = Int(minute) else { return cron }
        let clock = String(format: "%02ld", start)
        if let value = Int(hour) {
            return "每天 \(value):\(clock)"
        }
        if hour == "*" {
            return "每小时的第 \(start) 分"
        }
        // "7-23/3": every 3 hours from 7 to 23.
        let parts = hour.split(separator: "/").map(String.init)
        if parts.count == 2, let step = Int(parts[1]) {
            let range = parts[0].split(separator: "-").compactMap { Int($0) }
            if range.count == 2 {
                return "每天 \(range[0]):\(clock)–\(range[1]):\(clock)，每 \(step) 小时"
            }
            if parts[0] == "*" {
                return "每 \(step) 小时"
            }
        }
        return cron
    }
}

/// What the menu bar shows.
public enum MenuBarSummary {
    /// Todos due today or overdue that are still open.
    public static func remaining(_ today: Today?) -> Int {
        today?.todos.filter { $0.status == .pending }.count ?? 0
    }
}

/// The Agent status page: the headline under the sticker and the latest run.
public enum AgentText {
    public static func headline(_ state: MascotState) -> String {
        switch state {
        case .offline: return "连不上 Mac"
        case .working: return "正在运行"
        case .error: return "上次运行失败"
        case .done: return "刚刚完成"
        case .idle: return "Mac 在线，空闲中"
        }
    }

    /// "最近一次：提炼待办 · 失败 · 3 分钟前 · 额度用完".
    public static func lastRun(_ run: Run?, now: Date, calendar: Calendar) -> String {
        guard let run else { return "还没有运行记录" }
        let when = RelativeTime.describe(run.startedAt, now: now, calendar: calendar)
        var text = "最近一次：\(Names.job(run.job)) · \(RunText.status(run.status)) · \(when)"
        if let reason = RunText.error(run.error) {
            text += " · \(reason)"
        }
        return text
    }
}

/// The plugin settings the App edits itself: lists of chat names, and preview mode of the
/// screen-reading sources. A change replaces the whole configuration (PATCH config), so the
/// other keys are carried over unchanged.
public enum PluginConfigEdit {
    /// Lists of names a plugin's schema may have, with what the App calls them.
    public static let lists: [(key: String, title: String)] = [
        ("whitelist", "白名单会话"), ("course_chats", "课程群"),
    ]

    public static func lists(of plugin: PluginView) -> [(key: String, title: String)] {
        lists.filter { plugin.configSchema["properties"]?[$0.key] != nil }
    }

    public static func names(_ plugin: PluginView, _ key: String) -> [String] {
        plugin.config[key]?.arrayValue?.compactMap(\.stringValue) ?? []
    }

    /// A name added at the end: trimmed, and not again if it is already there.
    public static func adding(_ name: String, to names: [String]) -> [String] {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !names.contains(trimmed) else { return names }
        return names + [trimmed]
    }

    public static func hasPreviewMode(_ plugin: PluginView) -> Bool {
        plugin.configSchema["properties"]?["preview_only"] != nil
    }

    /// Preview mode is on unless switched off (the schema's default).
    public static func previewOnly(_ plugin: PluginView) -> Bool {
        plugin.config["preview_only"]?.boolValue ?? true
    }

    public static func setting(_ plugin: PluginView, _ key: String, to value: JSONValue) -> JSONValue {
        var object = plugin.config.objectValue ?? [:]
        object[key] = value
        return .object(object)
    }
}
