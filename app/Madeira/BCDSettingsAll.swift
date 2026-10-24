// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

// Settings › All settings: every option the engine reads from bcd.cfg (the generated
// settings catalog), searchable, each with what its code says about it and an editor for its
// value. The same keys and the same writes as before: a value set back to the standard one
// leaves the file. Shown words never carry the old name (BCDSettingsText): env.MADEIRA_X is
// shown as env.X and still written as env.MADEIRA_X.

/// One option of the catalog.
struct BCDConfigEntry: Identifiable, Hashable {
    enum Kind: Hashable { case toggle, number, text, choice }
    var key: String
    var title: String
    var kind: Kind
    var defaultValue: String
    var category: String
    var note: String
    var choices: [BCDSettingsChoice]
    var id: String { key }
}

@MainActor protocol BCDAllSettingsBackend: ObservableObject {
    /// Every option of the catalog but those another section shows (Memory's switches).
    var entries: [BCDConfigEntry] { get }
    /// The catalog's categories, in its order.
    var categories: [String] { get }
    /// Every key the catalog knows; the file's other lines are listed apart.
    var catalogKeys: Set<String> { get }
    /// The config file as it is now.
    var values: [String: String] { get }
    /// Writes one option; nil (or empty) takes it out of the file.
    func set(_ key: String, _ value: String?)
    /// Something changed this run: most options are read when BCD starts.
    var changed: Bool { get }
    func refresh()
}

struct BCDAllSettings<Backend: BCDAllSettingsBackend>: View {
    @ObservedObject var backend: Backend
    @EnvironmentObject private var model: BCDSettingsModel
    @Environment(\.bcdLayout) private var layout
    @State private var query = ""
    @State private var newKey = ""
    @State private var newValue = ""
    @FocusState private var typing: Field?

    private enum Field: Hashable { case search, newKey, newValue }

    /// A line of the list: a category's heading or one option.
    private enum Line: Identifiable {
        case heading(String)
        case option(BCDConfigEntry, first: Bool, last: Bool)
        var id: String {
            switch self {
            case .heading(let title): return "heading." + title
            case .option(let entry, _, _): return entry.key
            }
        }
    }

