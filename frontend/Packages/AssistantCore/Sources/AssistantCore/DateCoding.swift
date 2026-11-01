import Foundation

/// Timestamps in the API are ISO 8601 with a time zone, e.g. `2026-10-16T09:00:00Z` or
/// `2026-10-15T16:54:37.453973Z` (the backend keeps microseconds). Calendar days such as
/// `Today.date` stay plain `YYYY-MM-DD` strings.
public enum APIDateCoding {
    public static func date(from text: String) -> Date? {
        var value = text.trimmingCharacters(in: .whitespaces)
        guard let dot = value.firstIndex(of: ".") else {
            return formatter(fractional: false).date(from: value)
        }
        // Normalize any number of fractional digits to milliseconds.
        let start = value.index(after: dot)
        let end = value[start...].firstIndex { !($0.isASCII && $0.isNumber) } ?? value.endIndex
        let digits = String(value[start..<end])
        guard !digits.isEmpty else { return nil }
        let milliseconds = String((digits + "000").prefix(3))
        value.replaceSubrange(start..<end, with: milliseconds)
        return formatter(fractional: true).date(from: value)
    }

    public static func string(from date: Date) -> String {
        formatter(fractional: false).string(from: date)
    }

    private static func formatter(fractional: Bool) -> ISO8601DateFormatter {
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.formatOptions =
            fractional ? [.withInternetDateTime, .withFractionalSeconds] : [.withInternetDateTime]
        return formatter
    }
}

extension JSONDecoder {
    /// A decoder for every API response.
    public static func assistant() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let text = try container.decode(String.self)
            guard let date = APIDateCoding.date(from: text) else {
                throw DecodingError.dataCorruptedError(
                    in: container, debugDescription: "Invalid ISO 8601 date: \(text)")
            }
            return date
        }
        return decoder
    }
}

extension JSONEncoder {
    /// An encoder for request bodies; dates are sent as UTC ISO 8601.
    public static func assistant() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .custom { date, encoder in
            var container = encoder.singleValueContainer()
            try container.encode(APIDateCoding.string(from: date))
        }
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }
}
