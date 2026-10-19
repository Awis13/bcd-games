import SwiftUI

/// One option Madeira reads from Documents/bcd.cfg: a plain key
/// ("vram-mb = 4352") or an environment switch the app exports before Wine
/// starts ("env.MADEIRA_SWAP_COVERAGE = classic"). The list itself is
/// ConfigCatalog.generated.swift, written by build/tools/gen-config-catalog.py
/// from the code that reads each option; check-config-catalog.py fails when it
/// falls behind the code.
struct ConfigOption: Identifiable {
    enum Kind { case bool, int, text, choice }
    struct Choice: Hashable { let value: String; let label: String }

    let key: String
    let title: String
    let kind: Kind
    let defaultValue: String
    let category: String
    let note: String
    let choices: [Choice]
    let sources: [String]
    var id: String { key }
    var displayTitle: String { title.isEmpty ? key : title }

    init(key: String, title: String, kind: Kind, defaultValue: String, category: String, note: String,
         choices: [(String, String)], sources: [String]) {
        self.key = key; self.title = title; self.kind = kind; self.defaultValue = defaultValue
        self.category = category; self.note = note; self.sources = sources
        self.choices = choices.map { Choice(value: $0.0, label: $0.1) }
    }
}

enum ConfigCatalog {
    /// Categories in the order the catalog lists them.
    static let categories: [String] = {
        var seen = Set<String>()
        return generated.map(\.category).filter { seen.insert($0).inserted }
    }()
    static let keys = Set(generated.map(\.key))
}
