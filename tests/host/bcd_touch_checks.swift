// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The behaviour half of tests/host/check-bcd-touch.py. That script compiles this file together
// with the stored layout types (ControlAction, TouchControl from ContentView.swift), the pure
// layout store (TouchControlPresets.swift), the classic control's own input functions (snap,
// stickKeys, applyStick and press, frozen in tests/fixtures/classic-touch-control.swift.txt from the
// classic touch control deleted in wave 3, wrapped in ClassicButton with stubs
// that record what they send), BCDTouchCore.swift and BCDTouchLive.swift's conversion, and
// runs it: BCD's controls must send exactly what the classic ones sent, be the same size, take
// touches in the same places, and convert layouts without changing a byte.

var failures = 0
func require(_ ok: @autoclosure () -> Bool, _ what: String) {
    if ok() { print("PASS: " + what) } else { print("FAIL: " + what); failures += 1 }
}

/// A fixed sequence, so every run checks the same cases.
struct Dice {
    var state: UInt64
    mutating func next() -> UInt64 { state = state &* 6364136223846793005 &+ 1442695040888963407; return state >> 33 }
    mutating func unit() -> Double { Double(next() % 1_000_001) / 1_000_000 }
    mutating func range(_ a: Double, _ b: Double) -> Double { a + (b - a) * unit() }
}

func record(_ e: BCDTouchEvent) -> String {
    switch e {
    case .key(let vk, let down): return "key \(vk) \(down ? 1 : 0)"
    case .mouse(let flags): return "mouse \(flags)"
    case .toggleKeyboard: return "keyboard"
    }
}

/// One step's events as a set of releases, then a set of presses, with the order inside each
/// set left out (the classic stick went through a Set, whose order changes from run to run).
func normalised(_ steps: [[String]]) -> [[String]] {
    steps.map { step in
        step.filter { $0.hasSuffix(" 0") && $0.hasPrefix("key") }.sorted()
            + step.filter { !($0.hasSuffix(" 0") && $0.hasPrefix("key")) }.sorted()
    }
}

/// Releases never come after a press within a step.
func releasesFirst(_ steps: [[String]]) -> Bool {
    steps.allSatisfy { step in
        let firstPress = step.firstIndex { $0.hasPrefix("key") && $0.hasSuffix(" 1") } ?? step.count
        return !step[firstPress...].contains { $0.hasPrefix("key") && $0.hasSuffix(" 0") }
    }
}

/// The classic control under one finger: SwiftUI's DragGesture(minimumDistance: 0) called
/// onChanged at touch-down (no travel) and at every move, onEnded at the lift.
func classicRun(_ action: ControlAction, diameter: CGFloat, path: [CGSize]) -> (steps: [[String]], taps: Int) {
    classicLog = []
    classicTaps = 0
    var c = TouchControl()
    c.action = action
    let button = ClassicButton(control: c, diameter: diameter)
    var isDown = false
    var steps: [[String]] = []
    for t in path {
        classicLog = []
        if let q = action.stickKeys {
            isDown = true
            button.applyStick(button.snap(t), q)
        } else if !isDown {
            isDown = true
            button.press(true)
        }
        steps.append(classicLog)
    }
    classicLog = []
    if let q = action.stickKeys {
        button.applyStick(-1, q)
        isDown = false
    } else if isDown {
        isDown = false
        button.press(false)
    }
    steps.append(classicLog)
    return (steps, classicTaps)
}

/// BCD's control under the same finger: BCDTouchKeyView begins at touch-down, moves with the
/// finger's travel and ends at the lift.
func bcdRun(_ action: BCDTouchAction, diameter: Double, path: [CGSize]) -> (steps: [[String]], taps: Int) {
    var log: [String] = []
    var taps = 0
    let press = BCDTouchPress(action: action, diameter: diameter, send: { log.append(record($0)) }, tap: { taps += 1 })
    var steps: [[String]] = []
    for (i, t) in path.enumerated() {
        log = []
        if i == 0 { press.begin() }
        press.move(dx: Double(t.width), dy: Double(t.height))
        steps.append(log)
    }
    log = []
    press.end()
    steps.append(log)
    return (steps, taps)
}

let actions: [ControlAction] = [.key(0x41), .key(0x20), .key(0x0D), .key(0x70), .mouseLeft, .mouseRight,
                                .keyboardToggle, .none, .joystickWASD, .joystickArrows, .pad("A"), .pad("LS")]
var dice = Dice(state: 0xBCD_7006)

