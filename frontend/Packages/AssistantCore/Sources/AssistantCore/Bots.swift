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
    /// Cards the Bot attached to a chat reply, as they were then.
    public var cards: [Card]
    public var failed: Bool

    public init(
        id: String, author: Author, date: Date, title: String? = nil, text: String,
        todos: [Todo] = [], cards: [Card] = [], failed: Bool = false
    ) {
        self.id = id
        self.author = author
        self.date = date
        self.title = title
        self.text = text
        self.todos = todos
        self.cards = cards
        self.failed = failed
    }

    /// One line for the Bot lists, without Markdown's bold marks: the title, or the first line
    /// that says something, past a section name over a list (「**新待办**」 then 「- …」) and an
    /// item's dot. A bold line over plain text is a headline and stays.
    public var preview: String {
        if let title { return title.replacingOccurrences(of: "**", with: "") }
        let lines = RichLine.lines(text)
        for (index, line) in lines.enumerated() {
            let said: String
            switch line {
            case .bullet(let item), .numbered(_, let item), .heading(let item): said = item
            case .text(let raw): said = raw
            case .table(let table): said = table.header.joined(separator: " · ")
            case .blank: continue
            case .section(let name):
                let next = lines[(index + 1)...].first { $0 != .blank }
                switch next {
                case .bullet, .numbered: continue
                default: said = name
                }
            }
            let clean = said.replacingOccurrences(of: "**", with: "").trimmingCharacters(in: .whitespaces)
            if !clean.isEmpty { return clean }
        }
        return ""
    }
}

