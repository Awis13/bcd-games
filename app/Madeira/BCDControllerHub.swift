// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation
// GameController calls handlers on each pad's handler queue and has no Sendable annotations.
@preconcurrency import GameController
#if canImport(UIKit)
import UIKit
#endif

// BCD's controller hub, written from docs/clean-room/controller-hub-spec.md and Apple's
// GameController documentation. It is always there and does three things:
//
// - knows which controllers are connected, for the status chips and the controller screen;
// - feeds a live tester: both sticks, both triggers and the last control pressed;
// - on request turns the controller into keyboard keys and mouse buttons (BCDPadRules' table),
//   for Windows games that read no controller. Off by default while XInput is on: XInput
//   already gives games the controller, and both together would deliver every press twice.
//
// Threads. GamepadInput (XInput) moves every pad's handler queue off the main thread and sets
// the gamepad profile's own value-changed handler; the hub leaves both alone. It takes the
// handlers of the single buttons, triggers, sticks and D-pad instead (BCDFocus only polls), so
// its input side (BCDPadRouter) runs on whatever queue the pad delivers on, behind one lock,
// and sends keys from there. Everything the screens observe changes on the main thread.
//
// Cost. No timer. The tester's values reach the main thread only while a tester is on screen,
// at most 30 times a second; with the mapping off and no tester, an input costs one lock.

// MARK: - Mapping rules (pure: tests/host/check-controller-hub.py compiles this part alone)

/// The controls of an extended gamepad that BCD reads.
enum BCDPadControl: String, CaseIterable, Hashable {
    case a, b, x, y
    case leftShoulder, rightShoulder, leftTrigger, rightTrigger
    case leftStickClick, rightStickClick, menu, options, home
    case dpad, leftStick, rightStick
}

/// Whose names the buttons carry.
enum BCDPadFamily: Hashable {
    case playStation, xbox, other
}

extension BCDPadControl {
    /// The control's name on this family's controllers.
    func title(_ family: BCDPadFamily) -> String {
        let ps = family == .playStation, xbox = family == .xbox
        switch self {
        case .a: return ps ? "Cross" : "A"
        case .b: return ps ? "Circle" : "B"
        case .x: return ps ? "Square" : "X"
        case .y: return ps ? "Triangle" : "Y"
        case .leftShoulder: return ps ? "L1" : xbox ? "LB" : "Left shoulder"
        case .rightShoulder: return ps ? "R1" : xbox ? "RB" : "Right shoulder"
        case .leftTrigger: return ps ? "L2" : xbox ? "LT" : "Left trigger"
        case .rightTrigger: return ps ? "R2" : xbox ? "RT" : "Right trigger"
        case .leftStickClick: return ps ? "L3" : "Left stick click"
        case .rightStickClick: return ps ? "R3" : "Right stick click"
        case .menu: return ps ? "Options" : "Menu"
        case .options: return ps ? "Create" : xbox ? "View" : "Select"
        case .home: return ps ? "PS button" : xbox ? "Xbox button" : "Home"
        case .dpad: return "D-pad"
        case .leftStick: return "Left stick"
        case .rightStick: return "Right stick"
        }
    }
}

/// Where a stick or the D-pad points: eight 45° sectors, clockwise from up.
enum BCDPadDirection: Int, CaseIterable {
    case up, upRight, right, downRight, down, downLeft, left, upLeft

    var title: String {
        ["up", "up-right", "right", "down-right", "down", "down-left", "left", "up-left"][rawValue]
    }
}

/// The four keys a stick or the D-pad drives: the first for up, the second for right, the third
/// for down, the fourth for left. A diagonal holds both of its neighbours.
struct BCDKeyCross: Equatable {
    let up: Int32, right: Int32, down: Int32, left: Int32

    static let wasd = BCDKeyCross(up: 0x57, right: 0x44, down: 0x53, left: 0x41)     // W D S A
    static let arrows = BCDKeyCross(up: 0x26, right: 0x27, down: 0x28, left: 0x25)   // Up Right Down Left

    func keys(_ direction: BCDPadDirection?) -> [Int32] {
        guard let direction else { return [] }
        switch direction {
        case .up: return [up]
        case .upRight: return [up, right]
        case .right: return [right]
        case .downRight: return [right, down]
        case .down: return [down]
        case .downLeft: return [down, left]
        case .left: return [left]
        case .upLeft: return [up, left]
        }
    }
}

