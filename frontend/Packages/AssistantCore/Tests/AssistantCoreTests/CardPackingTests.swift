import XCTest

@testable import AssistantCore

final class CardPackingTests: XCTestCase {
    func testTheGridKeepsTheOrderAndStacksTilePairs() {
        // 助理: 今日提醒 (text), 上海今天 (tile), 出门提醒 (list), 明天 (tile): one row of three
        // cells, the two weather tiles together in the middle.
        XCTAssertEqual(
            CardPacking.grid(tiles: [false, true, false, true], columns: 4),
            [[[0], [1, 3], [2]]])
        // 健康 in three columns: 准备度 + 睡眠, the chart, 作息 + 静息心率; then HRV + 负荷, 记录.
        XCTAssertEqual(
            CardPacking.grid(tiles: [true, true, false, true, true, true, true, false], columns: 3),
            [[[0, 1], [2], [3, 4]], [[5, 6], [7]]])
        XCTAssertEqual(CardPacking.grid(tiles: [], columns: 3), [])
        XCTAssertEqual(CardPacking.grid(tiles: [false], columns: 0), [[[0]]])
    }

    func testThePhoneKeepsTheOrderAndPairsTiles() {
        // 准备度, 睡眠 (tiles), a chart, 作息 (tile), a list, 静息心率 (tile): 作息 pairs with
        // 静息心率, moved up; a tile with nothing left to pair fills its row.
        XCTAssertEqual(
            CardPacking.phoneRows(tiles: [true, true, false, true, false, true]),
            [[0, 1], [2], [3, 5], [4]])
        XCTAssertEqual(CardPacking.phoneRows(tiles: [false, true, false]), [[0], [1], [2]])
        XCTAssertEqual(CardPacking.phoneRows(tiles: []), [])
    }
}