// ---- every control sends what it always sent ----------------------------------------------
for action in actions {
    let mine = BCDTouchAction(stored: action)
    var same = true, ordered = true, tapsSame = true
    var detail = ""
    for scale in [0.5, 1.0, 1.45, 3.0] {
        for layout in [0.5, 1.0, 2.0] {
            let classicD = 64 * CGFloat(scale) * CGFloat(layout)
            let d = BCDTouchGeometry.diameter(scale: scale, layoutScale: layout)
            if Double(classicD) != d { same = false; detail = "diameter \(classicD) vs \(d)" }
            for _ in 0..<60 {
                // A finger: touch-down, then a walk that crosses the dead zone and the sectors' edges.
                var path = [CGSize.zero]
                var x = 0.0, y = 0.0
                for _ in 0..<Int(dice.next() % 24) {
                    if dice.next() % 5 == 0 {
                        // Straight onto a sector's edge or the dead zone's rim.
                        let angle = Double(dice.next() % 16) * .pi / 8
                        let r = d * (dice.next() % 2 == 0 ? 0.22 : dice.range(0.05, 1.5))
                        x = r * sin(angle); y = -r * cos(angle)
                    } else {
                        x += dice.range(-0.5, 0.5) * d; y += dice.range(-0.5, 0.5) * d
                    }
                    path.append(CGSize(width: x, height: y))
                }
                let a = classicRun(action, diameter: classicD, path: path)
                let b = bcdRun(mine, diameter: d, path: path)
                if normalised(a.steps) != normalised(b.steps) {
                    same = false
                    detail = "\(path): classic \(a.steps) BCD \(b.steps)"
                }
                if !releasesFirst(a.steps) || !releasesFirst(b.steps) { ordered = false }
                if a.taps != b.taps { tapsSame = false }
            }
        }
    }
    require(same, "\(action): BCD sends the same events, step by step, as the classic control \(same ? "" : detail)")
    require(ordered, "\(action): keys let go before new ones are pressed")
    require(tapsSame, "\(action): the same light ticks (down only; a stick when it leaves the centre)")
}
require(BCDTouchRules.press(.pad("A"), down: true).isEmpty && BCDTouchRules.press(.pad("LS"), down: false).isEmpty,
        "controller controls send nothing here: the touch gamepad's surface does")

// An interrupted touch (cancelled, the app inactive, the control gone) lets go of what it holds.
do {
    var log: [String] = []
    let press = BCDTouchPress(action: .keyStick(.wasd), diameter: 64, send: { log.append(record($0)) })
    press.begin(); press.move(dx: 40, dy: -40)
    press.end(); press.end()
    require(log == ["key 87 1", "key 68 1", "key 87 0", "key 68 0"] || log == ["key 87 1", "key 68 1", "key 68 0", "key 87 0"],
            "a held diagonal is let go once when the touch ends, however often it is ended \(log)")
    log = []
    let key = BCDTouchPress(action: .key(0x20), diameter: 64, send: { log.append(record($0)) })
    key.begin(); key.rebind(.key(0x41)); key.end()
    require(log == ["key 32 1", "key 32 0"], "a control rebound while held lets go of its old key first \(log)")
}

// ---- the same size and the same touch area -------------------------------------------------
let names = ["A", "B", "X", "Y", "D↑", "D↓", "D←", "D→", "LB", "RB", "LT", "RT", "LS", "RS", "L3", "R3",
             "Menu", "View", "Guide", "Unknown"]
let shapes: [ControlAction] = names.map { .pad($0) } + [.key(0x41), .mouseLeft, .mouseRight, .keyboardToggle, .none,
                                                         .joystickWASD, .joystickArrows]
var sizesSame = true, areasSame = true, stickSame = true
for action in shapes {
    let mine = BCDTouchAction(stored: action)
    if mine.isStick != (action.stickKeys != nil || action.isPadStick) { stickSame = false }
    for d in stride(from: 16.0, through: 400.0, by: 7.5) {
        let classic = action.controlSize(diameter: CGFloat(d))
        let bcd = BCDTouchGeometry.size(mine.shape, diameter: d)
        if classic != bcd { sizesSame = false }
        // Classic: the window took a touch within half a diameter of the centre; a controller
        // control's UIKit surface then took all of its frame, any other control its round face.
        for _ in 0..<40 {
            let p = CGPoint(x: dice.range(-1.2, 1.2) * d, y: dice.range(-1.2, 1.2) * d)
            let inWindow = hypot(Double(p.x), Double(p.y)) <= d / 2
            let inView = action.isPad ? (abs(p.x) <= classic.width / 2 && abs(p.y) <= classic.height / 2)
                                      : hypot(Double(p.x), Double(p.y)) <= d / 2
            if (inWindow && inView) != BCDTouchGeometry.takes(p, action: mine, diameter: d) { areasSame = false }
        }
    }
}
require(sizesSame, "every control is drawn the same size as before, at every diameter")
require(areasSame, "every control takes touches in the same area as before")
require(stickSame, "the same controls are sticks")
require(BCDTouchGeometry.addPosition(count: 4) == (0.56, 0.5) && BCDTouchGeometry.addPosition(count: 5) == (0.62, 0.56),
        "an added control goes where it went before")

