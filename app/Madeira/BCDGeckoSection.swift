// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

/// Settings › HTML engine for installers: Wine Gecko (BCDGecko.swift), downloaded once with BCD's own downloader so an
/// installer that shows a web page (the EA app's) does not have Wine download it inside the emulator, slowly. Built like
/// the Visual C++ runtime's page: what this device has, then Download, Cancel or Remove.
struct BCDGeckoSettings<Backend: BCDGeckoBackend>: View {
    @ObservedObject var backend: Backend
    @EnvironmentObject private var model: BCDSettingsModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            BCDSettingsGroup(title: "Wine Gecko \(BCDGeckoPin.version)",
                             footer: "Some installers show a web page, and Wine needs its HTML engine, Wine Gecko, for it. Without this download Wine fetches it inside the emulator, which takes many minutes. BCD downloads it straight from WineHQ, \(BCDFormat.bytes(BCDGeckoPin.totalBytes)) in two files for 32-bit and 64-bit programs, checks both against the checksums Wine pins, and keeps them outside the Wine prefix. Nothing changes for games that never need it.") {
                BCDSettingsInfoRow(title: "On this device", value: stateText, valueColor: stateColor)
                switch backend.state {
                case .downloading(let received, let total, let arch):
                    VStack(alignment: .leading, spacing: 8) {
                        BCDProgressBar(fraction: total > 0 ? min(1, Double(received) / Double(total)) : 0)
                        Text("\(BCDFormat.bytes(received)) of \(BCDFormat.bytes(total)) · \(arch == "x86" ? "32-bit" : "64-bit") files")
                            .font(BCDStyle.body(13.5)).foregroundStyle(BCDStyle.textSoft).monospacedDigit()
                    }
                    .padding(.horizontal, 14).padding(.vertical, 12)
                    BCDSettingsActionRow(id: BCDSettingsID.control(.gecko, "cancel"), title: "Cancel the download", symbol: "xmark") {
                        backend.cancel()
                    }
                case .missing:
                    BCDSettingsActionRow(id: BCDSettingsID.control(.gecko, "download"),
                                         title: backend.lastError == nil ? "Download Wine Gecko" : "Try again",
                                         detail: BCDFormat.bytes(BCDGeckoPin.totalBytes),
                                         symbol: "arrow.down.circle.fill") { backend.install() }
                case .installed:
                    BCDSettingsActionRow(id: BCDSettingsID.control(.gecko, "remove"), title: "Remove the downloaded copy",
                                         symbol: "trash", destructive: true) {
                        model.ask(BCDSettingsConfirm(title: "Remove Wine Gecko?",
                                                     message: "Installers that show a web page will ask Wine to download it again, which is slow.",
                                                     choices: [.init(title: "Remove", destructive: true) { backend.remove() }]))
                    }
                }
                if let error = backend.lastError {
                    Text(error).font(BCDStyle.body(13.5)).foregroundStyle(BCDStyle.alert)
                        .padding(.horizontal, 14).padding(.vertical, 11)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .onAppear { backend.refresh() }
    }

    private var stateText: String {
        switch backend.state {
        case .missing: return "Not downloaded"
        case .downloading: return "Downloading"
        case .installed: return "Downloaded"
        }
    }

    private var stateColor: Color {
        switch backend.state {
        case .missing: return BCDStyle.textSoft
        case .downloading: return BCDStyle.lavender
        case .installed: return BCDStyle.mint
        }
    }
}
