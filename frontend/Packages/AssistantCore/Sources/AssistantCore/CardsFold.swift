import Foundation

/// When the cards above a Bot's chat fold away by themselves (the Mac page): once their end
/// has scrolled out of the top of the window, or at once on a hard flick down. They never open
/// by themselves; the grip does that.
public struct CardsFold: Sendable {
    public enum Reason: Equatable, Sendable {
        /// The cards are out of sight; folding them must not move what is on screen.
        case scrolledPast
        /// A hard scroll down: fold them in view, so the chat comes up.
        case flick
    }

    /// How far above the window the end of the cards must be.
    public static let margin = 24.0
    /// Points per second downward that count as a hard flick (reading is well under 2000).
    public static let flickSpeed = 3000.0
    /// Speed is measured over the samples of the last `window` seconds, spanning at least
    /// `span`: a single jump (a restored scroll position) is not a flick, a spun wheel is.
    static let window = 0.12
    static let span = 0.04  // three frames at 60 Hz, five at 120 Hz

    private var samples: [(time: TimeInterval, offset: Double)] = []

    public init() {}

    /// For each scroll position: `offset`, how far the page is scrolled down; `cardsEnd`,
    /// where the end of the cards is in the window (negative above its top).
    public mutating func check(offset: Double, cardsEnd: Double, at time: TimeInterval) -> Reason? {
        samples.append((time, offset))
        samples.removeAll { time - $0.time > Self.window }
        if cardsEnd < -Self.margin {
            return .scrolledPast
        }
        guard samples.count >= 3, let first = samples.first, time - first.time >= Self.span else {
            return nil
        }
        return (offset - first.offset) / (time - first.time) >= Self.flickSpeed ? .flick : nil
    }
}
