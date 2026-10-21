// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// The once-a-day "a newer BCD is out" alert's state (BCDUpdateCheck.swift has the rules and the request).
// Engine side (it reads bcd.cfg and writes the log), so not a BCD*.swift screen file: the preview build leaves it out.

/// The notice the library shows (BCDLiveFrontEnd). One check per app run, and only when the day's is due.
@MainActor final class BCDUpdateNotice: ObservableObject {
    static let shared = BCDUpdateNotice()

    /// The alert's text while it waits to be seen; OK clears it.
    @Published var message: String?
    private var started = false

    /// When the library first appears: a few seconds later, at most once a day, ask the source.
    func start() {
        guard !started else { return }
        started = true
        guard SteamSignIn.flag("MADEIRA_UPDATE_CHECK", default: true), let current = BCDUpdateCheck.current() else { return }
        let today = BCDUpdateCheck.day(Date())
        guard BCDUpdateCheck.due(lastDay: UserDefaults.standard.string(forKey: BCDUpdateCheck.lastDayKey), today: today) else { return }
        Task.detached(priority: .utility) {
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            guard let data = await BCDUpdateCheck.fetch(), let latest = BCDUpdateCheck.latest(from: data) else {
                LogStore.shared.log("[update-check] no answer from the source; asked again at the next start")
                return
            }
            let text = BCDUpdateCheck.notice(latest: latest, current: current)
            await MainActor.run {
                UserDefaults.standard.set(today, forKey: BCDUpdateCheck.lastDayKey)
                LogStore.shared.log("[update-check] latest=\(latest.build) (\(latest.version)) this=\(current.build) told=\(text == nil ? 0 : 1)")
                if let text { BCDUpdateNotice.shared.message = text }
            }
        }
    }
}
