// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

/// The controller screen on sample data (-BCDTab controllers): two controllers, the tester in
/// the middle of a press, the keys switched on while XInput is on (so the warning shows).
/// -BCDEmpty YES: no controller, the switches as a fresh install has them.
extension BCDControllerHub {
    static func sample(empty: Bool) -> BCDControllerHub {
        let defaults = UserDefaults(suiteName: "bcd.preview.controllers") ?? .standard
        defaults.removeObject(forKey: mappingKey)
        let hub = BCDControllerHub(outlet: .inert, defaults: defaults)
        if empty {
            hub.preview(pads: [], reading: BCDPadReading(), xinput: true, mapping: false)
        } else {
            var reading = BCDPadReading(leftX: 0.62, leftY: 0.48, rightX: -0.08, rightY: 0.03,
                                        leftTrigger: 0, rightTrigger: 0.64)
            reading.held = [.leftStick, .rightTrigger, .a]
            reading.noteLast("Cross", .a)
            reading.presses = 14
            hub.preview(pads: [
                BCDPad(id: "dualsense", name: "DualSense Wireless Controller", family: .playStation, battery: 84, charging: true),
                BCDPad(id: "xbox", name: "Xbox Wireless Controller", family: .xbox, battery: 12),
            ], reading: reading, xinput: true, mapping: true)
        }
        return hub
    }
}

extension BCDControllerHub {
    /// Settings › Controls › Controllers opens the app's hub (BCDControllerHub.shared); in the preview
    /// app it holds the same sample, so the row's screen shows controllers too.
    static func shareSample(empty: Bool) {
        let sample = BCDControllerHub.sample(empty: empty)
        shared.preview(pads: sample.pads, reading: sample.tester.reading, xinput: sample.xinput, mapping: sample.mapping)
    }
}

/// What the preview app shows for -BCDTab controllers.
struct BCDControllerPreview: View {
    let focus: BCDFocus
    @StateObject private var hub = BCDControllerHub.sample(empty: UserDefaults.standard.bool(forKey: "BCDEmpty"))

    var body: some View {
        BCDControllerScreen(hub: hub, focus: focus, close: {})
    }
}
