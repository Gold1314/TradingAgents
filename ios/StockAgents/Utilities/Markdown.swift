import SwiftUI

/// Renders agent report markdown. The web app uses `marked` (full GFM incl.
/// tables); for the MVP we use Foundation's `AttributedString(markdown:)`, which
/// covers headings-as-text, emphasis, lists, links, and inline code — the bulk
/// of what the agents emit. Block tables degrade to monospaced raw text.
///
/// TODO (later milestone): swap in swift-markdown or a richer renderer for full
/// table/heading fidelity, and validate against real agent output (plan §8).
enum MarkdownRenderer {
    static func attributed(_ source: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(
            allowsExtendedAttributes: true,
            interpretedSyntax: .inlineOnlyPreservingWhitespace,
            failurePolicy: .returnPartiallyParsedIfPossible
        )
        if let parsed = try? AttributedString(markdown: source, options: options) {
            return parsed
        }
        return AttributedString(source)
    }
}

/// A scrollable, selectable markdown body for an agent card.
struct MarkdownText: View {
    let source: String

    var body: some View {
        Text(MarkdownRenderer.attributed(source))
            .font(.callout)
            .foregroundStyle(Theme.textPrimary)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