/// What a held control holds on the Windows side.
enum BCDPadTarget: Hashable {
    case key(Int32), mouseLeft, mouseRight
}

/// One event for Windows: a key (a Windows virtual-key code) or a mouse button (MOUSEEVENTF flags).
enum BCDPadOutput: Equatable {
    case key(Int32, down: Bool)
    case mouse(UInt32)
}

/// The mapping's table and thresholds.
enum BCDPadRules {
    /// A stick or the D-pad rests while its deflection is under this.
    static let deadZone: Float = 0.22
    /// A trigger is down above this value, or whenever it reports itself pressed.
    static let triggerThreshold: Float = 0.3
    /// The tester names the D-pad once either of its values passes this.
    static let dpadShown: Float = 0.1

    // MOUSEEVENTF_LEFTDOWN, LEFTUP, RIGHTDOWN, RIGHTUP.
    static let leftDown: UInt32 = 0x0002, leftUp: UInt32 = 0x0004
    static let rightDown: UInt32 = 0x0008, rightUp: UInt32 = 0x0010

    /// The buttons' keys (Windows virtual-key codes).
    static let buttonKeys: [BCDPadControl: Int32] = [
        .a: 0x20,                   // Space
        .b: 0x1B,                   // Esc
        .x: 0x45,                   // E
        .y: 0x52,                   // R
        .leftShoulder: 0x10,        // Shift
        .rightShoulder: 0x09,       // Tab
        .leftStickClick: 0x11,      // Ctrl
        .rightStickClick: 0x46,     // F
        .menu: 0x1B,                // Esc
        .options: 0x0D,             // Enter
    ]
    /// The sticks and the D-pad: eight directions over four keys each.
    static let crosses: [BCDPadControl: BCDKeyCross] = [.leftStick: .wasd, .rightStick: .arrows, .dpad: .arrows]
    /// The triggers are the mouse buttons, crossed: the left one is the right button (aim), the
    /// right one the left button (fire).
    static let triggerTargets: [BCDPadControl: BCDPadTarget] = [.leftTrigger: .mouseRight, .rightTrigger: .mouseLeft]

    static func target(_ control: BCDPadControl) -> BCDPadTarget? {
        if let key = buttonKeys[control] { return .key(key) }
        return triggerTargets[control]
    }

    static func press(_ target: BCDPadTarget) -> BCDPadOutput {
        switch target {
        case .key(let vk): return .key(vk, down: true)
        case .mouseLeft: return .mouse(leftDown)
        case .mouseRight: return .mouse(rightDown)
        }
    }

    static func release(_ target: BCDPadTarget) -> BCDPadOutput {
        switch target {
        case .key(let vk): return .key(vk, down: false)
        case .mouseLeft: return .mouse(leftUp)
        case .mouseRight: return .mouse(rightUp)
        }
    }

    /// The sector (x, y) points into, centred on up, up-right, right... (nil only at exactly 0, 0).
    static func sector(x: Float, y: Float) -> BCDPadDirection? {
        guard x.isFinite, y.isFinite, x != 0 || y != 0 else { return nil }
        var degrees = atan2(Double(x), Double(y)) * 180 / Double.pi    // 0 is up, clockwise
        if degrees < 0 { degrees += 360 }
        return BCDPadDirection(rawValue: Int((degrees + 22.5) / 45) % 8)
    }

    /// Where a stick or the D-pad points for the mapping: nil (at rest) under the dead zone.
    static func direction(x: Float, y: Float) -> BCDPadDirection? {
        guard x.isFinite, y.isFinite, (x * x + y * y).squareRoot() >= deadZone else { return nil }
        return sector(x: x, y: y)
    }

    static func triggerDown(value: Float, pressed: Bool) -> Bool { pressed || value > triggerThreshold }

    /// XInput is on unless its setting (bcd.cfg's line first, else the environment) is exactly "0".
    static func xinput(configured: String?, environment: String?) -> Bool { (configured ?? environment) != "0" }

    /// The mapping switch before anyone touches it: on only while XInput is off.
    static func mappingDefault(xinput: Bool) -> Bool { !xinput }

