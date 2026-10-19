#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""iOS's own gestures during play, two opt-in switches (app/Madeira/SystemGestures.swift).

gameplay.md sections 2.4 and 2.5 of the iOS 18 audit:
  - env.MADEIRA_PAD_SYSTEM_GESTURES = 1: a controller's Home and Create/Share (Options) buttons
    go to the game at once (preferredSystemGestureState .alwaysReceive) for the session, iOS
    gets them back (.enabled) when it ends; without the switch no controller element is touched;
  - env.MADEIRA_DEFER_EDGES = 1: edge swipes reach the game first while the on-screen controls
    are shown (defersSystemGestures); without the switch the game views are returned as they
    are and nothing observes the controls.
Checks the reads (only "1" turns either on; the game's own file first), the hooks (the session's
start in GamepadInput.beginPadSession, newly connected controllers, the end in
LibraryModel.finish, the two game views in ContentView), the Xcode project, the catalog and CI.
"""
from pathlib import Path
import re, sys

root = Path(__file__).resolve().parents[2]
app = root / "app/Madeira"
src = (app / "SystemGestures.swift").read_text()
gamepad = (app / "GamepadInput.swift").read_text()
lib = (app / "Library.swift").read_text()
content = (app / "ContentView.swift").read_text()
pbx = (root / "app/Madeira.xcodeproj/project.pbxproj").read_text()
catalog = (app / "ConfigCatalog.generated.swift").read_text()
workflow = (root / ".github/workflows/build-ipa.yml").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


def function(source, start):
    i = source.index(start)
    b = source.index("{", i)
    depth, k = 1, b + 1
    while depth:
        depth += (source[k] == "{") - (source[k] == "}")
        k += 1
    return source[i:k]


for key in ("MADEIRA_PAD_SYSTEM_GESTURES", "MADEIRA_DEFER_EDGES"):
    check(f"{key}: the game's own file, then madeira.cfg, then the environment; only \"1\" turns it on",
          f'MadeiraConfig.gameValue("env.{key}")' in src and f'MadeiraConfig.get("env.{key}")' in src
          and f'ProcessInfo.processInfo.environment["{key}"]) == "1"' in src)
    check(f"the catalog lists env.{key}, off by default",
          re.search(r'ConfigOption\(key: "env\.' + key + r'", [^\n]*kind: \.bool, defaultValue: "0"', catalog) is not None)

begin = function(src, "static func beginSession(enabled: Bool) {")
check("the session takes the buttons only with the switch (and controllers on)",
      "let take = enabled && padButtonsOptIn" in begin and "guard take else { return }" in begin
      and begin.index("guard take else { return }") < begin.index("taken = true"))
check("the buttons taken are Home and Options (XInput Guide and Back), to .alwaysReceive",
      "[pad.buttonHome, pad.buttonOptions].compactMap { $0 }" in src
      and "button.preferredSystemGestureState = .alwaysReceive" in function(src, "static func apply(_ controllers: [GCController]) {")
      and function(src, "static func apply(_ controllers: [GCController]) {").index("guard taken else { return }") > 0)
end = function(src, "static func endSession() {")
check("the end gives them back (.enabled), only when the session took them",
      "guard taken else { return }" in end and "button.preferredSystemGestureState = .enabled" in end)
check("hooks: the session's start, newly connected controllers, the end of a library session",
      "SystemGestures.beginSession(enabled: Self.enabled)" in function(gamepad, "@MainActor func beginPadSession() {")
      and "SystemGestures.apply(live.map { $0.0 })" in function(gamepad, "@MainActor private func refreshControllers() {")
      and "SystemGestures.endSession()" in function(lib, "private func finish() {"))
mod = function(src, "func body(content: Content) -> some View {")
check("edge deferral: without the switch the view is returned as it is",
      "if SystemGestures.deferEdgesOptIn && active {" in mod and "} else {\n            content\n        }" in mod)
check("with it, edges are deferred while the on-screen controls are shown",
      "content.defersSystemGestures(on: controls.visible ? .all : [])" in src
      and "@ObservedObject private var controls = TouchControlsModel.shared" in src)
# Wave 3 (2026-10-19): the developer interface's landscape view (DeferSystemEdges(active: landscape)) is deleted;
# the library session is the game's only view.
check("the game view carries it: the library session",
      content.count(".modifier(DeferSystemEdges(active: true))") == 1
      and content.count(".modifier(DeferSystemEdges(active: landscape))") == 0)
check("the Xcode project builds SystemGestures.swift",
      pbx.count("/* SystemGestures.swift in Sources */") == 2 and pbx.count("path = SystemGestures.swift;") == 1)
check("CI runs this check", "run: python3 tests/host/check-device-system-gestures.py" in workflow)

print("PASS: system gestures during play" if ok else "FAILED: system gestures during play")
sys.exit(0 if ok else 1)
