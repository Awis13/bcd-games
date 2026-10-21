// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Engine side (it reads the entitlements and writes the log), so not a BCD*.swift screen file: the preview
// build leaves it out. An install without the increased memory limit (owner, 2026-10-21: say so, never block). iOS then closes large
// games early, while small games can still run: of 89 logs from such installs, Doom 3, GTA V Legacy, Moonlighter and
// Tomb Raider: Underworld got in game, so nothing is refused. BCD says it once per build in a small box with OK over
// the library, and again where a log is sent, because such a log names the setup, not a bug BCD can fix.
// The entitlement read agrees with the provisioning profile line (`profile-increased-memory=`) on all 427 logged
// installs that have both (89 without, 338 with), so it is the signal.

enum MemoryRights {
    /// Read once per run; the entitlements do not change while BCD runs.
    static let lacking: Bool = !EntitlementStatus.check().increasedMemory

    /// Where a log is sent (Settings › Logs, a game's page).
    static let sendLogAdvice = "This install lacks the increased memory limit, the usual cause of crashes on it. "
        + "Add it with GetMoreRam, or sign BCD with a paid developer account, before sending a log."

    /// The box at start.
    static let startNotice = "This install lacks the increased memory limit, so iOS closes large games early; "
        + "small games can still run. Add it with GetMoreRam (with SideStore or LiveContainer), or sign BCD with "
        + "a paid developer account. BCD's README explains how."

    /// The defaults key holding the build the box was last shown for.
    static let shownBuildKey = "bcd.memoryNotice.shownBuild"

    /// The box is due when the limit is lacking and it has not been shown for this build.
    static func due(lacking: Bool, shownBuild: String?, build: String?) -> Bool {
        lacking && build != nil && shownBuild != build
    }
}

/// The start box's state (BCDLiveFrontEnd shows it; OK clears it).
@MainActor final class MemoryRightsNotice: ObservableObject {
    static let shared = MemoryRightsNotice()

    @Published var message: String?
    private var started = false

    /// When the library first appears, once per run.
    func start() {
        guard !started else { return }
        started = true
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String
        guard MemoryRights.due(lacking: MemoryRights.lacking,
                                  shownBuild: UserDefaults.standard.string(forKey: MemoryRights.shownBuildKey),
                                  build: build) else { return }
        UserDefaults.standard.set(build, forKey: MemoryRights.shownBuildKey)
        LogStore.shared.log("[memory-rights] increased-memory=0: told once for build \(build ?? "?")")
        message = MemoryRights.startNotice
    }
}
