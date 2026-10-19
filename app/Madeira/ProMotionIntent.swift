import SwiftUI
import UIKit
import QuartzCore
import os

/// Frame-rate intent for the ProMotion panel. CAMetalLayer presents alone
/// don't express one: iOS parks the display at 60Hz and only promotes on
/// touch (observed 2026-07-05: MAX mode ran 60 except ~119 bursts while
/// touching). An active CADisplayLink with a preferredFrameRateRange is the
/// documented way for present-driven Metal apps to hold the panel higher.
/// The tick itself does nothing.
///
/// Default (as on main): armed only outside the 60 and 30 caps, i.e. for
/// MAX and RAW, where the game is meant to run above 60, and for the 40 cap,
/// whose 25ms spacing needs the 120Hz grid (at 60Hz it would land on 33.3ms).
///
/// Opt-in `env.MADEIRA_PROMOTE = 1` (Settings > Display > Hold the display
/// at its maximum rate) also arms it in the 60 cap. The panel rate is the
/// grid a present snaps to: the 60 cap is a minimum spacing between
/// presentations (afterMinimumDuration), and a drawable becomes visible at
/// a vblank, never between two. With the panel parked at 60Hz a frame
/// ready at 17-25ms waits for the 33.3ms vblank, so a 20ms pipeline reports
/// 30 FPS; at 120Hz the same frame lands at 25ms (40 FPS). The cap itself is
/// unchanged, but the panel runs at 120Hz for the whole session, which
/// costs power, so it is not the default. The 30 cap keeps a 60Hz intent
/// (33.3ms is an exact multiple of 16.67ms).
final class ProMotionIntent {
    static let shared = ProMotionIntent()
    private var link: CADisplayLink?
    private var activeMax: Float = 0

    /// Honest report of what the panel can do, for the native `[frame]` line.
    static var panelMaxFPS: Int { UIScreen.main.maximumFramesPerSecond }

    /// The opt-in above; read when a session's pacing is applied.
    static var holdMaximum: Bool { MadeiraConfig.flag("MADEIRA_PROMOTE", fallback: false) }

    /// Whether DXMT has the 30 FPS cap (vsync mode 3) and the display-rate
    /// hook. Both arrive with willfaust/dxmt#1; without it mode 3 would run
    /// uncapped, so the 30 choice is offered only when this is true.
    static var has30Cap: Bool = {
        madeira_set_display_max_fps(Int32(panelMaxFPS), 0)
        return madeira_dxmt_has_display_pacing() != 0
    }()

    /// Whether the 40 FPS cap (vsync mode 4) is offered: DXMT must have it
    /// (madeira_dxmt_has_40_cap; without it mode 4 would present uncapped) and
    /// the panel must reach 120 Hz, because 25 ms is three refreshes at 120 Hz
    /// but rounds to 33 ms at 60 Hz. maxHz(for:) holds the panel at its maximum
    /// while it runs.
    static var has40Cap: Bool { madeira_dxmt_has_40_cap() != 0 && panelMaxFPS >= 120 }

    /// The pacing mode a saved FPS limit runs as: 30 and 40 run as 60 when
    /// they are not offered.
    static func supportedMode(_ mode: Int) -> Int32 {
        if mode == 3 && !has30Cap { return 1 }
        if mode == 4 && !has40Cap { return 1 }
        return Int32(mode)
    }

    /// `maxHz` 0 means "tear it down".
    func setActive(_ active: Bool, maxHz: Int = ProMotionIntent.panelMaxFPS) {
        let want = Float(max(maxHz, 0))
        if active && want > 0 {
            if link != nil && activeMax == want { return }
            link?.invalidate()
            let l = CADisplayLink(target: self, selector: #selector(tick))
            // `minimum` must not exceed the panel's own maximum or CoreAnimation
            // clamps the whole range away (a 60Hz device asked for min 60 /
            // max 120 gets nothing useful).
            let lo = Float(min(60, maxHz))
            l.preferredFrameRateRange = CAFrameRateRange(minimum: lo, maximum: want, preferred: want)
            l.add(to: .main, forMode: .common)
            link = l
            activeMax = want
            fputs("[promote] display link armed preferred=\(Int(want))Hz panel_max=\(ProMotionIntent.panelMaxFPS)Hz\n", stderr)
        } else {
            if link != nil { fputs("[promote] display link released\n", stderr) }
            link?.invalidate()
            link = nil
            activeMax = 0
        }
    }

    /// The intent that belongs to a given pacing mode (0 = none). See the type comment.
    static func maxHz(for mode: Int32) -> Int {
        if mode == 3 { return holdMaximum ? min(60, panelMaxFPS) : 0 }   // 30 cap
        if mode == 1 { return holdMaximum ? panelMaxFPS : 0 }            // 60 cap
        return panelMaxFPS                                                // MAX, RAW, 40 cap
    }

    /// Arms or releases the link for `mode` and publishes the rates to DXMT.
    static func apply(mode: Int32) {
        let hz = maxHz(for: mode)
        shared.setActive(hz > 0, maxHz: hz)
        madeira_set_display_max_fps(Int32(panelMaxFPS), Int32(hz))
    }

    @objc private func tick(_ sender: CADisplayLink) {}
}

/// ml1137: process-wide fence-mode display state for the overlay pill.
enum FPSOverlayFenceMode {
    static var current: Int = Int(MadeiraConfig.gameValue("fence-chain") ?? MadeiraConfig.get("fence-chain") ?? "1") ?? 1
}