    var body: some View {
        let shown = backend.entries.filter { matches($0) }
        let unlisted = unlistedKeys
        VStack(alignment: .leading, spacing: 16) {
            Text("Every option BCD reads from bcd.cfg, with what its code says about each. Most are read when BCD starts. Standard leaves an option out of the file; on a controller, Y puts one back. The memory switches are in Memory & performance.")
                .font(BCDStyle.body(layout == .pad ? 15 : 13.5)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
            searchField(count: shown.count)
            if backend.changed { BCDSettingsNotice(text: bcdSettingsRestartNote) }
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(lines(shown)) { line in
                    switch line {
                    case .heading(let title):
                        BCDEyebrow(text: title).padding(.leading, 4).padding(.top, 18).padding(.bottom, 8)
                    case .option(let entry, let first, let last):
                        BCDConfigRow(entry: entry, value: backend.values[entry.key], separator: !first) { backend.set(entry.key, $0) }
                            .background(BCDStyle.surface, in: UnevenRoundedRectangle(
                                topLeadingRadius: first ? 18 : 0, bottomLeadingRadius: last ? 18 : 0,
                                bottomTrailingRadius: last ? 18 : 0, topTrailingRadius: first ? 18 : 0, style: .continuous))
                    }
                }
            }
            if shown.isEmpty && unlisted.isEmpty {
                Text("Nothing matches \u{201C}\(query)\u{201D}.").font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft)
            }
            if !unlisted.isEmpty {
                BCDSettingsGroup(title: "Other lines in bcd.cfg",
                                 footer: "bcd.cfg has these, but no code reads them by name: options passed straight to the emulator as env.FEX_*, for one.") {
                    ForEach(unlisted, id: \.self) { key in
                        BCDConfigRow(entry: BCDConfigEntry(key: key, title: "", kind: .text, defaultValue: "", category: "",
                                                           note: "", choices: []),
                                     value: backend.values[key]) { backend.set(key, $0) }
                    }
                }
            }
            addEntry
        }
        // Rows below the screen are not built yet, so the whole order comes from here.
        .transformPreference(BCDSettingsOrderKey.self) { $0 = order(shown, unlisted) }
        .bcdKeyboardDone { typing = nil }
        .onAppear {
            backend.refresh()
            if !model.allQuery.isEmpty { query = model.allQuery }
        }
        .onChange(of: model.allQuery) { _, new in if !new.isEmpty { query = new } }
    }

    private func matches(_ entry: BCDConfigEntry) -> Bool {
        BCDSettingsText.matches(query, key: entry.key, title: entry.title, note: entry.note, category: entry.category)
    }

    private var unlistedKeys: [String] {
        backend.values.keys.filter { key in
            !backend.catalogKeys.contains(key) && BCDSettingsText.matches(query, key: key, title: "", note: "", category: "")
        }
        .sorted()
    }

    private func lines(_ shown: [BCDConfigEntry]) -> [Line] {
        var out: [Line] = []
        for category in backend.categories {
            let options = shown.filter { $0.category == category }
            guard !options.isEmpty else { continue }
            out.append(.heading(category))
            for (index, entry) in options.enumerated() {
                out.append(.option(entry, first: index == 0, last: index == options.count - 1))
            }
        }
        return out
    }

    private func order(_ shown: [BCDConfigEntry], _ unlisted: [String]) -> [String] {
        [BCDSettingsID.control(.all, "search")]
            + backend.categories.flatMap { category in shown.filter { $0.category == category }.map { BCDConfigRow.focusID($0.key) } }
            + unlisted.map(BCDConfigRow.focusID)
            + [BCDSettingsID.control(.all, "new-key"), BCDSettingsID.control(.all, "new-value"), BCDSettingsID.control(.all, "add")]
    }

    // MARK: Search

    private func searchField(count: Int) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass").foregroundStyle(BCDStyle.textSoft)
            TextField("Find an option", text: $query)
                .font(BCDStyle.body(16))
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .focused($typing, equals: .search)
                .submitLabel(.search)
            if !query.isEmpty {
                Text("\(count)").font(BCDStyle.label(13)).foregroundStyle(BCDStyle.textSoft).monospacedDigit()
                Button { query = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(BCDStyle.textSoft) }
                    .buttonStyle(.plain).accessibilityLabel("Clear the search")
            }
        }
        .padding(.horizontal, 14).frame(height: 46)
        .background(BCDStyle.surface, in: Capsule())
        .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
        .bcdSettingsControl(BCDSettingsID.control(.all, "search"), cornerRadius: 23) { typing = .search }
    }

    // MARK: A line of your own

    private var addEntry: some View {
        BCDSettingsGroup(title: "Add a line",
                         footer: "For an option the list does not have, such as env.FEX_TSOENABLED = 1. A key as this list shows it works too.") {
            field("new-key", "key, or env.NAME", text: $newKey, field: .newKey)
            field("new-value", "value", text: $newValue, field: .newValue)
            BCDSettingsActionRow(id: BCDSettingsID.control(.all, "add"), title: "Add to bcd.cfg", symbol: "plus",
                                 enabled: valid(newKey)) { add() }
        }
    }

    private func field(_ name: String, _ prompt: String, text: Binding<String>, field: Field) -> some View {
        TextField(prompt, text: text)
            .font(.system(size: 15, design: .monospaced))
            .textInputAutocapitalization(.never).autocorrectionDisabled()
            .focused($typing, equals: field)
            .padding(.horizontal, 14).frame(height: 48)
            .overlay(alignment: .top) { Rectangle().fill(BCDStyle.stroke.opacity(0.55)).frame(height: 1).padding(.leading, 14) }
            .bcdSettingsControl(BCDSettingsID.control(.all, name), cornerRadius: 12) { typing = field }
    }

    private func valid(_ key: String) -> Bool {
        let k = key.trimmingCharacters(in: .whitespaces)
        return !k.isEmpty && !k.contains("=") && !k.hasPrefix("#")
    }

    private func add() {
        guard valid(newKey) else { return }
        let known = Array(backend.catalogKeys) + Array(backend.values.keys)
        backend.set(BCDSettingsText.realKey(newKey, known: known), newValue.trimmingCharacters(in: .whitespaces))
        newKey = ""
        newValue = ""
        typing = nil
    }
}

/// One option: its title and what the code says about it, the key as shown, and its value.
struct BCDConfigRow: View {
    let entry: BCDConfigEntry
    let value: String?
    /// A hairline above the row (the lazy list draws its own; a card's group draws them for it).
    var separator = false
    let set: (String?) -> Void
    @State private var draft = ""
    @FocusState private var editing: Bool
    @Environment(\.bcdLayout) private var layout

    static func focusID(_ key: String) -> String { BCDSettingsID.control(.all, "key." + key) }

    private var title: String { entry.title.isEmpty ? BCDSettingsText.key(entry.key) : BCDSettingsText.scrub(entry.title) }

    var body: some View {
        switch entry.kind {
        case .toggle, .choice: menuRow
        case .number, .text: fieldRow
        }
    }

    private var token: String { value ?? "\u{0}" }
    private var reset: (() -> Void)? { value == nil ? nil : { set(nil) } }

    // MARK: Words

