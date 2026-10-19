#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's controller hub (app/Madeira/BCDControllerHub.swift, BCDControllerHubLive.swift and
BCDControllerScreen.swift), as docs/clean-room/controller-hub-spec.md section 4 asks.

Source checks (every host):
  - BCD's licence header on the hub's files; the engine side is ENGINE_BOUND, the hub and the
    screen name no engine type (the preview build compiles them);
  - the earlier controller manager is gone: its file is not in the tree, and nothing outside
    docs/ names it or its setup sheet (app, tools, build, tests, workflows, the Xcode project);
  - the Xcode project builds the three files;
  - no profile-level handler and no handler queue: the hub assigns handlers only to the button,
    trigger, stick and D-pad elements; GamepadInput still installs its own as before;
  - observable state lives in the main-actor hub only, and the input side reaches it through
    the main queue;
  - the engine side posts keys with winios_post_key and mouse buttons with
    winios_pointer(0, 0, flags, 0) and reads XInput from env.MADEIRA_XINPUT, then the environment;
  - the call sites: Settings › Controls opens BCD's controller screen, MadeiraApp starts the hub
    after GamepadInput (wave 3: ContentView's and HomeView's badges are deleted);
  - CI runs this check.
Behaviour (swiftc on the host; --require-swift makes its absence a failure):
  - the mapping rules alone (the file's pure section): the eight sectors and rest, the key sets
    of all eight directions and rest, releases before presses on a sector change, each source
    on its own, keys two sources share, the buttons' keys, the triggers' threshold and flags, the
    mapping switch's default from XInput's setting;
  - the whole hub on this Mac with GameController's snapshot pads, set up the way GamepadInput
    leaves a pad (its own handler queue, a profile handler): the profile handler and the queue
    stay; presses arrive on that queue and come out as keys and mouse buttons; the observable
    state changes only on the main thread; one log line per connection; the switches.
Build (xcrun; --require-swift makes its absence a failure):
  - the preview build compiles (tools/ui-preview.py --build-only); --skip-preview leaves it out.

  check-controller-hub.py [--require-swift] [--skip-preview]
"""
import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
ap.add_argument("--skip-preview", action="store_true", help="leave out the preview build")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures += 1


def block(text, header):
    p = text.index(header)
    a = text.index("{", p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == "{") - (text[b] == "}")
        b += 1
    return text[p:b]


def code(text):
    """The text without whole-line comments."""
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n"
          "// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
HUB, LIVE, SCREEN = "BCDControllerHub.swift", "BCDControllerHubLive.swift", "BCDControllerScreen.swift"
OLD = "GameController" + "Manager"          # split so this file is the only one that may hold the name
OLD_SHEET = "ControllerSetup" + "Sheet"
hub = (APP / HUB).read_text()
live = (APP / LIVE).read_text()
screen = (APP / SCREEN).read_text()
sample = (R / "app/BCDPreview/BCDControllerHubSample.swift").read_text()
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()

# ---- files, licence, preview, project ------------------------------------------------------
for name, text in ((HUB, hub), (LIVE, live), (SCREEN, screen), ("BCDControllerHubSample.swift", sample)):
    check(text.startswith(HEADER), f"{name}: BCD's licence header, exactly")
spec = importlib.util.spec_from_file_location("ui_preview", R / "tools/ui-preview.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)
check(LIVE in preview.ENGINE_BOUND and HUB not in preview.ENGINE_BOUND and SCREEN not in preview.ENGINE_BOUND,
      "tools/ui-preview.py: the engine side stays out of the preview build, the hub and the screen are in it",
      sorted(preview.ENGINE_BOUND))
ENGINE = ["LibraryModel", "LibraryEntry", "LibraryController", "MadeiraConfig", "LogStore", "GamepadInput",
          "WineProcessBridge", "ContentView", "HardwareInput", "PadKeyboardMouse", "winios_", "wine_process_is_running",
          "MadeiraDock", "JITCoordinator", OLD]
named = {n: sorted({t for t in ENGINE if re.search(r"\b" + t, code(text))}) for n, text in ((HUB, hub), (SCREEN, screen))}
check(not any(named.values()), "the hub and the screen name no engine type (only BCDControllerHubLive.swift does)", named)
for name in (HUB, SCREEN, LIVE):
    check(pbx.count(f"/* {name} in Sources */") == 2 and pbx.count(f"path = {name};") == 1,
          f"the Xcode project builds {name}")

# ---- the earlier component is gone -----------------------------------------------------------
check(not (APP / f"{OLD}.swift").exists(), f"app/Madeira/{OLD}.swift is gone")
tracked = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "app", "tools", "build",
                          "tests", ".github", "madeira-d3d12/src", "bcd-logs", "bcd-smoke"],
                         cwd=R, capture_output=True, text=True)
if tracked.returncode == 0:
    paths = [p for p in tracked.stdout.splitlines() if p != "tests/host/check-controller-hub.py"]
    check(f"app/Madeira/{OLD}.swift" not in paths, f"git tracks no {OLD}.swift")
    naming = []
    for rel in paths:
        f = R / rel
        try:
            if not f.is_file() or f.stat().st_size > 4_000_000:
                continue
            text = f.read_bytes().decode("utf-8", errors="ignore")
        except OSError:
            continue
        if OLD in text or OLD_SHEET in text:
            naming.append(rel)
    check(not naming, f"nothing outside docs/ names {OLD} or its setup sheet", naming[:20])
else:
    print("SKIP: no git for the tree-wide name search")

# ---- threads: element handlers only, state on the main thread -------------------------------
hub_code = code(hub)
assigned = re.findall(r"([A-Za-z_][\w.?]*?)\??\.(valueChangedHandler|pressedChangedHandler|touchedChangedHandler)\s*=", hub_code)
check(assigned and {r for r, _ in assigned} == {"input"},
      "the hub assigns handlers only to elements (the `input` of its button, trigger, stick and D-pad lists)", assigned)
check("-> [(GCControllerButtonInput, BCDPadControl)]" in hub and "-> [(GCControllerDirectionPad, BCDPadControl)]" in hub
      and "[(pad.leftTrigger, .leftTrigger), (pad.rightTrigger, .rightTrigger)]" in hub
      and "[(pad.dpad, .dpad), (pad.leftThumbstick, .leftStick), (pad.rightThumbstick, .rightStick)]" in hub,
      "those lists hold buttons, triggers, sticks and the D-pad, never the gamepad profile")
check("handlerQueue" not in hub_code, "the hub never touches a pad's handler queue")
gamepad = (APP / "GamepadInput.swift").read_text()
check("controller.handlerQueue = queue" in gamepad and "profile.valueChangedHandler = { [weak self] _, _ in" in gamepad,
      "GamepadInput still sets its handler queue and the profile's own handler (XInput as before)")
hub_class = block(hub, "@MainActor final class BCDControllerHub: ObservableObject")
tester_class = block(hub, "@MainActor final class BCDPadTester: ObservableObject")
check(hub.count("@Published") == hub_class.count("@Published") + tester_class.count("@Published")
      and hub_class.count("@Published") >= 4 and tester_class.count("@Published") == 2,
      "every observable value is a @Published of the main-actor hub or of its main-actor tester")
check("let tester = BCDPadTester()" in hub_class and "BCDPadTesterCard(tester: hub.tester" in screen
      and "BCDPadTableCard(tester: hub.tester" in screen and "@ObservedObject var tester: BCDPadTester" in screen,
      "the tester's values live apart from the hub: the status chips do not redraw while a tester runs")
flush = block(hub, "private func flush()")
check("DispatchQueue.main.asyncAfter" in block(hub, "private func queueFlush()")
      and "MainActor.assumeIsolated { hub?.receive(value) }" in flush,
      "the input side hands the tester's values to the hub on the main queue")
observers = re.findall(r"addObserver\(forName: [^)]*?queue: (\S+?)\)", hub_code)
check(observers and set(observers) == {".main"} and hub_code.count("addObserver(") == len(observers),
      "every notification the hub watches arrives on the main queue", observers)

# ---- the engine side ------------------------------------------------------------------------
check("case .key(let vk, let down): winios_post_key(vk, down ? 1 : 0)" in live
      and "case .mouse(let flags): winios_pointer(0, 0, flags, 0)" in live,
      "keys go out with winios_post_key, mouse buttons with winios_pointer(0, 0, flags, 0)")
check('(MadeiraConfig.get("env.MADEIRA_XINPUT"), ProcessInfo.processInfo.environment["MADEIRA_XINPUT"])' in live,
      "XInput's setting: bcd.cfg's env.MADEIRA_XINPUT, then the environment's MADEIRA_XINPUT")
check("accepting: { wine_process_is_running() != 0 && !LibraryController.shared.ownsInput }" in live,
      "keys go to Windows only while it runs and no BCD menu holds the controller")
check('LogStore.shared.log(line, level: success ? .success : .info)' in live, "log lines at the OK or info level")
check('static let mappingKey = "bcd.controllers.mapping"' in hub, "the mapping switch is remembered under its own new key")

# ---- call sites -----------------------------------------------------------------------------
content = (APP / "ContentView.swift").read_text()
app_file = (APP / "MadeiraApp.swift").read_text()
# Wave 3 (2026-10-19): the developer interface's controller badge and button (ContentView) and the old home
# screen's chip (HomeView) are deleted with those interfaces; BCD's shell shows the lead controller in its
# status chips and Settings › Controls › Controllers opens BCD's screen (checked below).
check("BCDControllerHub" not in content and "showControllerSheet" not in content
      and "BCDControllerHub.shared.start(.live)" in app_file,
      "ContentView carries no controller badge or button any more; the hub is started by MadeiraApp")
start_at = app_file.find("BCDControllerHub.shared.start(.live)")
check(0 < app_file.find("GamepadInput.shared.start()") < start_at, "MadeiraApp starts the hub at launch, after GamepadInput")
check("init(hub: BCDControllerHub? = nil, focus: BCDFocus? = nil, close: (() -> Void)? = nil)" in screen
      and "_hub = ObservedObject(wrappedValue: hub ?? .shared)" in screen,
      "BCDControllerScreen has a simple initializer (the shared hub by default)")
settings_root = (APP / "BCDSettings.swift").read_text()
settings_sections = (APP / "BCDSettingsSections.swift").read_text()
check("BCDControllerScreen(focus: focus, close: { model.hideControllers() })" in settings_root
      and "model.showControllers()" in settings_sections and 'title: "Controllers"' in settings_sections,
      "Settings › Controls › Controllers opens BCD's screen inside Settings, on Settings' own focus (a controller walks its switches)")
for control in ("controllers.mapping", "controllers.watch"):
    check(f'"{control}"' in screen, f"the screen's {control} switch is reachable by the controller")
check("hub.watchTester(true)" in screen and "hub.watchTester(false)" in screen,
      "the screen feeds the tester only while it is on screen")
ci = R / ".github/workflows/build-ipa.yml"
if ci.exists():
    check("run: python3 tests/host/check-controller-hub.py --require-swift" in ci.read_text(), "CI runs this check")

# ---- behaviour ------------------------------------------------------------------------------
SWIFTC = os.environ.get("SWIFTC") or shutil.which("swiftc")
pure = hub[hub.index("// MARK: - Mapping rules (pure"):hub.index("// MARK: - Input from GameController")]

RULES = r'''
var failed = 0
func expect(_ cond: Bool, _ what: String) { print((cond ? "PASS: " : "FAIL: ") + what); if !cond { failed += 1 } }
func at(_ degrees: Double, _ r: Double = 1) -> (Float, Float) {
    (Float(sin(degrees * Double.pi / 180) * r), Float(cos(degrees * Double.pi / 180) * r))
}
let order: [BCDPadDirection] = [.up, .upRight, .right, .downRight, .down, .downLeft, .left, .upLeft]
// Sectors: 45 degrees each, centred on up, up-right, right... clockwise.
var sectorsOK = true
for degree in stride(from: 0.25, to: 360.0, by: 1.0) {
    let (x, y) = at(degree)
    let want = order[Int((degree + 22.5) / 45) % 8]
    if BCDPadRules.direction(x: x, y: y) != want { sectorsOK = false; print("  \(degree)°: \(String(describing: BCDPadRules.direction(x: x, y: y)))") }
}
expect(sectorsOK, "eight 45° sectors centred on up, up-right, right, down-right, down, down-left, left, up-left")
for (i, d) in order.enumerated() {
    let (x, y) = at(Double(i) * 45)
    expect(BCDPadRules.direction(x: x, y: y) == d, "the centre of \(d.title) is \(d.title)")
}
expect(BCDPadRules.direction(x: at(22.4).0, y: at(22.4).1) == .up && BCDPadRules.direction(x: at(22.6).0, y: at(22.6).1) == .upRight,
       "the edge between up and up-right lies at 22.5°")
expect(BCDPadRules.direction(x: 0, y: 0.2199) == nil && BCDPadRules.direction(x: 0, y: 0.2201) == .up
       && BCDPadRules.direction(x: 0.155, y: 0.155) == nil && BCDPadRules.direction(x: -0.16, y: -0.16) == .downLeft,
       "at rest while the deflection is under 0.22, in any direction")
expect(BCDPadRules.direction(x: .nan, y: 1) == nil && BCDPadRules.direction(x: .infinity, y: 0) == nil, "values that are not numbers rest")
// The key sets of all eight directions and rest.
let W: Int32 = 0x57, A: Int32 = 0x41, S: Int32 = 0x53, D: Int32 = 0x44
let UP: Int32 = 0x26, LEFT: Int32 = 0x25, DOWN: Int32 = 0x28, RIGHT: Int32 = 0x27
let wasd: [BCDPadDirection?: Set<Int32>] = [.up: [W], .upRight: [W, D], .right: [D], .downRight: [S, D], .down: [S],
                                            .downLeft: [S, A], .left: [A], .upLeft: [W, A], nil: []]
let arrows: [BCDPadDirection?: Set<Int32>] = [.up: [UP], .upRight: [UP, RIGHT], .right: [RIGHT], .downRight: [DOWN, RIGHT],
                                              .down: [DOWN], .downLeft: [DOWN, LEFT], .left: [LEFT], .upLeft: [UP, LEFT], nil: []]
for (control, table) in [(BCDPadControl.leftStick, wasd), (.rightStick, arrows), (.dpad, arrows)] {
    for (direction, keys) in table {
        var m = BCDPadMapper()
        let (x, y) = direction.map { at(Double(order.firstIndex(of: $0)!) * 45) } ?? (0, 0)
        let pressed = m.aim(control, x: x, y: y)
        let released = m.aim(control, x: 0, y: 0)
        let downs = Set(pressed.compactMap { if case .key(let k, true) = $0 { return k } else { return nil } })
        let ups = Set(released.compactMap { if case .key(let k, false) = $0 { return k } else { return nil } })
        expect(downs == keys && pressed.count == keys.count && ups == keys && released.count == keys.count,
               "\(control.rawValue) \(direction?.title ?? "at rest"): holds \(keys.sorted()) and lets them go at rest")
    }
}
// Releases first, then presses; only what changed.
var m = BCDPadMapper()
expect(m.aim(.leftStick, x: 0, y: 1) == [.key(W, down: true)], "up: W down")
expect(m.aim(.leftStick, x: 0, y: 0.9) == [], "the same sector again sends nothing")
expect(m.aim(.leftStick, x: 0.7, y: 0.7) == [.key(D, down: true)], "up to up-right: D down, W stays")
expect(m.aim(.leftStick, x: 1, y: 0) == [.key(W, down: false)], "up-right to right: W up, D stays")
expect(m.aim(.leftStick, x: -1, y: 0) == [.key(D, down: false), .key(A, down: true)], "right to left: D up first, then A down")
_ = m.aim(.leftStick, x: 0.7, y: 0.7)
expect(m.aim(.leftStick, x: -0.7, y: -0.7) == [.key(W, down: false), .key(D, down: false), .key(S, down: true), .key(A, down: true)],
       "up-right to down-left: W and D up, then S and A down")
expect(m.aim(.leftStick, x: 0.05, y: 0) == [.key(S, down: false), .key(A, down: false)], "back to rest: everything up")
// Each source on its own; keys two sources share.
m = BCDPadMapper()
_ = m.aim(.leftStick, x: 0, y: 1); _ = m.aim(.dpad, x: 0, y: 1)
expect(m.aim(.rightStick, x: 0, y: 1) == [], "the right stick up while the D-pad holds Up: Up is already down")
expect(m.aim(.dpad, x: 0, y: 0) == [], "the D-pad lets go: Up stays, the right stick still holds it")
expect(m.aim(.rightStick, x: 0, y: 0) == [.key(UP, down: false)], "the right stick lets go: Up goes up")
expect(m.aim(.dpad, x: 1, y: 0) == [.key(RIGHT, down: true)] && m.pointing[BCDPadMapper.Slot(pad: 0, control: .leftStick)] == .up,
       "the D-pad moving leaves the left stick's W alone")
m = BCDPadMapper()
_ = m.aim(.leftStick, x: 0, y: 1, pad: 0)
expect(m.aim(.leftStick, x: 0.03, y: -0.02, pad: 1) == [], "a second pad's resting stick does not let go of the first's keys")
expect(m.aim(.leftStick, x: 0, y: 1, pad: 1) == [] && m.aim(.leftStick, x: 0, y: 0, pad: 0) == [],
       "two pads holding W: the first letting go keeps W down")
expect(m.forget(pad: 1) == [.key(W, down: false)], "a pad that leaves lets go of what it held")
// Buttons.
let keys: [BCDPadControl: Int32] = [.a: 0x20, .b: 0x1B, .x: 0x45, .y: 0x52, .leftShoulder: 0x10, .rightShoulder: 0x09,
                                    .leftStickClick: 0x11, .rightStickClick: 0x46, .menu: 0x1B, .options: 0x0D]
expect(BCDPadRules.buttonKeys == keys, "the buttons' keys: Space, Esc, E, R, Shift, Tab, Ctrl, F, Esc, Enter")
for (control, key) in keys {
    var b = BCDPadMapper()
    let down = b.button(control, pressed: true), again = b.button(control, pressed: true), up = b.button(control, pressed: false)
    expect(down == [.key(key, down: true)] && again == [] && up == [.key(key, down: false)] && b.button(control, pressed: false) == [],
           "\(control.rawValue): key 0x\(String(key, radix: 16)) down when pressed, up when let go, once each")
}
var b = BCDPadMapper()
expect(b.button(.home, pressed: true) == [] && b.button(.leftTrigger, pressed: true) == [], "Home sends nothing; triggers are not buttons")
expect(b.button(.b, pressed: true) == [.key(0x1B, down: true)] && b.button(.menu, pressed: true) == []
       && b.button(.b, pressed: false) == [] && b.button(.menu, pressed: false) == [.key(0x1B, down: false)],
       "B and Menu share Esc: it goes up when both let go")
// Triggers.
var t = BCDPadMapper()
expect(t.trigger(.leftTrigger, value: 0.3, pressed: false) == [], "the left trigger at 0.3: not yet")
expect(t.trigger(.leftTrigger, value: 0.31, pressed: false) == [.mouse(0x0008)], "above 0.3: right button down (0x0008)")
expect(t.trigger(.leftTrigger, value: 0.9, pressed: true) == [], "still down: nothing new")
expect(t.trigger(.leftTrigger, value: 0.1, pressed: false) == [.mouse(0x0010)], "back under: right button up (0x0010)")
expect(t.trigger(.leftTrigger, value: 0.05, pressed: true) == [.mouse(0x0008)], "a trigger reporting itself pressed is down below 0.3")
expect(t.trigger(.leftTrigger, value: 0, pressed: false) == [.mouse(0x0010)], "and up again")
expect(t.trigger(.rightTrigger, value: 0.5, pressed: false) == [.mouse(0x0002)]
       && t.trigger(.rightTrigger, value: 0.2, pressed: false) == [.mouse(0x0004)],
       "the right trigger is the left button (0x0002 down, 0x0004 up)")
// Everything up at once.
var all = BCDPadMapper()
_ = all.aim(.leftStick, x: 0, y: 1); _ = all.button(.a, pressed: true); _ = all.trigger(.rightTrigger, value: 1, pressed: true)
_ = all.aim(.dpad, x: -1, y: 0)
expect(all.releaseAll() == [.key(0x20, down: false), .key(LEFT, down: false), .key(W, down: false), .mouse(0x0004)]
       && !all.holding && all.releaseAll() == [], "releasing everything sends each held key and button up once")
// The switch's default from XInput's setting.
func xinput(_ c: String?, _ e: String?) -> Bool { BCDPadRules.xinput(configured: c, environment: e) }
expect(xinput(nil, nil) && xinput("1", nil) && xinput("", nil) && xinput(nil, "1") && !xinput("0", nil) && !xinput(nil, "0")
       && xinput("1", "0") && !xinput("0", "1"),
       "XInput is on unless its setting, bcd.cfg's first, else the environment's, is exactly \"0\"")
expect(BCDPadRules.mappingDefault(xinput: true) == false && BCDPadRules.mappingDefault(xinput: false) == true,
       "keys and mouse are on by default only while XInput is off")
// What the screen and the tester show.
expect(BCDPadRules.sends(.leftStick) == ["W", "A", "S", "D"] && BCDPadRules.sends(.rightStick) == ["↑", "←", "↓", "→"]
       && BCDPadRules.sends(.a) == ["Space"] && BCDPadRules.sends(.options) == ["Enter"] && BCDPadRules.sends(.home) == nil
       && BCDPadRules.sends(.leftTrigger) == ["Right click"] && BCDPadRules.sends(.rightTrigger) == ["Left click"],
       "the screen's table follows the rules")
expect(Set(BCDPadRules.tableOrder) == Set(keys.keys).union([.leftStick, .rightStick, .dpad, .leftTrigger, .rightTrigger])
       && BCDPadRules.tableOrder.count == 15, "the table lists every mapped control once")
expect(BCDPadRules.dpadWords(x: 0.05, y: -0.1) == nil && BCDPadRules.dpadWords(x: 1, y: 0) == "D-pad right · x 1.00 y 0.00"
       && BCDPadRules.dpadWords(x: -0.15, y: 0) == "D-pad left · x -0.15 y 0.00",
       "the tester names the D-pad with its values once it moves beyond 0.1")
expect(BCDPadControl.a.title(.playStation) == "Cross" && BCDPadControl.a.title(.xbox) == "A"
       && BCDPadControl.leftTrigger.title(.playStation) == "L2", "buttons carry their controller's names")
exit(failed == 0 ? 0 : 1)
'''

HUBTEST = r'''
import Combine
import Foundation
import GameController

var failed = 0
func expect(_ cond: Bool, _ what: String) { print((cond ? "PASS: " : "FAIL: ") + what); if !cond { failed += 1 } }

final class Record: @unchecked Sendable {
    let lock = NSLock()
    var outputs: [BCDPadOutput] = []
    var logs: [String] = []
    var accepting = true
    func take() -> [BCDPadOutput] { lock.lock(); defer { outputs = []; lock.unlock() }; return outputs }
}
let record = Record()
func outlet(_ xinput: String?) -> BCDControllerOutlet {
    BCDControllerOutlet(
        send: { output in record.lock.lock(); record.outputs.append(output); record.lock.unlock() },
        accepting: { record.lock.lock(); defer { record.lock.unlock() }; return record.accepting },
        log: { line, success in record.lock.lock(); record.logs.append((success ? "OK " : "INFO ") + line); record.lock.unlock() },
        xinputSetting: { (xinput, nil) })
}
func fresh() -> UserDefaults { UserDefaults(suiteName: "check-controller-hub-" + UUID().uuidString)! }
func spin(_ seconds: Double = 0.12) { RunLoop.main.run(until: Date().addingTimeInterval(seconds)) }

@MainActor func run() {
    // The switch's default and memory.
    let plain = fresh()
    let withXInput = BCDControllerHub(outlet: outlet(nil), defaults: plain)
    expect(withXInput.xinput && !withXInput.mapping, "XInput on (no setting): keys and mouse off")
    expect(BCDControllerHub(outlet: outlet("0"), defaults: fresh()).mapping, "XInput off (\"0\"): keys and mouse on")
    let stored = fresh()
    stored.set(true, forKey: BCDControllerHub.mappingKey)
    expect(BCDControllerHub(outlet: outlet(nil), defaults: stored).mapping, "a stored choice wins over the default")
    withXInput.setMapping(true)
    expect(plain.object(forKey: BCDControllerHub.mappingKey) as? Bool == true, "switching it is remembered")

    // A pad the way GamepadInput leaves it: its own serial handler queue and the profile's handler.
    let controller = GCController.withExtendedGamepad()
    let pad = controller.extendedGamepad!
    let queue = DispatchQueue(label: "check.gamepad")
    controller.handlerQueue = queue
    let profileCalls = Record()
    pad.valueChangedHandler = { _, _ in profileCalls.lock.lock(); profileCalls.logs.append("profile"); profileCalls.lock.unlock() }

    let hub = BCDControllerHub(outlet: outlet("0"), defaults: fresh())
    var changes = 0, offMain = 0, hubChanges = 0
    let sink = hub.objectWillChange.sink { _ in changes += 1; hubChanges += 1; if !Thread.isMainThread { offMain += 1 } }
    let testerSink = hub.tester.objectWillChange.sink { _ in changes += 1; if !Thread.isMainThread { offMain += 1 } }
    hub.watchTester(true)
    hub.attach(controller)
    hub.attach(controller)
    expect(hub.pads.count == 1 && hub.connected && hub.leadName == "ExtendedGamepad", "the pad is reported with its vendor name")
    expect(record.logs.filter { $0.contains("connected: ExtendedGamepad") } == ["OK [controllers] connected: ExtendedGamepad"],
           "one log line for the connection, at the OK level")
    pad.valueChangedHandler?(pad, pad.buttonA)
    expect(profileCalls.logs.count == 1, "the profile's own handler is still the one GamepadInput set")
    expect(controller.handlerQueue === queue, "the pad's handler queue is left alone")
    expect(pad.buttonA.pressedChangedHandler != nil && pad.rightTrigger.valueChangedHandler != nil
           && pad.leftThumbstick.valueChangedHandler != nil && pad.dpad.valueChangedHandler != nil,
           "the hub listens to the buttons, triggers, sticks and D-pad")
    _ = record.take()

    // Values set here reach the hub's handlers on the pad's queue, as a real pad's do.
    func on(_ work: () -> Void) -> [BCDPadOutput] { work(); queue.sync {}; return record.take() }
    let buttons: [(GCControllerButtonInput?, Int32, String)] = [
        (pad.buttonA, 0x20, "A"), (pad.buttonB, 0x1B, "B"), (pad.buttonX, 0x45, "X"), (pad.buttonY, 0x52, "Y"),
        (pad.leftShoulder, 0x10, "Left shoulder"), (pad.rightShoulder, 0x09, "Right shoulder"),
        (pad.leftThumbstickButton, 0x11, "Left stick click"), (pad.rightThumbstickButton, 0x46, "Right stick click"),
        (pad.buttonMenu, 0x1B, "Menu"), (pad.buttonOptions, 0x0D, "Select")]
    for (input, key, name) in buttons {
        guard let input else { expect(false, "\(name) exists on the snapshot pad"); continue }
        let down = on { input.setValue(1) }, up = on { input.setValue(0) }
        expect(down == [.key(key, down: true)] && up == [.key(key, down: false)], "\(name) on the pad's queue: key 0x\(String(key, radix: 16)) down, then up")
    }
    expect(on { pad.buttonHome?.setValue(1); pad.buttonHome?.setValue(0) } == [], "Home sends nothing")
    expect(on { pad.leftThumbstick.setValueForXAxis(0, yAxis: 1) } == [.key(0x57, down: true)], "left stick up: W")
    expect(on { pad.leftThumbstick.setValueForXAxis(0, yAxis: -1) } == [.key(0x57, down: false), .key(0x53, down: true)],
           "left stick up to down: W up, then S down")
    expect(on { pad.leftThumbstick.setValueForXAxis(0, yAxis: 0) } == [.key(0x53, down: false)], "left stick at rest: S up")
    expect(on { pad.rightTrigger.setValue(0.8) } == [.mouse(0x0002)] && on { pad.rightTrigger.setValue(0) } == [.mouse(0x0004)],
           "right trigger: left button down, then up")
    expect(on { pad.dpad.setValueForXAxis(1, yAxis: 0) } == [.key(0x27, down: true)] && on { pad.dpad.setValueForXAxis(0, yAxis: 0) } == [.key(0x27, down: false)],
           "D-pad right: the right arrow")

    // The tester, on the main thread.
    let hubBefore = hubChanges
    _ = on { pad.buttonA.setValue(1); pad.leftTrigger.setValue(0.5); pad.rightThumbstick.setValueForXAxis(0.25, yAxis: -0.5) }
    spin()
    let r = hub.tester.reading
    expect(hub.tester.live && r.last == "Left trigger" && r.held.contains(.a) && r.leftTrigger == 0.5
           && r.rightX == 0.25 && r.rightY == -0.5, "the tester shows the sticks, the triggers and the last press")
    _ = on { pad.buttonA.setValue(0); pad.leftTrigger.setValue(0); pad.rightThumbstick.setValueForXAxis(0, yAxis: 0) }
    spin()
    expect(changes > hubChanges && offMain == 0, "every change the screens observe happens on the main thread (\(changes) changes)")
    expect(hubChanges == hubBefore, "the tester's values do not redraw what observes only the hub")
    hub.watchTester(false)
    expect(!hub.tester.live && hub.tester.reading == BCDPadReading(), "nothing is kept for the tester once it leaves the screen")
    _ = record.take()

    // Windows not taking keys; the switch; a pad that leaves.
    expect(on { pad.buttonX.setValue(1) } == [.key(0x45, down: true)], "X held")
    record.lock.lock(); record.accepting = false; record.lock.unlock()
    expect(on { pad.buttonA.setValue(1) } == [.key(0x45, down: false)], "Windows stops taking keys: what was held goes up, the new press is dropped")
    expect(on { pad.buttonA.setValue(0); pad.buttonX.setValue(0) } == [], "and nothing goes up twice")
    record.lock.lock(); record.accepting = true; record.lock.unlock()
    expect(on { pad.buttonY.setValue(1) } == [.key(0x52, down: true)], "taking keys again: presses go out")
    hub.setMapping(false)
    expect(record.take() == [.key(0x52, down: false)], "turning the mapping off lets go of what it held")
    expect(on { pad.buttonY.setValue(0); pad.buttonB.setValue(1) } == [], "mapping off: no keys")
    hub.setMapping(true)
    _ = on { pad.buttonB.setValue(0) }
    expect(on { pad.buttonB.setValue(1) } == [.key(0x1B, down: true)], "mapping on again")
    hub.detach(controller)
    expect(record.take() == [.key(0x1B, down: false)], "a pad that leaves lets go of what it held")
    expect(pad.buttonA.pressedChangedHandler == nil && pad.dpad.valueChangedHandler == nil && pad.leftTrigger.valueChangedHandler == nil,
           "its handlers are given back")
    pad.valueChangedHandler?(pad, pad.buttonA)
    expect(profileCalls.logs.count == 2 && controller.handlerQueue === queue, "GamepadInput's handler and queue are still there")
    expect(hub.pads.isEmpty && record.logs.last == "INFO [controllers] disconnected: ExtendedGamepad",
           "one log line for the disconnection, at the info level")

    // The enabled switch.
    hub.attach(controller)
    hub.watchTester(true)
    hub.setOn(false)
    expect(hub.pads.isEmpty && !hub.connected && !hub.tester.live && pad.buttonA.pressedChangedHandler == nil,
           "off: no controller reported, the tester off, the handlers given back")
    hub.attach(controller)
    expect(hub.pads.isEmpty, "off: a connecting pad is not taken")
    hub.setOn(true)
    expect(hub.isOn && hub.tester.live, "on again: it looks for controllers again")
    withExtendedLifetime((sink, testerSink)) {}
}

MainActor.assumeIsolated { run() }
exit(failed == 0 ? 0 : 1)
'''

if not SWIFTC:
    print("SKIP: no swiftc for the behaviour checks")
    if args.require_swift:
        check(False, "swiftc is required (--require-swift)")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-controller-hub-") as tmp:
        folder = Path(tmp)
        home_dir = folder / "home"
        home_dir.mkdir()
        env = dict(os.environ, HOME=str(home_dir), CFFIXED_USER_HOME=str(home_dir))
        (folder / "rules.swift").write_text("import Foundation\n" + pure + "\n" + RULES)
        build = subprocess.run([SWIFTC, "-swift-version", "5", "-o", str(folder / "rules"), str(folder / "rules.swift")],
                               capture_output=True, text=True)
        check(build.returncode == 0, "the mapping rules compile alone on the host", build.stderr[-3000:])
        if build.returncode == 0:
            run = subprocess.run([str(folder / "rules")], env=env, capture_output=True, text=True)
            sys.stdout.write(run.stdout)
            check(run.returncode == 0, "the mapping rules: sectors, key sets, order, sources, buttons, triggers, default",
                  run.stderr[-2000:])
        (folder / "main.swift").write_text(HUBTEST)
        build = subprocess.run([SWIFTC, "-swift-version", "5", "-o", str(folder / "hub"), str(APP / HUB), str(folder / "main.swift")],
                               capture_output=True, text=True)
        if build.returncode != 0 and "no such module 'GameController'" in build.stderr:
            print("SKIP: no GameController on this host for the whole hub")
            if args.require_swift:
                check(False, "GameController is required (--require-swift)")
        else:
            check(build.returncode == 0, "the whole hub compiles on the host", build.stderr[-3000:])
            if build.returncode == 0:
                run = subprocess.run([str(folder / "hub")], env=env, capture_output=True, text=True, timeout=120)
                sys.stdout.write(run.stdout)
                check(run.returncode == 0, "the hub with snapshot pads: handlers, threads, keys, log lines, switches",
                      run.stderr[-2000:])

# ---- build: the preview -----------------------------------------------------------------------
if args.skip_preview:
    print("SKIP: the preview build (--skip-preview)")
elif not shutil.which("xcrun"):
    print("SKIP: no xcrun for the preview build")
    if args.require_swift:
        check(False, "xcrun is required for the preview build (--require-swift)")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-controller-hub-preview-") as tmp:
        run = subprocess.run([sys.executable, str(R / "tools/ui-preview.py"), "--build-only", "--out", tmp],
                             capture_output=True, text=True)
        check(run.returncode == 0, "the preview build compiles (tools/ui-preview.py --build-only)",
              (run.stdout + run.stderr)[-3000:])

print("check-controller-hub:", "FAIL" if failures else "PASS")
sys.exit(1 if failures else 0)
