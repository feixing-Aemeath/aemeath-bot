import Foundation

/// Where a Bot's cards go, always in their order (the plugin's priority, its `first` cards
/// leading): on the iPhone's one column each card a row, tiles two to a row; in several columns
/// (the Mac, the iPad) rows of cells of equal height, a pair of tiles sharing one cell.
public enum CardPacking {
    /// Columns that fit the actual detail width, including the gaps between cards.
    public static func columns(width: Double, minWidth: Double, spacing: Double) -> Int {
        guard width.isFinite, minWidth.isFinite, spacing.isFinite,
            width > 0, minWidth > 0, spacing >= 0
        else { return 1 }
        return max(1, Int((width + spacing) / (minWidth + spacing)))
    }

    /// Rows for several columns: the same cells as on the iPhone (a card, or two tiles that
    /// go together), `columns` cells to a row in their order. A row's cells are drawn as tall
    /// as its tallest, so the rows line up; two tiles in a cell stand one above the other.
    public static func grid(tiles: [Bool], columns: Int) -> [[[Int]]] {
        let cells = phoneRows(tiles: tiles)
        let columns = max(1, columns)
        return stride(from: 0, to: cells.count, by: columns).map {
            Array(cells[$0..<min($0 + columns, cells.count)])
        }
    }

    /// The cards in the first `rows` rows, in their order: what 仪表盘 shows of a Bot before
    /// 「还有 N 张卡片」. One column has the iPhone's rows (a card, or two tiles side by side);
    /// several have the grid's (a pair of tiles is one cell). The cards shown pack into the same
    /// rows again, since a tile's partner is always the next tile after it.
    public static func firstRows(tiles: [Bool], columns: Int, rows: Int) -> [Int] {
        let packed = columns <= 1 ? phoneRows(tiles: tiles) : grid(tiles: tiles, columns: columns).map { $0.flatMap { $0 } }
        return packed.prefix(max(rows, 0)).flatMap { $0 }.sorted()
    }

    /// The cards in as many of the iPhone's first rows as hold at most `cards` cards (the first
    /// row whatever it holds): its 「first couple of cards」 without splitting a pair of tiles.
    public static func firstRows(tiles: [Bool], holding cards: Int) -> [Int] {
        var kept: [Int] = []
        for row in phoneRows(tiles: tiles) {
            guard kept.isEmpty || kept.count + row.count <= cards else { break }
            kept += row
        }
        return kept.sorted()
    }

    /// Columns for `count` small tiles of at least `minWidth`, with the rows as even as they can
    /// be: seven tiles where six fit go four and three, not six and one.
    public static func balancedColumns(count: Int, width: Double, minWidth: Double, spacing: Double) -> Int {
        guard count > 0 else { return 1 }
        let fit = min(count, columns(width: width, minWidth: minWidth, spacing: spacing))
        let rows = (count + fit - 1) / fit
        return (count + rows - 1) / rows
    }

    /// Rows on the iPhone's one column: the cards in their given order (the plugins'), each
    /// a full-width row, except tiles, which go two to a row. A tile pairs with the next tile
    /// after it, moved up if need be; one left without a partner fills its row.
    public static func phoneRows(tiles: [Bool]) -> [[Int]] {
        var rows: [[Int]] = []
        var unpairedRow: Int?
        for (index, tile) in tiles.enumerated() {
            if tile, let row = unpairedRow {
                rows[row].append(index)
                unpairedRow = nil
            } else {
                if tile { unpairedRow = rows.count }
                rows.append([index])
            }
        }
        return rows
    }
}
