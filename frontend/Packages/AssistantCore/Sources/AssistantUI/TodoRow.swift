import AssistantCore
import SwiftUI

/// One todo: title, deadline and where it came from.
public struct TodoRow: View {
    let todo: Todo
    let compact: Bool

    public init(todo: Todo, compact: Bool = false) {
        self.todo = todo
        self.compact = compact
    }

    private var overdue: Bool {
        todo.status == .pending && DueText.isOverdue(todo, now: Date(), calendar: DueText.calendar())
    }

    public var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: todo.status == .completed ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(todo.status == .completed ? Color.green : overdue ? Color.red.opacity(0.75) : Color.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(todo.title)
                    .font(compact ? CardFont.note : CardFont.body.weight(.medium))
                    .strikethrough(todo.status == .completed)
                    .lineLimit(compact ? 2 : 3)
                Text(compact ? DueText.describe(todo) : DueText.describe(todo) + " · " + SourceLabel.text(todo.sourcePlugin))
                    .font(compact ? .caption2 : CardFont.note)
                    .foregroundStyle(overdue ? Color.red : Color.secondary)
            }
        }
        .padding(.vertical, compact ? 0 : 3)
    }
}

/// "qq_screen,feishu" → "QQ · 飞书".
public enum SourceLabel {
    public static func text(_ plugins: String) -> String {
        Names.plugins(plugins)
    }
}
