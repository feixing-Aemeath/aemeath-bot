import XCTest

@testable import AssistantCore

final class CardPackingTests: XCTestCase {
    func testHealthCardsGoByHeightTallestFirst() {
        // 预警 (no alerts), 准备度, 昨晚睡眠, 睡眠分期 chart, 作息规律, 静息心率, HRV, 训练负荷.
        let heights = [90.0, 130, 110, 250, 140, 100, 100, 120]
        XCTAssertEqual(
            CardPacking.rows(heights: heights, columns: 2),
            [[3], [1, 2], [4, 7], [0, 5], [6]])
        // Three columns: the chart alone, then the four of 105 points or more, then the rest.
        XCTAssertEqual(
            CardPacking.rows(heights: heights, columns: 3),
            [[3], [1, 2, 4], [7], [0, 5, 6]])
    }

    func testEqualCardsKeepTheirOrder() {
        XCTAssertEqual(
            CardPacking.rows(heights: [100, 100, 100, 100, 100], columns: 2),
            [[0, 1], [2, 3], [4]])
    }

    func testOneColumnStillPutsTheTallOnTop() {
        XCTAssertEqual(
            CardPacking.rows(heights: [80, 300, 120, 290], columns: 1),
            [[1], [3], [2], [0]])
        XCTAssertEqual(CardPacking.rows(heights: [], columns: 3), [])
        XCTAssertEqual(CardPacking.rows(heights: [50], columns: 0), [[0]])
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

    func testAGroupIsMeasuredFromItsTallestCard() {
        // 100, 80, 64: each is 80% of the one before, but 64 is under 75% of 100.
        XCTAssertEqual(CardPacking.rows(heights: [64, 80, 100], columns: 3), [[1, 2], [0]])
    }
}
