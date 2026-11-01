import Foundation

/// One entry of the Mac sidebar: the App's own assistant, a plugin with a character of its own,
/// the App's keeper of the knowledge base, or a message source. A Bot's face is its sticker (docs/mascot.md) and what it did reads like
/// a chat (`BotTimeline`).
public struct Bot: Hashable, Identifiable, Sendable {
    public enum Kind: Hashable, Sendable {
        /// The App's own assistant: all of today's todos and the Agent.
        case assistant
        /// A plugin that is a character, e.g. grades or health.
        case character
        /// 知识库Bot, the App's own keeper of the knowledge base: the plugins that bring it material
        /// (GoodNotes, 课堂照片, 视频) join her page, and the knowledge base opens from it.
        case knowledge
        /// A plugin that only reads messages (QQ, 飞书, 微信): an icon, not a character.
        case source
    }

    public static let assistantID = "assistant"
    public static let assistantName = "助理Bot"
    public static let knowledgeID = "knowledge"
    public static let knowledgeName = "知识库Bot"

    /// `assistant` or the plugin's id.
    public var id: String
    public var name: String
    /// What it looks after: 个人助理, or the plugin's name.
    public var role: String
    public var kind: Kind
    public var look: StickerLook
    /// SF Symbol for a source.
    public var icon: String
    /// Plugins of the same character whose cards this Bot's page shows after its own, e.g. 课表
    /// with 作业Bot's 作业 (`role` then names both).
    public var joined: [String] = []

    public var isAssistant: Bool { kind == .assistant }

    /// The assistant or 知识库Bot: Bots of the App itself, there even without any plugin.
    public var isBuiltIn: Bool { kind == .assistant || kind == .knowledge }

    /// The plugins whose cards the page shows: its own, then the joined ones (for the App's own
    /// Bots, only the joined ones, e.g. 天气).
    public var pluginIDs: [String] { isBuiltIn ? joined : [id] + joined }

    /// What the Bot looks after, two words or so, the same way for every Bot: its own duties,
    /// or for a Bot that took in other plugins (作业Bot) the first duty of each.
    public var duties: [String] {
        if !joined.isEmpty, !isBuiltIn {
            return pluginIDs.map { Self.dutyNames[$0]?.first ?? $0 }
        }
        return Self.dutyNames[id] ?? [role]
    }

    /// One line under the name in the Bot list: the duties as “…”与“…”; a plugin the App does
    /// not know yet shows its name as it is.
    public var intro: String {
        guard joined.isEmpty || isBuiltIn, Self.dutyNames[id] == nil else {
            return duties.map { "“\($0)”" }.joined(separator: "与")
        }
        return kind == .source ? "消息来源" : role
    }

    /// The tag under the name on the Bot's page: the duties as 「学分 · 成绩」.
    public var tag: String { duties.joined(separator: " · ") }

    static let dutyNames = [
        assistantID: ["个人助理"],
        knowledgeID: ["知识库", "课堂笔记"],
        "campus": ["请假", "学校活动"],
        "grades": ["学分", "成绩"],
        "health": ["健康", "睡眠"],
        "homework": ["作业", "截止时间"],
        "timetable": ["课表", "上课时间"],
        "token_usage": ["Token 余额", "消耗"],
        "finance": ["记账", "预算"],
        "qq_screen": ["QQ 群", "私聊"],
        "wechat_screen": ["微信群", "私聊"],
        "feishu": ["飞书群", "单聊"],
    ]

    public init(
        id: String, name: String, role: String = "", kind: Kind = .character, look: StickerLook,
        icon: String = "puzzlepiece.extension"
    ) {
        self.id = id
        self.name = name
        self.role = role
        self.kind = kind
        self.look = look
        self.icon = icon
    }
}

