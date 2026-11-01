import Foundation

/// The two numbers logged each day to calibrate the readiness score
/// (docs/readiness-calibration.md): the readiness shown in Apple's Fitness app (0-10) and how
/// ready the owner feels (1-5). They travel as ordinary health samples
/// (`POST /api/v1/health/samples`); for one day the backend keeps the latest entry.
public struct ReadinessCheckin: Equatable, Sendable {
    public static let appleType = "apple_fitness_readiness"
    public static let perceivedType = "perceived_readiness"
    public static let source = "24小时助手 App"

    public var appleReadiness: Double?
    public var perceived: Int?

    public init(appleReadiness: Double? = nil, perceived: Int? = nil) {
        self.appleReadiness = appleReadiness
        self.perceived = perceived
    }

    /// "7", "7.5" or "7,5" between 0 and 10; nil for anything else, including empty text.
    public static func parseApple(_ text: String) -> Double? {
        let cleaned = text.trimmingCharacters(in: .whitespaces)
            .replacingOccurrences(of: "，", with: ".")
            .replacingOccurrences(of: ",", with: ".")
        guard !cleaned.isEmpty, let value = Double(cleaned), value.isFinite, (0...10).contains(value)
        else { return nil }
        return value
    }

    public var isValid: Bool {
        guard appleReadiness != nil || perceived != nil else { return false }
        if let apple = appleReadiness, !(0...10).contains(apple) { return false }
        if let feeling = perceived, !(1...5).contains(feeling) { return false }
        return true
    }

    /// When an entry is stamped: now for today; for an earlier day, that day at the current
    /// time of day. Either way a later correction for the same day has a later time, and the
    /// backend files it under the day it is for.
    public static func moment(for day: Date, now: Date, calendar: Calendar) -> Date {
        if calendar.isDate(day, inSameDayAs: now) { return now }
        let time = calendar.dateComponents([.hour, .minute, .second], from: now)
        return calendar.date(
            bySettingHour: time.hour ?? 12, minute: time.minute ?? 0, second: time.second ?? 0,
            of: day) ?? day
    }

    public func batch(for day: Date, now: Date, calendar: Calendar) -> HealthBatch {
        let at = Self.moment(for: day, now: now, calendar: calendar)
        // A new ID per entry: resending the same ID would be ignored as a duplicate.
        let stamp = Int((now.timeIntervalSince1970 * 1000).rounded())
        var samples: [HealthSample] = []
        if let appleReadiness {
            samples.append(
                HealthSample(
                    id: "checkin-\(Self.appleType)-\(stamp)", type: Self.appleType,
                    value: appleReadiness, unit: "", startAt: at, endAt: at, source: Self.source))
        }
        if let perceived {
            samples.append(
                HealthSample(
                    id: "checkin-\(Self.perceivedType)-\(stamp)", type: Self.perceivedType,
                    value: Double(perceived), unit: "", startAt: at, endAt: at, source: Self.source))
        }
        return HealthBatch(samples: samples)
    }
}