    /// The tester's words for the D-pad, once it moves beyond `dpadShown`.
    static func dpadWords(x: Float, y: Float) -> String? {
        guard x.isFinite, y.isFinite, abs(x) > dpadShown || abs(y) > dpadShown else { return nil }
        let way = sector(x: x, y: y).map { " \($0.title)" } ?? ""
        return "D-pad\(way) · x \(number(x)) y \(number(y))"
    }

    static func number(_ value: Float) -> String {
        guard value.isFinite else { return "0.00" }
        return String(format: "%.2f", abs(value) < 0.005 ? 0 : value)
    }

    /// The table the controller screen shows, in this order.
    static let tableOrder: [BCDPadControl] = [.leftStick, .rightStick, .dpad, .a, .b, .x, .y, .leftShoulder,
                                              .rightShoulder, .leftStickClick, .rightStickClick, .menu, .options,
                                              .leftTrigger, .rightTrigger]

    static let keyNames: [Int32: String] = [
        0x20: "Space", 0x1B: "Esc", 0x45: "E", 0x52: "R", 0x10: "Shift", 0x09: "Tab", 0x11: "Ctrl", 0x46: "F",
        0x0D: "Enter", 0x57: "W", 0x41: "A", 0x53: "S", 0x44: "D", 0x26: "↑", 0x25: "←", 0x28: "↓", 0x27: "→",
    ]

    /// What a control sends, as key caps: a stick's four keys (up, left, down, right), a button's
    /// key, or a trigger's mouse button. Nil: the control sends nothing.
    static func sends(_ control: BCDPadControl) -> [String]? {
        if let cross = crosses[control] {
            return [cross.up, cross.left, cross.down, cross.right].map { keyNames[$0] ?? "?" }
        }
        switch target(control) {
        case .key(let vk)?: return [keyNames[vk] ?? String(format: "0x%02X", vk)]
        case .mouseLeft?: return ["Left click"]
        case .mouseRight?: return ["Right click"]
        case nil: return nil
        }
    }
}

/// The mapping's memory. Each stick and the D-pad of each pad keeps its own direction, each
/// button and trigger its own state. A key two of them hold (the D-pad and the right stick both
/// drive the arrows; B and Menu are both Esc) goes up only when the last of them lets go.
struct BCDPadMapper {
    struct Slot: Hashable {
        var pad: Int
        var control: BCDPadControl
    }

    private(set) var pointing: [Slot: BCDPadDirection] = [:]
    private(set) var down: Set<Slot> = []
    private(set) var holds: [BCDPadTarget: Int] = [:]

    var holding: Bool { !holds.isEmpty }

    /// A button pressed or let go: its key down or up.
    mutating func button(_ control: BCDPadControl, pressed: Bool, pad: Int = 0) -> [BCDPadOutput] {
        guard BCDPadRules.triggerTargets[control] == nil, let target = BCDPadRules.target(control) else { return [] }
        let slot = Slot(pad: pad, control: control)
        if pressed { return down.insert(slot).inserted ? hold(target) : [] }
        return down.remove(slot) != nil ? letGo(target) : []
    }

    /// A trigger's value: its mouse button goes down or up only when the trigger crosses over.
    mutating func trigger(_ control: BCDPadControl, value: Float, pressed: Bool, pad: Int = 0) -> [BCDPadOutput] {
        guard let target = BCDPadRules.triggerTargets[control] else { return [] }
        let slot = Slot(pad: pad, control: control)
        let now = BCDPadRules.triggerDown(value: value, pressed: pressed)
        guard now != down.contains(slot) else { return [] }
        if now {
            down.insert(slot)
            return hold(target)
        }
        down.remove(slot)
        return letGo(target)
    }

    /// A stick or the D-pad moved. When its sector changes, the keys it no longer holds go up
    /// first, then the new ones go down; at rest it holds nothing.
    mutating func aim(_ control: BCDPadControl, x: Float, y: Float, pad: Int = 0) -> [BCDPadOutput] {
        guard let cross = BCDPadRules.crosses[control] else { return [] }
        let slot = Slot(pad: pad, control: control)
        let new = BCDPadRules.direction(x: x, y: y), old = pointing[slot]
        guard new != old else { return [] }
        pointing[slot] = new
        let before = cross.keys(old), after = cross.keys(new)
        var out: [BCDPadOutput] = []
        for key in before where !after.contains(key) { out += letGo(.key(key)) }
        for key in after where !before.contains(key) { out += hold(.key(key)) }
        return out
    }