public enum BotList {
    /// The assistant first, then the character plugins, then 知识库Bot (the lists draw a line above
    /// her), then the message sources (plugins that provide `source`); running ones before
    /// stopped ones, otherwise in the backend's order. A character is called by its sticker's name
    /// in the stickers folder ("成绩Bot"), otherwise by the plugin's name. Plugins with the same
    /// sticker are one character, so one Bot: the first of them, with the others' cards joined to
    /// its page; plugins with the assistant's own sticker (天气) join the assistant, and those with
    /// 知识库's (GoodNotes, 课堂照片, 视频, app.json's `knowledge`) join her.
    public static func build(plugins: [PluginView], catalog: StickerCatalog) -> [Bot] {
        let assistantName = catalog.assistant.sticker.flatMap { catalog.characters[$0] } ?? Bot.assistantName
        var assistant = Bot(
            id: Bot.assistantID, name: assistantName, role: "个人助理", kind: .assistant,
            look: catalog.assistant, icon: "checklist")
        let shelf = catalog.look(role: Bot.knowledgeID)
        var knowledge = Bot(
            id: Bot.knowledgeID, name: shelf.sticker.flatMap { catalog.characters[$0] } ?? Bot.knowledgeName,
            role: "知识库", kind: .knowledge, look: shelf, icon: "books.vertical")
        let running = plugins.filter { $0.state == .enabled || $0.state == .error }
        let stopped = plugins.filter { $0.state == .disabled || $0.state == .unsupported }
        let bots = (running + stopped).map { plugin -> Bot in
            let look = StickerLook(plugin: plugin)
            if plugin.provides.contains("source") {
                return Bot(
                    id: plugin.id, name: plugin.name, role: "消息来源", kind: .source, look: look,
                    icon: plugin.icon)
            }
            let name = look.sticker.flatMap { catalog.characters[$0] } ?? plugin.name
            return Bot(id: plugin.id, name: name, role: plugin.name, kind: .character, look: look, icon: plugin.icon)
        }
        var characters: [Bot] = []
        for bot in bots where bot.kind == .character {
            if let sticker = bot.look.sticker, sticker == assistant.look.sticker {
                assistant.joined.append(bot.id)
            } else if let sticker = bot.look.sticker, sticker == knowledge.look.sticker {
                knowledge.joined.append(bot.id)
            } else if let sticker = bot.look.sticker, catalog.contains(sticker),
                let index = characters.firstIndex(where: { $0.look.sticker == sticker })
            {
                characters[index].joined.append(bot.id)
                characters[index].role += " · \(bot.role)"
            } else {
                characters.append(bot)
            }
        }
        return [assistant] + characters + [knowledge] + bots.filter { $0.kind == .source }
    }
}

/// One bubble of a Bot's chat.
public struct BotMessage: Hashable, Identifiable, Sendable {
    public enum Author: Hashable, Sendable {
        case bot
        case me
        /// A note from the App itself, shown small in the middle.
        case app
    }

    public var id: String
    public var author: Author
    public var date: Date
    public var title: String?
    /// Markdown.
    public var text: String
    /// Todos that came out of it, e.g. on the day of a digest.
    public var todos: [Todo]
    public var failed: Bool

    public init(
        id: String, author: Author, date: Date, title: String? = nil, text: String,
        todos: [Todo] = [], failed: Bool = false
    ) {
        self.id = id
        self.author = author
        self.date = date
        self.title = title
        self.text = text
        self.todos = todos
        self.failed = failed
    }

    /// One line for the sidebar, without Markdown's bold marks.
    public var preview: String {
        let line = title ?? text.split(separator: "\n").first.map(String.init) ?? ""
        return line.replacingOccurrences(of: "**", with: "")
    }
}

public enum BotTimeline {
    /// What a Bot said, with what was typed to it (`chat`), oldest first. A plugin: one message
    /// per day of digests with the todos extracted that day, and for the health plugin its
    /// reports too. The assistant: today's todo extraction runs and today's summary.
    public static func messages(
        for bot: Bot, today: Today?, digests: [Digest], todos: [Todo], runs: [Run],
        reports: [HealthReport], chat: [BotMessage] = [], now: Date, calendar: Calendar
    ) -> [BotMessage] {
        let said =
            bot.isAssistant
            ? assistant(today: today, digests: digests, runs: runs, now: now, calendar: calendar)
            : digestDays(of: bot.id, digests: digests, todos: todos, runs: runs, calendar: calendar)
                + (bot.id == "health" ? reports.map(message) : [])
        let messages = said + chat
        // Stable, so messages at the same time keep the order above.
        return messages.enumerated()
            .sorted { ($0.element.date, $0.offset) < ($1.element.date, $1.offset) }
            .map(\.element)
    }

    static func digestDays(
        of plugin: String, digests: [Digest], todos: [Todo], runs: [Run], calendar: Calendar
    ) -> [BotMessage] {
        let section = DigestSections.build(
            digests: digests, todos: todos, runs: runs, calendar: calendar
        ).first { $0.plugin == plugin }
        return (section?.days ?? []).compactMap { day in
            let written = digests.filter { $0.plugin == plugin && $0.date == day.date }
            guard let last = written.map(\.createdAt).max() else { return nil }
            return BotMessage(
                id: "digest-\(plugin)-\(day.date)", author: .bot, date: last,
                text: day.notes.joined(separator: "\n"), todos: day.todos)
        }
    }

