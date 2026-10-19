// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// The controller hub's way into the engine (BCDControllerHub.swift). Engine-bound: the preview
// build leaves this file out (tools/ui-preview.py, ENGINE_BOUND). MadeiraApp starts the hub with
// it at launch, beside GamepadInput.
extension BCDControllerOutlet {
    static let live = BCDControllerOutlet(
        send: { output in
            switch output {
            case .key(let vk, let down): winios_post_key(vk, down ? 1 : 0)
            case .mouse(let flags): winios_pointer(0, 0, flags, 0)
            }
        },
        // Windows takes the controller's keys while it runs (a game, the Wine desktop) and no BCD
        // menu holds the controller (LibraryController; GamepadInput gives the game a pad at rest
        // then): nothing waits in the input queue for the next game, and a press in BCD's menu
        // stays there. When it stops taking them, what the mapping held goes up.
        accepting: { wine_process_is_running() != 0 && !LibraryController.shared.ownsInput },
        log: { line, success in LogStore.shared.log(line, level: success ? .success : .info) },
        xinputSetting: {
            // Paired controllers reach games as Xbox controllers (XInput) unless this is 0. Read
            // when BCD starts; with XInput off, the controller hub's keys and mouse are on by default.
            (MadeiraConfig.get("env.MADEIRA_XINPUT"), ProcessInfo.processInfo.environment["MADEIRA_XINPUT"])
        })
}
