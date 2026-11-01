import XCTest

@testable import AssistantCore

final class CardsFoldTests: XCTestCase {
    /// Scrolls by `step` points every 1/60 s from `offset`, with the end of the cards starting
    /// at `cardsEnd`; returns the first reason and how many frames it took.
    func scroll(
        _ fold: inout CardsFold, from offset: Double = 0, cardsEnd: Double, step: Double,
        frames: Int, start: TimeInterval = 100
    ) -> (CardsFold.Reason, Int)? {
        for frame in 0..<frames {
            let moved = step * Double(frame)
            let time = start + Double(frame) / 60
            if let reason = fold.check(offset: offset + moved, cardsEnd: cardsEnd - moved, at: time) {
                return (reason, frame)
            }
        }
        return nil
    }

    func testReadingThroughTheCardsFoldsThemOnlyOnceTheyAreOutOfSight() {
        var fold = CardsFold()
        // 10 points a frame is 600 points a second: reading speed, never a flick.
        let result = scroll(&fold, cardsEnd: 900, step: 10, frames: 200)
        XCTAssertEqual(result?.0, .scrolledPast)
        XCTAssertEqual(result?.1, 93)  // 900 - 930 = -30, the first end past -24
    }

    func testAHardFlickFoldsAtOnce() {
        var fold = CardsFold()
        // 80 points a frame is 4800 points a second; samples over 40 ms are needed.
        let result = scroll(&fold, cardsEnd: 900, step: 80, frames: 20)
        XCTAssertEqual(result?.0, .flick)
        XCTAssertEqual(result?.1, 3)
    }

    func testJumpsAndUpwardFlicksDoNotFold() {
        var fold = CardsFold()
        // A restored position: one jump of 2000 points, then nothing.
        XCTAssertNil(fold.check(offset: 0, cardsEnd: 900, at: 10))
        XCTAssertNil(fold.check(offset: 600, cardsEnd: 300, at: 10.016))
        XCTAssertNil(fold.check(offset: 600, cardsEnd: 300, at: 11))
        // Back up fast.
        var up = CardsFold()
        XCTAssertNil(scroll(&up, from: 3000, cardsEnd: 900, step: -80, frames: 30))
    }

    func testAPauseStartsTheMeasureAgain() {
        var fold = CardsFold()
        XCTAssertNil(fold.check(offset: 0, cardsEnd: 900, at: 10))
        XCTAssertNil(fold.check(offset: 100, cardsEnd: 800, at: 10.02))
        // 300 points after half a second still: two samples in the window, not a flick.
        XCTAssertNil(fold.check(offset: 400, cardsEnd: 500, at: 10.5))
        XCTAssertNil(fold.check(offset: 500, cardsEnd: 400, at: 10.52))
    }
}