    /// A pad went away: what it held goes up.
    mutating func forget(pad: Int) -> [BCDPadOutput] {
        var out: [BCDPadOutput] = []
        for slot in pointing.keys.filter({ $0.pad == pad }).sorted(by: Self.order) {
            out += aim(slot.control, x: 0, y: 0, pad: pad)
        }
        for slot in down.filter({ $0.pad == pad }).sorted(by: Self.order) {
            down.remove(slot)
            if let target = BCDPadRules.target(slot.control) { out += letGo(target) }
        }
        return out
    }

    /// Everything goes up at once (the mapping turned off, BCD left the screen, Windows stopped
    /// taking the keys).
    mutating func releaseAll() -> [BCDPadOutput] {
        let out = holds.keys.sorted { Self.rank($0) < Self.rank($1) }.map(BCDPadRules.release)
        pointing = [:]
        down = []
        holds = [:]
        return out
    }

    private mutating func hold(_ target: BCDPadTarget) -> [BCDPadOutput] {
        holds[target, default: 0] += 1
        return holds[target] == 1 ? [BCDPadRules.press(target)] : []
    }

    private mutating func letGo(_ target: BCDPadTarget) -> [BCDPadOutput] {
        guard let count = holds[target] else { return [] }
        if count > 1 {
            holds[target] = count - 1
            return []
        }
        holds[target] = nil
        return [BCDPadRules.release(target)]
    }

    private static func order(_ a: Slot, _ b: Slot) -> Bool {
        let all = BCDPadControl.allCases
        return a.pad != b.pad ? a.pad < b.pad : all.firstIndex(of: a.control)! < all.firstIndex(of: b.control)!
    }

    private static func rank(_ target: BCDPadTarget) -> Int {
        switch target {
        case .key(let vk): return Int(vk)
        case .mouseLeft: return 0x10000
        case .mouseRight: return 0x10001
        }
    }
}

/// What the tester shows.
struct BCDPadReading: Equatable {
    var leftX: Float = 0, leftY: Float = 0
    var rightX: Float = 0, rightY: Float = 0
    var leftTrigger: Float = 0, rightTrigger: Float = 0
    /// Held now: buttons, triggers past their threshold, sticks out of rest, the D-pad moved.
    var held: Set<BCDPadControl> = []
    /// The last control pressed, in words ("Cross", "D-pad right · x 1.00 y 0.00").
    var last: String?
    var lastControl: BCDPadControl?
    /// Presses seen while the tester was on screen (the same button again still counts).
    var presses = 0

    mutating func noteLast(_ words: String, _ control: BCDPadControl) {
        last = words
        lastControl = control
        presses += 1
    }
}

/// A connected controller as the screens show it.
struct BCDPad: Identifiable, Equatable {
    var id: String
    var name: String
    var family: BCDPadFamily = .other
    /// Percent; nil when the controller does not say.
    var battery: Int? = nil
    var charging = false
    /// It has the extended gamepad profile: only those take part in the tester and the mapping.
    var gamepad = true
}

// MARK: - Input from GameController (the pad's handler queue)

/// What the hub needs from the rest of BCD. In the app BCDControllerHubLive.swift hands in the
/// engine's (Windows' input queue, BCD's log, bcd.cfg); the preview and the host check pass their own.
struct BCDControllerOutlet: Sendable {
    /// One key or mouse button to Windows. Called on the pad's handler queue (or the main thread).
    var send: @Sendable (BCDPadOutput) -> Void
    /// Whether Windows takes the controller's keys now; asked on every mapped input.
    var accepting: @Sendable () -> Bool
    /// One line in BCD's log: `success` at the OK level, else info.
    var log: @Sendable (_ line: String, _ success: Bool) -> Void
    /// XInput's setting as written: bcd.cfg's line, else the process environment's variable.
    var xinputSetting: @Sendable () -> (configured: String?, environment: String?)

    static let inert = BCDControllerOutlet(send: { _ in }, accepting: { false }, log: { _, _ in },
                                           xinputSetting: { (nil, nil) })
}

