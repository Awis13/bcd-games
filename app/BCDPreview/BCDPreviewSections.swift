// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

/// One Settings section full screen on its sample backend: -BCDTab settings-accounts,
/// settings-jit or settings-dock, with a sample variant after one more dash
/// (settings-jit-ios18; see BCDPreviewAccounts/JIT/Dock.swift). The variant "hosted" shows the
/// default sample the way Settings hosts a section: beside a list (on a phone on its side and
/// an iPad), inside the host's own scrolling page and title, the section neither scrolling nor
/// titled itself.
struct BCDPreviewSettingsSection: View {
    private let section: String
    private let variant: String
    @StateObject private var accounts: BCDPreviewAccountsBackend
    @StateObject private var jit: BCDPreviewJITBackend
    @StateObject private var dock: BCDPreviewDockBackend
    @Environment(\.horizontalSizeClass) private var horizontal
    @Environment(\.verticalSizeClass) private var vertical

    init?(tab: String) {
        let parts = tab.split(separator: "-", maxSplits: 2).map(String.init)
        guard parts.count >= 2, parts[0] == "settings", ["accounts", "jit", "dock"].contains(parts[1]) else { return nil }
        let variant = parts.count > 2 ? parts[2] : ""
        section = parts[1]
        self.variant = variant
        _accounts = StateObject(wrappedValue: BCDPreviewAccountsBackend(variant: variant))
        _jit = StateObject(wrappedValue: BCDPreviewJITBackend(variant: variant))
        _dock = StateObject(wrappedValue: BCDPreviewDockBackend(variant: variant))
    }

    /// The preview app's screen: this section when -BCDTab names one, else `root`.
    @ViewBuilder static func over<Root: View>(_ root: Root, focus: BCDFocus) -> some View {
        if let section = BCDPreviewSettingsSection(tab: UserDefaults.standard.string(forKey: "BCDTab") ?? "") {
            section.environmentObject(focus)
        } else {
            root
        }
    }

    private var layout: BCDLayout { BCDLayout.of(horizontal: horizontal, vertical: vertical) }

    var body: some View {
        Group {
            if variant == "hosted" { hosted } else { content(scrolls: true) }
        }
        .background { BCDBackdrop() }
        .coordinateSpace(name: BCDFocus.space)
        .environment(\.bcdLayout, layout)
        .foregroundStyle(BCDStyle.text)
        .preferredColorScheme(.dark)
    }

    @ViewBuilder private func content(scrolls: Bool) -> some View {
        switch section {
        case "accounts":
            BCDAccountsSection(backend: accounts, showsTitle: scrolls, scrolls: scrolls, startOn: BCDPreviewAccountsBackend.page(variant))
        case "jit":
            BCDJITSection(backend: jit, showsTitle: scrolls, scrolls: scrolls)
        default:
            BCDDockSection(backend: dock, showsTitle: scrolls, scrolls: scrolls,
                           signIn: { print("[preview] Dock: sign in to Steam") })
        }
    }

    /// Roughly what Settings' detail pane does with a section: a list beside it, its own title,
    /// its own scrolling.
    private var hosted: some View {
        HStack(spacing: 0) {
            if layout != .phonePortrait {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(["Accounts", "JIT", "BCD Dock", "Memory", "Storage"], id: \.self) { name in
                        Text(name).font(BCDStyle.label(15)).padding(.horizontal, 12).padding(.vertical, 8)
                    }
                    Spacer()
                }
                .frame(width: layout == .pad ? 300 : 230, alignment: .leading)
                .background(BCDStyle.surface.opacity(0.6))
            }
            ScrollView(.vertical, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 16) {
                    Text(section == "jit" ? "JIT" : section == "dock" ? "BCD Dock" : "Accounts")
                        .font(BCDStyle.title(layout == .pad ? 32 : 24))
                    content(scrolls: false)
                }
                .padding(.horizontal, layout == .pad ? 28 : 18).padding(.top, 10).padding(.bottom, 28)
                .frame(maxWidth: layout == .pad ? 780 : .infinity, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}
