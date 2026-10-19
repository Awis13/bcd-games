//
//  SessionUI.swift
//  Madeira
//
//  The game session as a player sees it, modelled on upstream's new UI (shown
//  in the Madeira Discord, not yet pushed): a launch screen with the game's
//  art until the first frame arrives, the game alone on screen, and one
//  Session panel for everything that used to be spread over the developer
//  view's button rows. Steam sign-in and downloads are deliberately left out.
//
//  The developer view is still there, under Settings > Developer tools; a
//  game started from the library no longer shows it.
//

import SwiftUI
import UIKit

// MARK: - Session state

/// What is running, and the player-facing switches that outlive one panel.
final class GameSession: ObservableObject {
    static let shared = GameSession()

    /// Title of the game this launch started (one Windows session per launch).
    @Published var title = ""

    /// The FPS readout beside the game. Persisted: a preference, not a state.
    @Published var showPerformance: Bool =
        UserDefaults.standard.object(forKey: GameSession.performanceKey) as? Bool ?? true {
        didSet { UserDefaults.standard.set(showPerformance, forKey: Self.performanceKey) }
    }

    private static let performanceKey = "madeira.session.performanceOverlay"
}

/// How the guest display sits on the screen. MetalBackedView.gameRect()
/// reads this, and touch mapping uses the same rect, so input follows.
enum DisplayFit: String, CaseIterable, Identifiable {
    case fit
    case stretch

    var id: String { rawValue }

    var label: String {
        switch self {
        case .fit: return "Fit"
        case .stretch: return "Stretch"
        }
    }

    private static let key = "madeira.display.fit"

    static var current: DisplayFit {
        get { DisplayFit(rawValue: UserDefaults.standard.string(forKey: key) ?? "") ?? .fit }
        set {
            UserDefaults.standard.set(newValue.rawValue, forKey: key)
            MetalBackedView.relayoutLive()
        }
    }
}

/// DXMT's pacing modes (g_madeira_vsync_mode). 1/0/2 are upstream's (the FPS
/// pill cycles through them); 3 and 4 are added in CI by
/// tools/patch-dxmt-frame-limits.py. Listed in the order the panel shows them.
enum FrameLimit: Int32, CaseIterable, Identifiable {
    case locked30 = 3
    case locked40 = 4
    case locked60 = 1
    case display = 0
    case unlimited = 2

    var id: Int32 { rawValue }

    var label: String {
        switch self {
        case .locked30: return "30 FPS"
        case .locked40: return "40 FPS"
        case .locked60: return "60 FPS"
        case .display: return "\(UIScreen.main.maximumFramesPerSecond) FPS"
        case .unlimited: return "Unlimited"
        }
    }

    static var current: FrameLimit { FrameLimit(rawValue: Int32(madeira_get_vsync_locked())) ?? .locked60 }

    /// 30 and 60 divide a 60 Hz panel evenly. 40 does not: 25 ms is three
    /// refreshes at 120 Hz but rounds to 33 ms at 60 Hz, so it needs the
    /// ProMotion rate held, as do the display-max and raw modes.
    static func wantsHighRefresh(_ mode: Int32) -> Bool {
        mode != FrameLimit.locked60.rawValue && mode != FrameLimit.locked30.rawValue
    }

    static func apply(_ limit: FrameLimit) {
        madeira_set_vsync_locked(Int32(limit.rawValue))
        ProMotionIntent.apply(mode: limit.rawValue)
    }
}