/// The hub's input side. GameController calls the handlers it installs on each pad's handler
/// queue: GamepadInput's own serial queue while XInput is on, the main thread otherwise. Its
/// state sits behind one lock and it touches nothing of the main actor's: the tester's values
/// travel to the hub on the main thread.
final class BCDPadRouter: @unchecked Sendable {
    private let lock = NSLock()
    private var outlet: BCDControllerOutlet
    private var mapper = BCDPadMapper()
    private var mapping = false
    private var testing = false
    private var reading = BCDPadReading()
    private var flushQueued = false
    private var lastFlush: UInt64 = 0
    private var nextPad = 0
    /// Set and read on the main thread only.
    weak var hub: BCDControllerHub?

    init(outlet: BCDControllerOutlet) { self.outlet = outlet }

    func use(_ outlet: BCDControllerOutlet) {
        lock.lock(); defer { lock.unlock() }
        self.outlet = outlet
    }

    /// Off: every key and mouse button the mapping holds goes up.
    func setMapping(_ on: Bool) {
        lock.lock(); defer { lock.unlock() }
        mapping = on
        if !on { send(mapper.releaseAll()) }
    }

    func setTesting(_ on: Bool, start: BCDPadReading) {
        lock.lock(); defer { lock.unlock() }
        testing = on
        reading = on ? start : BCDPadReading()
    }

    func releaseAll() {
        lock.lock(); defer { lock.unlock() }
        send(mapper.releaseAll())
    }

    // MARK: Handlers

    /// Takes the handlers of the pad's buttons, triggers, sticks and D-pad. Never the profile's
    /// own value-changed handler (GamepadInput's) and never the pad's handler queue. Returns the
    /// number the mapping knows the pad by.
    func install(on pad: GCExtendedGamepad, family: BCDPadFamily) -> Int {
        lock.lock()
        let index = nextPad
        nextPad += 1
        lock.unlock()
        for (input, control) in Self.buttons(of: pad) {
            input.pressedChangedHandler = { [weak self] _, _, pressed in
                self?.button(control, pressed: pressed, pad: index, family: family)
            }
        }
        for (input, control) in Self.triggers(of: pad) {
            input.valueChangedHandler = { [weak self] _, value, pressed in
                self?.trigger(control, value: value, pressed: pressed, pad: index, family: family)
            }
        }
        for (input, control) in Self.directions(of: pad) {
            input.valueChangedHandler = { [weak self] _, x, y in
                self?.aim(control, x: x, y: y, pad: index)
            }
        }
        return index
    }

    /// Gives the handlers back and lets go of what the pad held.
    func remove(from pad: GCExtendedGamepad, index: Int) {
        for (input, _) in Self.buttons(of: pad) { input.pressedChangedHandler = nil }
        for (input, _) in Self.triggers(of: pad) { input.valueChangedHandler = nil }
        for (input, _) in Self.directions(of: pad) { input.valueChangedHandler = nil }
        lock.lock(); defer { lock.unlock() }
        send(mapper.forget(pad: index))
    }

    static func buttons(of pad: GCExtendedGamepad) -> [(GCControllerButtonInput, BCDPadControl)] {
        var list: [(GCControllerButtonInput, BCDPadControl)] = [
            (pad.buttonA, .a), (pad.buttonB, .b), (pad.buttonX, .x), (pad.buttonY, .y),
            (pad.leftShoulder, .leftShoulder), (pad.rightShoulder, .rightShoulder), (pad.buttonMenu, .menu),
        ]
        if let input = pad.leftThumbstickButton { list.append((input, .leftStickClick)) }
        if let input = pad.rightThumbstickButton { list.append((input, .rightStickClick)) }
        if let input = pad.buttonOptions { list.append((input, .options)) }
        if let input = pad.buttonHome { list.append((input, .home)) }
        return list
    }

    static func triggers(of pad: GCExtendedGamepad) -> [(GCControllerButtonInput, BCDPadControl)] {
        [(pad.leftTrigger, .leftTrigger), (pad.rightTrigger, .rightTrigger)]
    }

    static func directions(of pad: GCExtendedGamepad) -> [(GCControllerDirectionPad, BCDPadControl)] {
        [(pad.dpad, .dpad), (pad.leftThumbstick, .leftStick), (pad.rightThumbstick, .rightStick)]
    }

    // MARK: Input

