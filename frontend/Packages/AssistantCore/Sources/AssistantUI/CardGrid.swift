import AssistantCore
import SwiftUI

/// Marks a card that may share a row with another on the iPhone (a stat tile).
struct CardTileKey: LayoutValueKey {
    static let defaultValue = false
}

/// A Bot's cards, in their order. In one column (the iPhone) each is as wide as the page, and
/// tiles (`CardTileKey`) go two to a row, as tall as the taller. In as many columns of at least
/// `minWidth` as fit (the Mac, the iPad) they go in rows of equal height (CardPacking.grid), so
/// tops and bottoms line up; two tiles share a cell, one above the other.
struct CardGrid: Layout {
    var minWidth: CGFloat = 240
    var spacing: CGFloat = 12
    /// A short last row's cells share the width, as a single row's do (in a Bot's group on
    /// 仪表盘, where an empty slot would be a hole in the group).
    var fillsLastRow = false

    struct Placement {
        var index: Int
        var origin: CGPoint
        var size: CGSize
    }

    func placements(width: CGFloat, subviews: Subviews) -> (items: [Placement], height: CGFloat) {
        let columns = CardPacking.columns(width: Double(width), minWidth: Double(minWidth), spacing: Double(spacing))
        var items: [Placement] = []
        if columns == 1 {
            var y: CGFloat = 0
            for indices in CardPacking.phoneRows(tiles: subviews.map { $0[CardTileKey.self] }) {
                let count = CGFloat(indices.count)
                let cardWidth = (width - spacing * (count - 1)) / count
                let height = indices.map { self.height(of: subviews[$0], width: cardWidth) }.max() ?? 0
                for (slot, index) in indices.enumerated() {
                    items.append(
                        Placement(
                            index: index, origin: CGPoint(x: CGFloat(slot) * (cardWidth + spacing), y: y),
                            size: CGSize(width: cardWidth, height: height)))
                }
                y += height + spacing
            }
            return (items, max(y - spacing, 0))
        }
        let rows = CardPacking.grid(tiles: subviews.map { $0[CardTileKey.self] }, columns: columns)
        var y: CGFloat = 0
        for (number, row) in rows.enumerated() {
            // All in one row: the cells share the width; otherwise a short last row keeps the
            // columns of the rows above, unless it fills its row.
            let shares = rows.count == 1 || (fillsLastRow && number == rows.count - 1)
            let used = shares ? max(row.count, 1) : columns
            let column = (width - spacing * CGFloat(used - 1)) / CGFloat(used)
            let heights = row.map { cell in cell.map { height(of: subviews[$0], width: column) } }
            let cellHeights = heights.map { $0.reduce(0, +) + spacing * CGFloat(max($0.count - 1, 0)) }
            let rowHeight = cellHeights.max() ?? 0
            for (slot, cell) in row.enumerated() {
                let x = CGFloat(slot) * (column + spacing)
                // Every cell as tall as the row; two tiles share what is left over evenly.
                let extra = (rowHeight - cellHeights[slot]) / CGFloat(max(cell.count, 1))
                var top = y
                for (place, index) in cell.enumerated() {
                    let height = heights[slot][place] + extra
                    items.append(
                        Placement(index: index, origin: CGPoint(x: x, y: top), size: CGSize(width: column, height: height)))
                    top += height + spacing
                }
            }
            y += rowHeight + spacing
        }
        return (items, max(y - spacing, 0))
    }

    func height(of subview: LayoutSubview, width: CGFloat) -> CGFloat {
        subview.sizeThatFits(ProposedViewSize(width: width, height: nil)).height
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        // SwiftUI also asks with no width and with an infinite one (Int(.infinity) traps).
        let width = proposal.width.flatMap { $0.isFinite ? max($0, 0) : nil } ?? minWidth
        return CGSize(width: width, height: placements(width: width, subviews: subviews).height)
    }

    func placeSubviews(
        in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()
    ) {
        for item in placements(width: bounds.width, subviews: subviews).items {
            subviews[item.index].place(
                at: CGPoint(x: bounds.minX + item.origin.x, y: bounds.minY + item.origin.y), anchor: .topLeading,
                proposal: ProposedViewSize(item.size))
        }
    }
}
