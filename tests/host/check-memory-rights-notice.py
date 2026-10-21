#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""An install without the increased memory limit is told, never blocked (app/Madeira/MemoryRights.swift).

Owner, 2026-10-21: no hard block (simple games run without the limit: Doom 3, GTA V Legacy, Moonlighter and
Tomb Raider: Underworld got in game in the 89 logs of such installs); a small box with OK once per build over the
library, and the same advice where a log is sent (Settings › Logs and a game's page). Source invariants: the
condition, once per build, OK only, never during a game, the advice at both send points, nothing refused, and the
layering (the engine-side file is not a BCD*.swift screen file; the screens get the text through their models).
"""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
app = root / "app/Madeira"
failures = 0


def code(text):
    """The text without // comments (a comment may name a file; only code counts)."""
    return "\n".join(l.split("//", 1)[0] for l in text.splitlines())


def check(ok, label):
    global failures
    print(("PASS: " if ok else "FAIL: ") + label)
    failures += 0 if ok else 1


rights = (app / "MemoryRights.swift").read_text()
front = (app / "BCDLiveFrontEnd.swift").read_text()
page = (app / "BCDGamePage.swift").read_text()
page_live = (app / "BCDGamePageLive.swift").read_text()
sections = (app / "BCDSettingsSections.swift").read_text()
settings_live = (app / "BCDSettingsLive.swift").read_text()
pbx = (root / "app/Madeira.xcodeproj/project.pbxproj").read_text()

check("static let lacking: Bool = !EntitlementStatus.check().increasedMemory" in rights,
      "the condition is the increased-memory entitlement (it matches the profile line on all 427 logged installs)")
check("lacking && build != nil && shownBuild != build" in rights and "UserDefaults.standard.set(build, forKey: MemoryRights.shownBuildKey)" in rights,
      "the box is due once per build, only when the limit is lacking")
check("[memory-rights]" in rights, "the log says it was told")
box = front[front.index('.alert("Memory limit"'):front.index('.alert("Update available"')]
check("library.current == nil" in box and "!onboarding.presented" in box and box.count("Button(") == 1
      and 'Button("OK", role: .cancel)' in box, "the box waits for the library and has OK alone")
check("memory.start()" in front[front.index(".onAppear {"):front.index(".onChange(of: scenePhase)")], "it starts when the library appears")
check("details.sendLogAdvice" in page and "var sendLogAdvice: String? = nil" in page
      and "d.sendLogAdvice = MemoryRights.lacking ? MemoryRights.sendLogAdvice : nil" in page_live,
      "a game's page says it before a log is sent")
check("backend.sendLogAdvice" in sections and "var sendLogAdvice: String? { nil }" in sections
      and "var sendLogAdvice: String? { MemoryRights.lacking ? MemoryRights.sendLogAdvice : nil }" in settings_live,
      "Settings › Logs says it before a log is sent")
check(not any(w in code(rights) for w in ("refuse", "throw", "return false")) and "MemoryRights" not in
      code((app / "ContentView.swift").read_text()), "nothing is refused: no start path reads it")
check(not any("MemoryRights" in code(f.read_text()) for f in app.glob("BCD*.swift")
              if f.name not in ("BCDLiveFrontEnd.swift", "BCDGamePageLive.swift", "BCDSettingsLive.swift")),
      "only the engine-bound live files name it (the screens get text through their models)")
check(pbx.count("/* MemoryRights.swift in Sources */") == 2 and pbx.count("path = MemoryRights.swift;") == 1,
      "the Xcode project builds MemoryRights.swift")

if failures:
    print(f"check-memory-rights-notice: {failures} FAILED")
    sys.exit(1)
print("check-memory-rights-notice: all passed")
