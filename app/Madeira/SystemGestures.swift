// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  SystemGestures.swift
//
//  Two switches for iOS's own gestures during play, both off by default (2026-10-18,
//  docs/research/ios18-2026-10-18/gameplay.md sections 2.4 and 2.5):
//
//  - env.MADEIRA_PAD_SYSTEM_GESTURES = 1: a controller's Home button and its Create/Share
//    (Options) button reach the game at once. iOS binds them to system gestures (the Games
//    overlay, screenshots and recordings) and otherwise forwards them late, after it has ruled
//    out a long press. For the session their preferredSystemGestureState is .alwaysReceive;
//    the end of the session sets it back to .enabled.
//  - env.MADEIRA_DEFER_EDGES = 1: while the on-screen controls are shown, a swipe from a screen
//    edge goes to the game first, and only a second swipe opens Notification Center or Control
//    Center or goes Home (defersSystemGestures), so a thumb on a corner button stays in the game.
//
//  Neither set: no controller element and no view is touched.

@preconcurrency import GameController
import SwiftUI

enum SystemGestures {
    /// env.MADEIRA_PAD_SYSTEM_GESTURES: the game's own file, else madeira.cfg, else the environment.
    static var padButtonsOptIn: Bool {
        (MadeiraConfig.gameValue("env.MADEIRA_PAD_SYSTEM_GESTURES")
            ?? MadeiraConfig.get("env.MADEIRA_PAD_SYSTEM_GESTURES")   // 1: the Home and Create/Share buttons of a controller go to the game at once instead of to iOS's gestures; unset/0: as before
            ?? ProcessInfo.processInfo.environment["MADEIRA_PAD_SYSTEM_GESTURES"]) == "1"
    }

    /// env.MADEIRA_DEFER_EDGES, read the same way, once per app run (the game views read it).
    static let deferEdgesOptIn: Bool =
        (MadeiraConfig.gameValue("env.MADEIRA_DEFER_EDGES")
            ?? MadeiraConfig.get("env.MADEIRA_DEFER_EDGES")   // 1: with the on-screen controls shown, an edge swipe goes to the game first and a second one to iOS; unset/0: as before
            ?? ProcessInfo.processInfo.environment["MADEIRA_DEFER_EDGES"]) == "1"

    /// The buttons iOS binds to system gestures that the game reads (XInput Guide and Back).
    static func gestureButtons(_ pad: GCExtendedGamepad) -> [GCControllerButtonInput] {
        [pad.buttonHome, pad.buttonOptions].compactMap { $0 }
    }

    /// This session takes the buttons (set at its start, with the switch). Main thread.
    nonisolated(unsafe) private(set) static var taken = false

    /// The session's start (GamepadInput.beginPadSession): with the switch, the buttons of
    /// every paired controller go to the game; GamepadInput applies it to later ones too.
    /// Main thread, as everything below.
    static func beginSession(enabled: Bool) {
        let take = enabled && padButtonsOptIn
        if taken && !take { endSession() }
        guard take else { return }
        taken = true
        apply(GCController.controllers())
        LogStore.shared.log("[pad-gestures] Home and Create/Share go to the game for this session "
                            + "(env.MADEIRA_PAD_SYSTEM_GESTURES = 1)")
    }

    /// A controller that joined during the session.
    static func apply(_ controllers: [GCController]) {
        guard taken else { return }
        for c in controllers {
            guard let pad = c.extendedGamepad else { continue }
            for button in gestureButtons(pad) { button.preferredSystemGestureState = .alwaysReceive }
        }
    }

    /// The session's end (LibraryModel.finish): iOS gets its gestures back.
    static func endSession() {
        guard taken else { return }
        taken = false
        for c in GCController.controllers() {
            guard let pad = c.extendedGamepad else { continue }
            for button in gestureButtons(pad) { button.preferredSystemGestureState = .enabled }
        }
        LogStore.shared.log("[pad-gestures] Home and Create/Share back to iOS")
    }
}

/// env.MADEIRA_DEFER_EDGES on a game view. Without the switch the view is returned as it
/// is, and nothing observes the on-screen controls.
struct DeferSystemEdges: ViewModifier {
    /// The view shows the game (landscape in the developer interface).
    var active: Bool

    @ViewBuilder
    func body(content: Content) -> some View {
        if SystemGestures.deferEdgesOptIn && active {
            content.modifier(DeferSystemEdgesWhileTouch())
        } else {
            content
        }
    }
}

/// With the switch: edges are deferred while the on-screen controls are shown.
private struct DeferSystemEdgesWhileTouch: ViewModifier {
    @ObservedObject private var controls = TouchControlsModel.shared

    func body(content: Content) -> some View {
        content.defersSystemGestures(on: controls.visible ? .all : [])
    }
}
