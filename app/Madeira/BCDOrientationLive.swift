// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import UIKit

// The engine side of the screen orientation lock (BCDOrientation.swift): which choice a library session takes, and
// the calls that turn it on when the session starts and off when it ends (LibraryModel.begin and finish). Left
// out of the preview build (tools/ui-preview.py, ENGINE_BOUND), which names no engine type.
//
// Off, the default, no call below reaches UIKit: a session with no choice of its own and no `orientation-lock` in
// bcd.cfg leaves the lock as it is and logs nothing.

enum BCDOrientationLive {
    /// bcd.cfg's key for the choice that holds for every game.
    static let key = "orientation-lock"

    /// What Settings says for every game.
    static var global: String? { MadeiraConfig.get("orientation-lock") }

    /// The choice a session of `entry` takes: the game's own, else Settings'.
    static func choice(for entry: LibraryEntry) -> BCDOrientationChoice {
        BCDOrientationRules.effective(global: global, game: entry.orientationLock)
    }

    /// A library session starts: the lock goes on when the choice asks for one.
    static func sessionBegan(_ entry: LibraryEntry) {
        let chosen = choice(for: entry)
        DispatchQueue.main.async {
            let lock = BCDOrientationLock.shared
            guard chosen.locked || lock.choice.locked else { return }
            lock.onLog = { LogStore.shared.log($0) }
            lock.apply(chosen)
        }
    }

    /// The session ended: the interface follows the device again.
    static func sessionEnded() {
        DispatchQueue.main.async {
            let lock = BCDOrientationLock.shared
            guard lock.choice.locked else { return }
            lock.onLog = { LogStore.shared.log($0) }
            lock.apply(.free)
        }
    }
}
