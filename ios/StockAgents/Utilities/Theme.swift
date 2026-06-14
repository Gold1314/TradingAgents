import SwiftUI

/// Color helpers so the hex strings ported from the web app (`NODE`, `STAGES`,
/// `RATING_COLOR`) can be used directly in SwiftUI.
extension Color {
    /// Build a Color from a "#rrggbb" / "rrggbb" hex string. Falls back to gray.
    init(hex: String) {
        let cleaned = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var value: UInt64 = 0
        guard Scanner(string: cleaned).scanHexInt64(&value), cleaned.count == 6 else {
            self = .gray
            return
        }
        let red = Double((value & 0xFF0000) >> 16) / 255.0
        let green = Double((value & 0x00FF00) >> 8) / 255.0
        let blue = Double(value & 0x0000FF) / 255.0
        self = Color(red: red, green: green, blue: blue)
    }
}

enum Theme {
    static let background = Color(hex: "#0f172a")
    static let surface = Color(hex: "#1e293b")
    static let surfaceMuted = Color(hex: "#0b1220")
    static let border = Color(hex: "#334155")
    static let textPrimary = Color(hex: "#e2e8f0")
    static let textMuted = Color(hex: "#94a3b8")
    static let accent = Color(hex: "#34d399")
    static let warning = Color(hex: "#fbbf24")
    static let danger = Color(hex: "#f43f5e")
    static let upColor = Color(hex: "#34d399")
    static let downColor = Color(hex: "#fb7185")
}
