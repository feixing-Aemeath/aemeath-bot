import AssistantCore
import SwiftUI

/// Cards in as many columns of at least `minWidth` as fit, without holes: rows come from
/// CardPacking (cards of one size together, the tallest first), every card in a row is drawn as
/// tall as the row, and a row with fewer cards than columns shares the whole width. In one
/// column (the iPhone) the cards keep their order, and tiles (`CardTileKey`) go two to a row
/// as squares.
/// Marks a card that may share a row with another as a square on the iPhone.
struct CardTileKey: LayoutValueKey {
    static let defaultValue = false
}

struct CardGrid: Layout {
    var minWidth: CGFloat = 240
    var spacing: CGFloat = 12

    struct Row {
        var indices: [Int]
        var cardWidth: CGFloat
        var height: CGFloat
    }

    func rows(width: CGFloat, subviews: Subviews) -> [Row] {
        let columns = CardPacking.columns(width: Double(width), minWidth: Double(minWidth), spacing: Double(spacing))
        if columns == 1 {
            return CardPacking.phoneRows(tiles: subviews.map { $0[CardTileKey.self] }).map { indices in
                let count = CGFloat(indices.count)
                let cardWidth = (width - spacing * (count - 1)) / count
                let height = indices.map { self.height(of: subviews[$0], width: cardWidth) }.max() ?? 0
                // Two tiles: about square, however little their numbers take.
                return Row(indices: indices, cardWidth: cardWidth, height: count > 1 ? max(height, cardWidth * 0.8) : height)
            }
        }
        let column = (width - spacing * CGFloat(columns - 1)) / CGFloat(columns)
        let heights = subviews.map { Double(height(of: $0, width: column)) }
        return CardPacking.rows(heights: heights, columns: columns).map { indices in
            let count = CGFloat(indices.count)
            let cardWidth = (width - spacing * (count - 1)) / count
            let height = indices.map { self.height(of: subviews[$0], width: cardWidth) }.max() ?? 0
            return Row(indices: indices, cardWidth: cardWidth, height: height)
        }
    }

    func height(of subview: LayoutSubview, width: CGFloat) -> CGFloat {
        subview.sizeThatFits(ProposedViewSize(width: width, height: nil)).height
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        // SwiftUI also asks with no width and with an infinite one (Int(.infinity) traps).
        let width = proposal.width.flatMap { $0.isFinite ? max($0, 0) : nil } ?? minWidth
        let rows = rows(width: width, subviews: subviews)
        let height = rows.map(\.height).reduce(0, +) + spacing * CGFloat(max(rows.count - 1, 0))
        return CGSize(width: width, height: height)
    }

    func placeSubviews(
        in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()
    ) {
        var y = bounds.minY
        for row in rows(width: bounds.width, subviews: subviews) {
            var x = bounds.minX
            for index in row.indices {
                subviews[index].place(
                    at: CGPoint(x: x, y: y), anchor: .topLeading,
                    proposal: ProposedViewSize(width: row.cardWidth, height: row.height))
                x += row.cardWidth + spacing
            }
            y += row.height + spacing
        }
    }
}
