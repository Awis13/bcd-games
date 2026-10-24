// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

/// The card the Library shows under a launcher's chip (Battle.net, EA app; BCDLaunchers.swift): Install when the
/// launcher is not on the device, Open when it is, the installer's download in between. Plain text and plain
/// symbols only: no company logo or cover art. The words are short; BCD never asks for a login here, the
/// launcher's own window does.
struct BCDLauncherPanel: View {
    let spec: BCDLauncherSpec
    @ObservedObject var model: BCDLauncherModel = .shared
    @EnvironmentObject private var store: BCDStore

    /// The controller ids of this card start with this.
    static let prefix = "library.launcher."

    private var status: BCDLauncherStatus { model.status(spec) }
    /// The session is on its way: handed to the engine, or waiting for Play to turn JIT on (the store names the launcher then).
    private var starting: Bool { status.handingOver || turningOnJIT }
    private var turningOnJIT: Bool { store.startingJIT == BCDLauncherSpec.gameID(spec.id) }

    var body: some View {
        BCDSectionKit.Card(eyebrow: spec.label, symbol: spec.symbol, accent: true) {
            VStack(alignment: .leading, spacing: 14) {
                state
                if let message = status.message {
                    BCDSectionKit.Note(text: message, symbol: "exclamationmark.triangle.fill", tone: .bad)
                }
                buttons
                notes
            }
        }
        .frame(maxWidth: 620, alignment: .leading)
        .onAppear { model.refresh() }
    }

    // MARK: What the launcher is doing

    @ViewBuilder private var state: some View {
        if let download = status.download {
            BCDSectionKit.Headline(text: "Downloading \(download.what)", symbol: "arrow.down.circle", tone: .info,
                                   detail: download.what == "the installer"
                                       ? "From \(spec.hosts.sorted().first ?? "\(spec.company)'s server"), over HTTPS."
                                       : "The installer shows a web page, which Wine needs this for. BCD downloads it once, from \(BCDGeckoPin.hosts.sorted().first ?? "WineHQ").")
            VStack(alignment: .leading, spacing: 6) {
                BCDProgressBar(fraction: download.fraction)
                Text(Self.bytesLine(download)).bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft)
            }
        } else if starting {
            BCDSectionKit.Headline(text: turningOnJIT ? "Turning JIT on…" : "Starting…", symbol: "play.circle", tone: .info,
                                   detail: turningOnJIT ? "It starts as soon as JIT is on." : nil)
        } else if status.installed {
            BCDSectionKit.Headline(text: "\(spec.namedFirst) is installed", symbol: "checkmark.circle.fill", tone: .good,
                                   detail: "Open it, then sign in and install your games in its own window.")
        } else {
            BCDSectionKit.Headline(text: "\(spec.namedFirst) isn't installed yet", symbol: "arrow.down.circle", tone: .info,
                                   detail: "BCD downloads \(spec.companyPossessive) installer (\(spec.sizeHint)) and runs it. You sign in afterwards, in \(spec.named)'s own window.")
        }
    }

    /// "1.2 MB of 4.7 MB", "1.2 MB", or "Connecting…" before the first byte.
    static func bytesLine(_ download: BCDLauncherStatus.Progress) -> String {
        if download.received <= 0 && download.total <= 0 { return "Connecting…" }
        if download.total > 0 { return "\(BCDFormat.bytes(download.received)) of \(BCDFormat.bytes(download.total))" }
        return BCDFormat.bytes(download.received)
    }

    // MARK: Buttons

    @ViewBuilder private var buttons: some View {
        if status.download != nil {
            BCDSectionKit.ActionButton(title: "Cancel", symbol: "xmark", kind: .outline, fill: true,
                                       id: Self.prefix + "cancel") { model.cancel(spec) }
        } else if status.installed {
            BCDSectionKit.ActionButton(title: "Open \(spec.title)", symbol: "play.fill", kind: .primary, fill: true,
                                       id: Self.prefix + "open", enabled: !starting) { model.open(spec) }
            BCDSectionKit.ActionButton(title: "Install again", symbol: "arrow.down.circle", kind: .quiet,
                                       id: Self.prefix + "reinstall", enabled: !starting) { model.install(spec) }
        } else {
            BCDSectionKit.ActionButton(title: "Install \(spec.title)", symbol: "arrow.down.circle", kind: .primary, fill: true,
                                       id: Self.prefix + "install", enabled: !starting) { model.install(spec) }
        }
    }

    // MARK: Notes

    @ViewBuilder private var notes: some View {
        VStack(alignment: .leading, spacing: 8) {
            if spec.experimental {
                BCDSectionKit.Note(text: "Experimental: \(spec.named) may not start, sign in or install games on every device.",
                                   symbol: "exclamationmark.triangle", tone: .warn)
            }
            BCDSectionKit.Note(text: "BCD never sees your \(spec.title) login: you sign in in its own window.", symbol: "lock")
            BCDSectionKit.Note(text: status.installed
                               ? "BCD starts one program each time it opens. To open \(spec.named) after another session, swipe BCD away in the app switcher and open it again."
                               : "The installer opens \(spec.named) itself when it finishes. Leave from BCD's menu when you are done; to open it later, swipe BCD away in the app switcher and open it again.",
                               symbol: "arrow.counterclockwise")
            BCDSectionKit.Note(text: "Games you install from \(spec.named) stay inside its window; BCD doesn't list them here.", symbol: "info.circle")
        }
    }
}