    private var words: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(BCDStyle.label(layout == .pad ? 16 : 14.5)).fixedSize(horizontal: false, vertical: true)
            if !entry.note.isEmpty {
                Text(BCDSettingsText.scrub(entry.note)).font(BCDStyle.body(layout == .pad ? 13.5 : 12)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 8) {
                if !entry.title.isEmpty { Text(BCDSettingsText.key(entry.key)).font(.system(size: 11, design: .monospaced)) }
                if !entry.defaultValue.isEmpty { Text("standard \(BCDSettingsText.scrub(entry.defaultValue))").font(.system(size: 11, design: .rounded)) }
                if value != nil { Text("set").font(.system(size: 11, weight: .bold, design: .rounded)).foregroundStyle(BCDStyle.lavender) }
            }
            .foregroundStyle(BCDStyle.textSoft)
        }
        .multilineTextAlignment(.leading)    // a Menu's label would centre it
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func frame<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        HStack(alignment: .center, spacing: 12) { content() }
            .padding(.horizontal, 14).padding(.vertical, 11)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay(alignment: .top) { if separator { BCDSettingsSeparator() } }
            .contentShape(Rectangle())
    }

    // MARK: A value from a list

    private var choices: [BCDSettingsChoice] {
        var list: [BCDSettingsChoice]
        if entry.kind == .toggle {
            let standard = entry.defaultValue == "1" ? "Standard (on)" : entry.defaultValue == "0" ? "Standard (off)" : "Standard"
            list = [BCDSettingsChoice(value: "", label: standard), BCDSettingsChoice(value: "1", label: "On"),
                    BCDSettingsChoice(value: "0", label: "Off")]
        } else {
            list = entry.choices.map { BCDSettingsChoice(value: $0.value, label: BCDSettingsText.scrub($0.label)) }
            if !list.contains(where: { $0.value.isEmpty }) { list.insert(BCDSettingsChoice(value: "", label: "Standard"), at: 0) }
        }
        if let value, !list.contains(where: { $0.value == value }) { list.append(BCDSettingsChoice(value: value, label: "Yours: \(value)")) }
        return list
    }

    private var current: String { value ?? "" }

    private var menuRow: some View {
        let list = choices
        let shown = list.first { $0.value == current }?.label ?? current
        return Menu {
            ForEach(list, id: \.self) { choice in
                Button { choose(choice.value) } label: {
                    if choice.value == current { Label(choice.label, systemImage: "checkmark") } else { Text(choice.label) }
                }
            }
        } label: {
            frame {
                words
                Text(shown).font(BCDStyle.label(14)).foregroundStyle(BCDStyle.lavender)
                    .multilineTextAlignment(.trailing).lineLimit(2)
                    .frame(maxWidth: layout == .phonePortrait ? 120 : 200, alignment: .trailing)
            }
        }
        .bcdSettingsControl(Self.focusID(entry.key), token: token, step: step, reset: reset, action: primary)
    }

    private func choose(_ chosen: String) { set(chosen.isEmpty ? nil : chosen) }

    private func step(_ delta: Int) {
        let list = choices
        guard !list.isEmpty else { return }
        let from = list.firstIndex { $0.value == current } ?? 0
        let to = min(max(from + delta, 0), list.count - 1)
        if to != from { choose(list[to].value) }
    }

    // MARK: A number or text

    private var fieldRow: some View {
        frame {
            words
            TextField(entry.defaultValue.isEmpty ? "Standard" : BCDSettingsText.scrub(entry.defaultValue), text: $draft)
                .font(.system(size: 14, design: .monospaced)).foregroundStyle(BCDStyle.lavender)
                .multilineTextAlignment(.trailing)
                .keyboardType(entry.kind == .number ? .numbersAndPunctuation : .default)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .focused($editing)
                .onSubmit { commit() }
                .frame(maxWidth: layout == .phonePortrait ? 110 : 170)
        }
        .onAppear { draft = value ?? "" }
        .onChange(of: value) { _, new in if !editing { draft = new ?? "" } }
        .onChange(of: editing) { _, on in if !on { commit() } }
        .bcdSettingsControl(Self.focusID(entry.key), token: token, reset: reset, action: primary)
    }

    private func commit() {
        let text = draft.trimmingCharacters(in: .whitespaces)
        if text != (value ?? "") { set(text.isEmpty ? nil : text) }
    }

    /// A: the next choice, or typing into the field.
    private func primary() {
        switch entry.kind {
        case .toggle, .choice:
            let list = choices
            guard !list.isEmpty else { return }
            let from = list.firstIndex { $0.value == current } ?? -1
            choose(list[(from + 1) % list.count].value)
        case .number, .text:
            editing = true
        }
    }
}