    static func message(_ report: HealthReport) -> BotMessage {
        let kind = report.kind == .morning ? "晨间简报" : "周报"
        let id = "report-\(report.id)"
        switch report.status {
        case .ready:
            return BotMessage(
                id: id, author: .bot, date: report.updatedAt,
                title: report.title.map { "\(kind) · \($0)" } ?? kind, text: report.body ?? "")
        case .pending:
            return BotMessage(id: id, author: .bot, date: report.updatedAt, title: kind, text: "正在写…")
        case .failed:
            return BotMessage(
                id: id, author: .bot, date: report.updatedAt, title: kind,
                text: "没有生成：\(RunText.error(report.error) ?? "未知原因")", failed: true)
        }
    }

    static func assistant(
        today: Today?, digests: [Digest], runs: [Run], now: Date, calendar: Calendar
    ) -> [BotMessage] {
        let extractions = runs.filter {
            $0.job == "extract_todos" && calendar.isDate($0.startedAt, inSameDayAs: now)
        }
        var messages = extractions.map { run -> BotMessage in
            let agent = run.attempt > 1
                ? "\(Names.harness(run.harness))（第 \(run.attempt) 次）" : Names.harness(run.harness)
            let text: String
            switch run.status {
            case .running: text = "正在提炼待办… · \(agent)"
            case .succeeded: text = "提炼完一次待办 · \(agent) · 用时 \(RunText.duration(run, now: now))"
            case .failed: text = "提炼待办失败：\(RunText.error(run.error) ?? "未知原因") · \(agent)"
            case .cancelled: text = "提炼待办被取消了 · \(agent)"
            }
            return BotMessage(
                id: "run-\(run.id)", author: .bot, date: run.finishedAt ?? run.startedAt, text: text,
                failed: run.status == .failed)
        }
        if let today, !today.summary.isEmpty {
            // The summary comes from today's digests, so it is as new as the newest of them.
            let written = digests.filter { $0.date == today.date }.map(\.createdAt).max()
            messages.append(
                BotMessage(
                    id: "summary-\(today.date)", author: .bot, date: written ?? now, title: "今天的总结",
                    text: today.summary))
        }
        return messages
    }

    /// How many things the Bot said after its page was last open; without a record, after the
    /// App first started, so a new install does not light up every Bot.
    public static func unread(_ messages: [BotMessage], seen: Date?, firstLaunch: Date) -> Int {
        let since = seen ?? firstLaunch
        return messages.filter { $0.author == .bot && $0.date > since }.count
    }

    /// The messages of the last `days` days that have any (oldest first), and how many earlier
    /// days are left out. The page opens on the face and the cards, so the chat below them
    /// starts with the latest days instead of a week of history to scroll through.
    public static func recent(_ messages: [BotMessage], days: Int, calendar: Calendar)
        -> (messages: [BotMessage], hiddenDays: Int)
    {
        let starts = messages.map { calendar.startOfDay(for: $0.date) }
        let distinct = Array(Set(starts)).sorted(by: >)
        guard days > 0, distinct.count > days else { return (messages, 0) }
        let cutoff = distinct[days - 1]
        let shown = zip(messages, starts).filter { $0.1 >= cutoff }.map(\.0)
        return (shown, distinct.count - days)
    }

    /// The divider above a day's first message: 今天, 昨天 or 10月21日.
    public static func day(_ date: Date, now: Date, calendar: Calendar) -> String {
        if calendar.isDate(date, inSameDayAs: now) { return "今天" }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
            calendar.isDate(date, inSameDayAs: yesterday)
        {
            return "昨天"
        }
        return DueText.format(date, "M月d日", calendar)
    }
}

/// A Bot's chat (`/api/v1/bots/{bot_id}/messages`) as bubbles. A pending reply has no bubble:
/// the page shows the typing indicator instead. A failed one becomes a small note.
public enum BotChat {
    public static func messages(_ chat: [ChatMessage]) -> [BotMessage] {
        chat.compactMap { line in
            switch (line.author, line.status) {
            case (.me, _):
                return BotMessage(id: line.id, author: .me, date: line.createdAt, text: line.text)
            case (.bot, .ready):
                return BotMessage(id: line.id, author: .bot, date: line.updatedAt, text: line.text)
            case (.bot, .pending):
                return nil
            case (.bot, .failed):
                return BotMessage(
                    id: line.id, author: .app, date: line.updatedAt, text: failure(line.error), failed: true)
            }
        }
    }

    public static func isReplying(_ chat: [ChatMessage]) -> Bool {
        chat.contains { $0.author == .bot && $0.status == .pending }
    }

    static func failure(_ error: String?) -> String {
        switch error {
        case "interrupted":
            return "没回复完：Mac 上的后端重启了，再发一次吧。"
        case "all_harnesses_failed":
            return "没能回复：Claude Code 和 Codex 都没完成，可能还没登录，或者额度用完了。"
        default:
            return "没能回复，再发一次试试。"
        }
    }
}