    private func button(_ control: BCDPadControl, pressed: Bool, pad: Int, family: BCDPadFamily) {
        lock.lock(); defer { lock.unlock() }
        if mapping { map { $0.button(control, pressed: pressed, pad: pad) } }
        guard testing else { return }
        if pressed {
            reading.held.insert(control)
            reading.noteLast(control.title(family), control)
        } else {
            reading.held.remove(control)
        }
        queueFlush()
    }

    private func trigger(_ control: BCDPadControl, value: Float, pressed: Bool, pad: Int, family: BCDPadFamily) {
        lock.lock(); defer { lock.unlock() }
        if mapping { map { $0.trigger(control, value: value, pressed: pressed, pad: pad) } }
        guard testing else { return }
        if control == .leftTrigger { reading.leftTrigger = value } else { reading.rightTrigger = value }
        if BCDPadRules.triggerDown(value: value, pressed: pressed) {
            if reading.held.insert(control).inserted { reading.noteLast(control.title(family), control) }
        } else {
            reading.held.remove(control)
        }
        queueFlush()
    }

    private func aim(_ control: BCDPadControl, x: Float, y: Float, pad: Int) {
        lock.lock(); defer { lock.unlock() }
        if mapping { map { $0.aim(control, x: x, y: y, pad: pad) } }
        guard testing else { return }
        switch control {
        case .leftStick:
            (reading.leftX, reading.leftY) = (x, y)
            moved(control, BCDPadRules.direction(x: x, y: y) != nil)
        case .rightStick:
            (reading.rightX, reading.rightY) = (x, y)
            moved(control, BCDPadRules.direction(x: x, y: y) != nil)
        default:
            let words = BCDPadRules.dpadWords(x: x, y: y)
            if let words, words != reading.last { reading.noteLast(words, control) }
            moved(control, words != nil)
        }
        queueFlush()
    }

    private func moved(_ control: BCDPadControl, _ away: Bool) {
        if away { reading.held.insert(control) } else { reading.held.remove(control) }
    }

    /// Lock held, mapping on. Keys go out only while Windows takes them; when it stops, what the
    /// mapping held goes up and inputs are dropped until it takes them again.
    private func map(_ step: (inout BCDPadMapper) -> [BCDPadOutput]) {
        if outlet.accepting() {
            send(step(&mapper))
        } else if mapper.holding {
            send(mapper.releaseAll())
        }
    }

    private func send(_ outputs: [BCDPadOutput]) {
        for output in outputs { outlet.send(output) }
    }

    /// Lock held: the tester's values go to the main thread, at most 30 times a second.
    private func queueFlush() {
        guard !flushQueued else { return }
        flushQueued = true
        let now = DispatchTime.now().uptimeNanoseconds, due = lastFlush + 33_000_000
        let wait = due > now ? Int(due - now) : 0
        DispatchQueue.main.asyncAfter(deadline: .now() + .nanoseconds(wait)) { [weak self] in self?.flush() }
    }

    /// The main thread.
    private func flush() {
        lock.lock()
        let value = reading, on = testing
        flushQueued = false
        lastFlush = DispatchTime.now().uptimeNanoseconds
        lock.unlock()
        guard on else { return }
        MainActor.assumeIsolated { hub?.receive(value) }
    }
}

// MARK: - The hub (main thread)

/// The tester's values, kept apart from the hub: views that show only the controllers (the status
/// chips) are not redrawn while a tester runs.
@MainActor final class BCDPadTester: ObservableObject {
    /// They move only while a tester is on screen.
    @Published fileprivate(set) var reading = BCDPadReading()
    /// A tester is on screen and the hub feeds it.
    @Published fileprivate(set) var live = false
}

/// The service the screens observe. The app's is `shared`, started at launch with the engine's
/// outlet (MadeiraApp, BCDControllerHubLive.swift); the preview app makes its own sample.
@MainActor final class BCDControllerHub: ObservableObject {
    static let shared = BCDControllerHub()
    /// The mapping switch, remembered across launches.
    static let mappingKey = "bcd.controllers.mapping"

    /// Connected controllers, in the order they arrived; none while the hub is off.
    @Published private(set) var pads: [BCDPad] = []
    /// The enabled switch.
    @Published private(set) var isOn = true
    /// The controller as keyboard and mouse.
    @Published private(set) var mapping: Bool
    /// XInput as this launch runs it: read once at start, as the engine reads it.
    @Published private(set) var xinput: Bool
    /// The tester's values (a separate object: observing the hub does not redraw with them).
    let tester = BCDPadTester()

