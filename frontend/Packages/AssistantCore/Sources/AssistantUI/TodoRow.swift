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
                .foregroundStyle(todo.status == .completed ? Color.green : Color.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(todo.title)
                    .font(compact ? .footnote : .body)
                    .strikethrough(todo.status == .completed)
                    .lineLimit(compact ? 2 : 3)
                Text(DueText.describe(todo))
                    .font(.caption2)
                    .foregroundStyle(overdue ? Color.red : Color.secondary)
                if !compact {
                    Text(SourceLabel.text(todo.sourcePlugin))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, compact ? 0 : 2)
    }
}

/// "qq_screen,feishu" → "QQ · 飞书".
public enum SourceLabel {
    public static func text(_ plugins: String) -> String {
        Names.plugins(plugins)
    }
}