public enum BotTimeline {
    /// What a Bot said, with what was typed to it (`chat`), oldest first. A plugin: one message
    /// per day of digests with the todos extracted that day, and for the health plugin its
    /// reports too. The assistant: today's summary, and a todo extraction that is going on or
    /// went wrong.
    public static func messages(
        for bot: Bot, today: Today?, digests: [Digest], todos: [Todo], runs: [Run],
        reports: [HealthReport], chat: [BotMessage] = [], now: Date, calendar: Calendar
    ) -> [BotMessage] {
        let said =
            bot.isAssistant
            ? assistant(today: today, digests: digests, todos: todos, runs: runs, now: now, calendar: calendar)
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
                text: spaced(day.notes), todos: day.todos)
        }
    }

    /// A day's digest lines with a blank line above each section name (「**通知**」) but the
    /// first, as the Agent wrote them; `DigestSections` drops blank lines.
    static func spaced(_ lines: [String]) -> String {
        lines.enumerated()
            .map { index, line in index > 0 && line.hasPrefix("**") ? "\n" + line : line }
            .joined(separator: "\n")
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
        today: Today?, digests: [Digest], todos: [Todo], runs: [Run], now: Date, calendar: Calendar
    ) -> [BotMessage] {
        let extractions = runs.filter {
            $0.job == "extract_todos" && calendar.isDate($0.startedAt, inSameDayAs: now)
        }
        // Only what matters to the user: a run going on or gone wrong is a message of its own.
        // The ones that went fine say nothing here: 「今天提炼了 4 次待办 · Codex」 was a line of
        // the run log in her voice, and lit her unread dot every time one finished. When she
        // last read is under her name, and every run is in 设置's 运行记录.
        var messages = extractions.compactMap { run -> BotMessage? in
            let agent = run.attempt > 1
                ? "\(Names.harness(run.harness))（第 \(run.attempt) 次）" : Names.harness(run.harness)
            let text: String
            switch run.status {
            case .succeeded: return nil
            case .running: text = "正在提炼待办… · \(agent)"
            case .failed: text = "提炼待办失败：\(RunText.error(run.error) ?? "未知原因") · \(agent)"
            case .cancelled: text = "提炼待办被取消了 · \(agent)"
            }
            return BotMessage(
                id: "run-\(run.id)", author: .bot, date: run.finishedAt ?? run.startedAt, text: text,
                failed: run.status == .failed)
        }
        if let today, !today.summary.isEmpty || todos.contains(where: { calendar.isDate($0.createdAt, inSameDayAs: now) }) {
            // The summary comes from today's digests, so it is as new as the newest of them.
            let written = digests.filter { $0.date == today.date }.map(\.createdAt).max()
            messages.append(
                BotMessage(
                    id: "summary-\(today.date)", author: .bot, date: written ?? now, title: "今天的总结",
                    text: today.summary, todos: todos.filter {
                        calendar.isDate($0.createdAt, inSameDayAs: now) && $0.status != .deleted && $0.status != .cancelled
                    }))
        }
        return messages
    }

    /// How many things the Bot said after its page was last open; without a record, after the
    /// App first started, so a new install does not light up every Bot.
    public static func unread(_ messages: [BotMessage], seen: Date?, firstLaunch: Date) -> Int {
        let since = seen ?? firstLaunch
        return messages.filter { $0.author == .bot && $0.date > since }.count
    }

    /// Today's messages and those of the `earlier` latest days before today that have any
    /// (oldest first), and the earlier days still left out, newest first. The page opens on
    /// the face, the cards and today; each tap on the button above brings back one more day.
    public static func recent(_ messages: [BotMessage], earlier: Int, now: Date, calendar: Calendar)
        -> (messages: [BotMessage], hidden: [Date])
    {
        let today = calendar.startOfDay(for: now)
        let starts = messages.map { calendar.startOfDay(for: $0.date) }
        let before = Array(Set(starts.filter { $0 < today })).sorted(by: >)
        let shown = Set(before.prefix(max(earlier, 0)))
        let kept = zip(messages, starts).filter { $0.1 >= today || shown.contains($0.1) }.map(\.0)
        return (kept, Array(before.dropFirst(max(earlier, 0))))
    }

    /// Whether a message shows its Bot's face, name and time above it: not when it follows
    /// another of the Bot's own (not a failure) on the same day within 10 minutes.
    public static func startsGroup(at index: Int, in messages: [BotMessage], calendar: Calendar) -> Bool {
        guard index > 0, index < messages.count else { return true }
        let message = messages[index], previous = messages[index - 1]
        guard message.author == .bot, previous.author == .bot, !previous.failed else { return true }
        return !calendar.isDate(message.date, inSameDayAs: previous.date)
            || message.date.timeIntervalSince(previous.date) > 600
    }

    /// When a Bot last spoke, beside its row in the Bot list: 09:41 today, 昨天, or 10月21日.
    public static func listTime(_ date: Date, now: Date, calendar: Calendar) -> String {
        if calendar.isDate(date, inSameDayAs: now) { return DueText.format(date, "HH:mm", calendar) }
        return day(date, now: now, calendar: calendar)
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
    /// Only today's latest ready Bot line, while visible, may offer replies.
    public static func replyQuestion(
        _ chat: [ChatMessage], visible: [BotMessage]? = nil,
        now: Date = Date(), calendar: Calendar = .current
    ) -> ChatMessage? {
        guard let latest = chat.max(by: {
            $0.createdAt == $1.createdAt ? $0.updatedAt < $1.updatedAt : $0.createdAt < $1.createdAt
        }), latest.author == .bot, latest.status == .ready,
            let replies = latest.replies, !replies.isEmpty,
            calendar.isDate(latest.updatedAt, inSameDayAs: now)
        else { return nil }
        if let visible, !visible.contains(where: { $0.id == latest.id }) { return nil }
        return latest
    }

    public static func messages(_ chat: [ChatMessage]) -> [BotMessage] {
        chat.compactMap { line in
            switch (line.author, line.status) {
            case (.me, _):
                return BotMessage(id: line.id, author: .me, date: line.createdAt, text: line.text)
            case (.bot, .ready):
                return BotMessage(
                    id: line.id, author: .bot, date: line.updatedAt, text: line.text, cards: line.cards ?? [])
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