// ---- layouts convert both ways without changing a byte ------------------------------------
let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
var controls: [TouchControl] = []
for action in shapes + [.key(0x7B), .key(0xDE), .key(0x6F), .key(0x5B), .key(0x08)] {
    for link in [nil, "RT", "LS", "D↑"] as [String?] {
        var c = TouchControl()
        c.nx = dice.range(0.03, 0.97); c.ny = dice.range(0.03, 0.97); c.scale = dice.range(0.5, 3)
        c.action = action
        c.padBinding = link
        controls.append(c)
    }
}
let before = try! encoder.encode(controls)
let back = controls.map(BCDTouchItem.init(stored:)).map(\.stored)
require(try! encoder.encode(back) == before && back == controls,
        "every control (all \(shapes.count + 5) actions, with and without a controller link) converts back unchanged")
let written = #"{"controls":[{"id":"6F1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F","nx":0.25,"ny":0.75,"scale":1.45,"action":{"pad":{"_0":"LS"}}},{"id":"7A1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F","nx":0.5,"ny":0.5,"scale":1,"action":{"joystickWASD":{}},"padBinding":"LS"}]}"#
struct Stored: Codable, Equatable { var controls: [TouchControl] }
let old = try! JSONDecoder().decode(Stored.self, from: Data(written.utf8))
let again = Stored(controls: old.controls.map(BCDTouchItem.init(stored:)).map(\.stored))
require(try! encoder.encode(again) == (try! encoder.encode(old)), "a controls file the old editor wrote comes back byte for byte")

var store = ControlPresetStore()
var n = 0
_ = store.save(name: "Racing", controls: controls, newID: { n += 1; return "user-\(n)" })
_ = store.save(name: "Keys", controls: Array(controls.prefix(9)), newID: { n += 1; return "user-\(n)" })
let file = try! store.encoded()
var edited = try! ControlPresetStore.decoded(file)
for p in edited.user {
    _ = edited.overwrite(id: p.id, controls: p.controls.map(BCDTouchItem.init(stored:)).map(\.stored))
}
require(try! edited.encoded() == file, "bcd-control-presets.json written after a pass through BCD's values is the same file")
require(ControlPresetStore.builtIns.first.map { BCDTouchNames.layoutName(id: $0.id, stored: $0.name) } == "Full controller",
        "the built-in is shown as \"Full controller\" (its stored name and id stay)")

// ---- the editor's panel never covers the selected control on common screens ---------------
var covered: [String] = []
for (w, h) in [(844.0, 390.0), (667.0, 375.0), (956.0, 440.0), (1180.0, 820.0), (390.0, 844.0), (820.0, 1180.0)] {
    for _ in 0..<300 {
        let d = 64 * dice.range(0.5, 1.8)
        let control = CGRect(x: dice.range(0.03, 0.97) * w - d / 2, y: dice.range(0.03, 0.97) * h - d / 2, width: d, height: d)
        let panel = BCDTouchPanelPlacement.dock(screen: CGSize(width: w, height: h), left: w > h ? 47 : 0, right: w > h ? 47 : 0,
                                                top: w > h ? 0 : 47, bottom: w > h ? 21 : 34, bar: 60, avoid: control)
        if panel.intersects(control) { covered.append("\(Int(w))x\(Int(h)) \(control)") }
        if panel.minX < 0 || panel.maxX > w || panel.minY < 0 || panel.maxY > h { covered.append("off screen \(panel)") }
    }
}
require(covered.isEmpty, "the selected control's panel stays on screen and off the control \(covered.prefix(3))")

if failures > 0 { print("\(failures) FAILED"); exit(1) }
print("PASS: BCD's touch controls send the same events, take the same touches and keep layouts byte for byte")
