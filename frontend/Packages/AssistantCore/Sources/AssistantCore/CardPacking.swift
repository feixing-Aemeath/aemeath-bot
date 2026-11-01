import Foundation

/// The order and rows of a Bot's cards in a grid with no holes: cards of about the same height
/// go together, the tallest group first, so each row holds cards of one size; a row's cards are
/// then drawn as tall as the row, and a group's last, shorter row shares the whole width.
public enum CardPacking {
    /// Columns that fit the actual detail width, including the gaps between cards.
    public static func columns(width: Double, minWidth: Double, spacing: Double) -> Int {
        guard width.isFinite, minWidth.isFinite, spacing.isFinite,
            width > 0, minWidth > 0, spacing >= 0
        else { return 1 }
        return max(1, Int((width + spacing) / (minWidth + spacing)))
    }

    /// Two cards are the same size when the shorter is at least this share of the taller.
    public static let sameSize = 0.75

    /// Rows of card indices for `columns` columns, from each card's height at one column's
    /// width. Within a size group the cards keep their given order (the plugin's priority).
    public static func rows(heights: [Double], columns: Int) -> [[Int]] {
        let columns = max(1, columns)
        let tallestFirst = heights.indices.sorted {
            heights[$0] != heights[$1] ? heights[$0] > heights[$1] : $0 < $1
        }
        var groups: [[Int]] = []
        for index in tallestFirst {
            // Compared with the group's tallest card, so a group cannot creep down in size.
            if let tallest = groups.last?.first, heights[index] >= heights[tallest] * sameSize {
                groups[groups.count - 1].append(index)
            } else {
                groups.append([index])
            }
        }
        return groups.flatMap { group -> [[Int]] in
            let ordered = group.sorted()
            return stride(from: 0, to: ordered.count, by: columns).map {
                Array(ordered[$0..<min($0 + columns, ordered.count)])
            }
        }
    }

    /// Rows on the iPhone's one column: the cards in their given order (the plugins'), each
    /// a full-width row, except tiles, which go two to a row as squares. A tile pairs with the
    /// next tile after it, moved up if need be; one left without a partner fills its row.
    public static func phoneRows(tiles: [Bool]) -> [[Int]] {
        var rows: [[Int]] = []
        var placed = Set<Int>()
        for index in tiles.indices where !placed.contains(index) {
            placed.insert(index)
            if tiles[index], let partner = tiles.indices.first(where: { $0 > index && tiles[$0] && !placed.contains($0) }) {
                placed.insert(partner)
                rows.append([index, partner])
            } else {
                rows.append([index])
            }
        }
        return rows
    }
}
