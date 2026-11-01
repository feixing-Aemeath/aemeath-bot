import Foundation

/// Invented sample data for the skeleton screens and SwiftUI previews. No real names.
public enum MockData {
    public static func todos(now: Date = Date(), calendar: Calendar = DueText.calendar()) -> [Todo] {
        let today = calendar.startOfDay(for: now)
        func at(_ days: Int, _ hour: Int, _ minute: Int = 0) -> Date {
            let day = calendar.date(byAdding: .day, value: days, to: today) ?? today
            return calendar.date(bySettingHour: hour, minute: minute, second: 0, of: day) ?? day
        }
        func todo(
            _ id: String, _ title: String, due: Date?, hasTime: Bool, plugin: String,
            excerpt: String, status: TodoStatus = .pending
        ) -> Todo {
            Todo(
                id: id, title: title, dueAt: due, hasTime: hasTime, sourcePlugin: plugin,
                sourceRefs: ["mock-\(id)"], sourceExcerpt: excerpt, confidence: 0.92,
                status: status, createdAt: at(-1, 9), updatedAt: at(-1, 9))
        }
        return [
            todo(
                "1", "在学工系统完成学生信息核对", due: at(0, 17), hasTime: true,
                plugin: "qq_screen", excerpt: "@全体成员 请在今天17:00前完成学生信息核对"),
            todo(
                "2", "交数据结构第三次作业", due: at(0, 0), hasTime: false,
                plugin: "qq_screen,feishu", excerpt: "第三次作业：第4章 4.2、4.5、4.9"),
            todo(
                "3", "补交寝室卫生评比材料", due: at(-1, 12), hasTime: true,
                plugin: "qq_screen", excerpt: "明天中午12点前务必补交"),
            todo(
                "4", "提交实验室年度材料", due: at(1, 18), hasTime: true,
                plugin: "feishu", excerpt: "请每人在18:00前提交到飞书文档"),
            todo(
                "5", "把团员证交给团支书", due: at(0, 12), hasTime: true,
                plugin: "qq_screen", excerpt: "团员证还没交的同学中午之前交给我",
                status: .completed),
        ]
    }

    public static func today(now: Date = Date(), calendar: Calendar = DueText.calendar()) -> Today {
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        let dueToday = todos(now: now, calendar: calendar).filter { todo in
            guard let due = todo.dueAt, todo.status == .pending else { return false }
            return calendar.startOfDay(for: due) <= calendar.startOfDay(for: now)
        }
        return Today(
            date: formatter.string(from: now),
            summary: "班级群有 2 条截止通知，实验室提醒年度材料明天截止。",
            todos: dueToday,
            cards: [
                Card(
                    type: .text, title: "QQ 屏幕读取",
                    data: .object(["text": .string("仅预览模式：只读会话列表")])),
                Card(
                    type: .stat, title: "今日步数",
                    data: .object(["value": .number(6240), "unit": .string("步")])),
            ])
    }
}