    var connected: Bool { !pads.isEmpty }
    /// The first controller's name ("Controller" when it gives none), for the status chips.
    var leadName: String? { pads.first?.name }
    /// Whose button names the screen uses: the first controller's (Xbox's without one).
    var family: BCDPadFamily { pads.first?.family ?? .xbox }

    private struct Attached {
        let controller: GCController
        let profile: GCExtendedGamepad?
        let index: Int
        let name: String
    }

    private var outlet: BCDControllerOutlet
    private let defaults: UserDefaults
    private let router: BCDPadRouter
    private var attached: [Attached] = []
    private var observers: [NSObjectProtocol] = []
    private var started = false
    private var watchers = 0
    /// The preview's controllers (nil in the app).
    private var samplePads: [BCDPad]?

    init(outlet: BCDControllerOutlet = .inert, defaults: UserDefaults = .standard) {
        self.outlet = outlet
        self.defaults = defaults
        router = BCDPadRouter(outlet: outlet)
        let setting = outlet.xinputSetting()
        let xinput = BCDPadRules.xinput(configured: setting.configured, environment: setting.environment)
        self.xinput = xinput
        mapping = defaults.object(forKey: Self.mappingKey) as? Bool ?? BCDPadRules.mappingDefault(xinput: xinput)
        router.hub = self
        router.setMapping(mapping)
    }

