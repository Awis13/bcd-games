// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  AutoTuneViews.swift
//  Madeira
//
//  madeira-bcd: auto-tune's views (docs/AUTO-TUNE.md): the switch in
//  Settings and the section on a game's details page. The page shows its section
//  only while Settings › Auto-tune is on for this run (AutoTune.enabled).
//

import SwiftUI

/// Auto-tune's result after a tuned session, after the exit report and any restart notice.
/// A modifier of its own: ContentView's chain of alerts is at the type checker's limit.
/// The offer is only ever set by AutoTune's analysis, which runs only while it is on.
struct AutoTuneAlert: ViewModifier {
    @ObservedObject private var offers = AutoTuneOffers.shared
    @ObservedObject private var library = LibraryModel.shared

    func body(content: Content) -> some View {
        content.alert(offers.offer?.headline ?? "Auto-tune", isPresented: presented, presenting: offers.offer) { offer in
            if offer.ask {
                Button("It froze") { offers.offer = nil; AutoTune.answer("froze") }
                Button("It closed by itself") { offers.offer = nil; AutoTune.answer("closed-by-itself") }
                Button("I closed BCD", role: .cancel) { offers.offer = nil; AutoTune.answer("i-closed") }
            } else {
                if offer.state.candidate != nil {
                    Button("Close BCD") {
                        LogStore.shared.log("[autotune] closed by the user for the next attempt")
                        exit(0)
                    }
                }
                // madeira-bcd FORK-ONLY: a search that ended without a fix sends its log in one tap.
                if ["stuck", "gave-up", "known"].contains(offer.state.status),
                   let name = offer.state.attempts.last?.log, let log = LogUpload.log(named: name) {
                    Button("Send Log") {
                        offers.offer = nil
                        LogUpload.send(log, game: offer.title)
                    }
                }
                Button("Stop for this game", role: .destructive) {
                    AutoTune.setOn(offer.windowsPath, false)
                    offers.offer = nil
                }
                Button("OK", role: .cancel) { offers.offer = nil }
            }
        } message: { offer in
            Text(offer.message)
        }
    }

    private var presented: Binding<Bool> {
        Binding(get: { AutoTune.enabled && offers.offer != nil && library.error == nil && library.restartNotice == nil },
                set: { if !$0 { offers.offer = nil } })
    }
}