    /// The app, once at launch: the engine's outlet, the controllers already connected, then
    /// those that come and go. Calling it again does nothing.
    func start(_ outlet: BCDControllerOutlet) {
        guard !started else { return }
        started = true
        self.outlet = outlet
        router.use(outlet)
        let setting = outlet.xinputSetting()
        xinput = BCDPadRules.xinput(configured: setting.configured, environment: setting.environment)
        if defaults.object(forKey: Self.mappingKey) == nil { mapping = BCDPadRules.mappingDefault(xinput: xinput) }
        router.setMapping(mapping)

        let center = NotificationCenter.default
        observers.append(center.addObserver(forName: .GCControllerDidConnect, object: nil, queue: .main) { [weak self] note in
            guard let controller = note.object as? GCController else { return }
            MainActor.assumeIsolated { self?.attach(controller) }
        })
        observers.append(center.addObserver(forName: .GCControllerDidDisconnect, object: nil, queue: .main) { [weak self] note in
            guard let controller = note.object as? GCController else { return }
            MainActor.assumeIsolated { self?.detach(controller) }
        })
        #if canImport(UIKit)
        // Leaving the screen: nothing stays held in the game while BCD is away.
        for name in [UIApplication.willResignActiveNotification, UIApplication.didEnterBackgroundNotification] {
            observers.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.router.releaseAll() }
            })
        }
        // Back on screen: controllers that came or went meanwhile, and the batteries.
        observers.append(center.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.refresh() }
        })
        #endif
        refresh()
        outlet.log("[controllers] hub started: \(attached.count) connected, XInput \(xinput ? "on" : "off"), "
                   + "keys and mouse \(mapping ? "on" : "off")", false)
    }

    // MARK: Switches

    /// Off: no controller is reported, the tester says it is off, the mapping holds nothing.
    /// On: looks for controllers again.
    func setOn(_ on: Bool) {
        guard on != isOn else { return }
        isOn = on
        if let samplePads {
            pads = on ? samplePads : []
            tester.live = on && !samplePads.isEmpty
            return
        }
        outlet.log("[controllers] reading controllers: \(on ? "on" : "off")", false)
        if on {
            refresh()
        } else {
            for entry in attached {
                if let profile = entry.profile { router.remove(from: profile, index: entry.index) }
            }
            attached = []
            router.releaseAll()
            publishPads()
        }
        updateTesting()
    }

    /// The controller as keyboard and mouse; remembered.
    func setMapping(_ on: Bool) {
        guard on != mapping else { return }
        mapping = on
        defaults.set(on, forKey: Self.mappingKey)
        router.setMapping(on)
        outlet.log("[controllers] keys and mouse from the controller: \(on ? "on" : "off")", false)
    }

    /// A tester appeared (true) or went away (false). Nothing is sent to the main thread for
    /// it while none is on screen.
    func watchTester(_ on: Bool) {
        watchers = max(0, watchers + (on ? 1 : -1))
        updateTesting()
    }

    private func updateTesting() {
        guard samplePads == nil else { return }
        let want = watchers > 0 && isOn
        guard want != tester.live else { return }
        tester.live = want
        let start = want ? currentReading() : BCDPadReading()
        router.setTesting(want, start: start)
        tester.reading = start
    }

    /// The first gamepad's sticks and triggers as they stand (read here, as BCDFocus reads them).
    private func currentReading() -> BCDPadReading {
        var now = BCDPadReading()
        guard let pad = attached.lazy.compactMap(\.profile).first else { return now }
        now.leftX = pad.leftThumbstick.xAxis.value
        now.leftY = pad.leftThumbstick.yAxis.value
        now.rightX = pad.rightThumbstick.xAxis.value
        now.rightY = pad.rightThumbstick.yAxis.value
        now.leftTrigger = pad.leftTrigger.value
        now.rightTrigger = pad.rightTrigger.value
        return now
    }

    /// The router's tester values, on the main thread.
    func receive(_ value: BCDPadReading) {
        guard tester.live, value != tester.reading else { return }
        tester.reading = value
    }

    // MARK: Detection

    /// Looks for controllers again: those that left go, new ones come, batteries are read again.
    func refresh() {
        guard isOn, samplePads == nil else { return }
        let now = GCController.controllers()
        for entry in attached where !now.contains(where: { $0 === entry.controller }) { detach(entry.controller) }
        for controller in now { attach(controller) }
        publishPads()
    }

    /// A controller connected (or was there at start): one log line naming it.
    func attach(_ controller: GCController) {
        guard isOn, samplePads == nil, !attached.contains(where: { $0.controller === controller }) else { return }
        let pad = Self.describe(controller)
        let profile = controller.extendedGamepad
        let index = profile.map { router.install(on: $0, family: pad.family) } ?? -1
        attached.append(Attached(controller: controller, profile: profile, index: index, name: pad.name))
        outlet.log("[controllers] connected: \(pad.name)"
                   + (profile == nil ? " (no extended gamepad profile: not read)" : ""), true)
        publishPads()
    }

    /// A controller disconnected: one log line naming it.
    func detach(_ controller: GCController) {
        guard let i = attached.firstIndex(where: { $0.controller === controller }) else { return }
        let entry = attached.remove(at: i)
        if let profile = entry.profile { router.remove(from: profile, index: entry.index) }
        outlet.log("[controllers] disconnected: \(entry.name)", false)
        publishPads()
        if tester.live {
            // What the pad held disappears from the tester with it.
            let start = currentReading()
            router.setTesting(true, start: start)
            tester.reading = start
        }
    }

    private func publishPads() {
        let list = isOn ? attached.map { Self.describe($0.controller) } : []
        if list != pads { pads = list }
    }

    static func describe(_ controller: GCController) -> BCDPad {
        let vendor = controller.vendorName?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let profile = controller.extendedGamepad
        let category = controller.productCategory
        let family: BCDPadFamily
        if profile is GCDualSenseGamepad || profile is GCDualShockGamepad
            || category.contains("DualSense") || category.contains("DualShock") {
            family = .playStation
        } else if profile is GCXboxGamepad || category.contains("Xbox") {
            family = .xbox
        } else {
            family = .other
        }
        var battery: Int?
        var charging = false
        if let state = controller.battery, state.batteryState != .unknown, state.batteryLevel.isFinite {
            battery = Int((max(0, min(1, state.batteryLevel)) * 100).rounded())
            charging = state.batteryState == .charging
        }
        return BCDPad(id: String(UInt(bitPattern: ObjectIdentifier(controller).hashValue), radix: 16),
                      name: vendor.isEmpty ? "Controller" : vendor, family: family, battery: battery,
                      charging: charging, gamepad: profile != nil)
    }

    // MARK: Preview

    /// The preview app's sample (app/BCDPreview): the screen as someone with controllers sees it.
    func preview(pads: [BCDPad], reading: BCDPadReading, xinput: Bool, mapping: Bool) {
        samplePads = pads
        self.pads = pads
        self.xinput = xinput
        self.mapping = mapping
        isOn = true
        tester.reading = reading
        tester.live = !pads.isEmpty
    }
}
