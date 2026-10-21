#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The once-a-day "a newer BCD is out" alert (app/Madeira/BCDUpdateCheck.swift, UpdateNotice.swift).

Owner, 2026-10-21: people still send logs from builds two or three releases old; say so at start, never block,
a small alert with OK, at most once a day. Part A compiles the production BCDUpdateCheck.swift (Foundation only)
and runs its rules: the source file's shape as tools/update-altsource.py writes it, garbage, the build compare,
the day key. Part B checks the source: the request (ephemeral, one host, size cap, delayed, once a day, switch),
the alert (only over the library, OK only), and that the AltStore generator still writes what the app reads.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
app = root / "app/Madeira"
failures = 0


def check(ok, label):
    global failures
    print(("PASS: " if ok else "FAIL: ") + label)
    failures += 0 if ok else 1


core = (app / "BCDUpdateCheck.swift").read_text()
notice = (app / "UpdateNotice.swift").read_text()
front = (app / "BCDLiveFrontEnd.swift").read_text()
altsource = (root / "tools/update-altsource.py").read_text()

# ---- Part A: the rules, compiled ------------------------------------------------------------
swiftc = shutil.which("swiftc")
harness = r'''
import Foundation
var failures = 0
func require(_ ok: Bool, _ label: String) { print((ok ? "PASS: " : "FAIL: ") + label); if !ok { failures += 1 } }
@main struct Checks {
    static func main() {
        typealias U = BCDUpdateCheck
        // The shape tools/update-altsource.py writes (make_source): apps[0].versions[0].
        let source = """
        {"name": "BCD", "apps": [{"name": "BCD", "bundleIdentifier": "x", "versions": [{"version": "0.1.579",
         "buildVersion": "579", "date": "2026-10-22T10:00:00Z", "localizedDescription": "notes", "size": 1}]}], "news": []}
        """
        let latest = U.latest(from: Data(source.utf8))
        require(latest == U.Release(build: 579, version: "0.1.579"), "the newest release from the source's first app and version")
        require(U.latest(from: Data("not json".utf8)) == nil, "garbage is no release")
        require(U.latest(from: Data(#"{"apps": []}"#.utf8)) == nil, "no app, no release")
        require(U.latest(from: Data(#"{"apps": [{"versions": [{"version": "1", "buildVersion": "abc"}]}]}"#.utf8)) == nil,
                "a build that is not a number is no release")
        require(U.latest(from: Data(repeating: 32, count: U.maxBytes + 1)) == nil, "an answer over the size cap is no release")
        let this = U.current(["CFBundleVersion": "578", "CFBundleShortVersionString": "0.1.578"])
        require(this == U.Release(build: 578, version: "0.1.578"), "this build from Info.plist")
        require(U.current(["CFBundleVersion": "$(CURRENT_PROJECT_VERSION)"]) == nil, "an unexpanded build says nothing")
        let text = U.notice(latest: latest!, current: this!)
        require(text?.contains("0.1.579") == true && text?.contains("0.1.578") == true, "an older build is told, with both versions")
        require(U.notice(latest: U.Release(build: 578, version: "0.1.578"), current: this!) == nil, "the same build is not told")
        require(U.notice(latest: U.Release(build: 577, version: "0.1.577"), current: this!) == nil, "a newer local build is not told")
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(identifier: "UTC")!
        let day = U.day(Date(timeIntervalSince1970: 1792540800), calendar: calendar)
        require(day == "2026-10-21", "the day key is the local calendar day: \(day)")
        require(U.due(lastDay: nil, today: day) && !U.due(lastDay: day, today: day) && U.due(lastDay: "2026-10-20", today: day),
                "due once a day: never asked, asked today, asked yesterday")
        exit(failures == 0 ? 0 : 1)
    }
}
'''
if swiftc:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "main.swift").write_text(harness)
        exe = t / "check"
        b = subprocess.run([swiftc, "-parse-as-library", "-o", str(exe), str(app / "BCDUpdateCheck.swift"), str(t / "main.swift")],
                           capture_output=True, text=True)
        check(b.returncode == 0, "BCDUpdateCheck.swift compiles on the host (Foundation only)")
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            sys.stdout.write(r.stdout)
            check(r.returncode == 0, "the update rules hold")
        else:
            sys.stdout.write(b.stderr[-3000:])
elif "--require-swift" in sys.argv:
    check(False, "swiftc is needed")
else:
    print("SKIP: no swiftc; the source checks below still run")

# ---- Part B: the source ------------------------------------------------------------------------
check('URL(string: "https://thebcd69.github.io/bcd-games/source.json")' in core, "it reads BCD's own AltStore source, nothing else")
check("URLSessionConfiguration.ephemeral" in core and "urlCache = nil" in core and "timeoutIntervalForResource = 30" in core
      and "http.url?.host == sourceURL.host" in core and "data.count <= maxBytes" in core,
      "the request: no cache, short timeouts, no other host, a size cap")
check('SteamSignIn.flag("MADEIRA_UPDATE_CHECK", default: true)' in notice, "env.MADEIRA_UPDATE_CHECK = 0 turns it off")
start = notice[notice.index("func start()"):]
check(start.index("guard !started") < start.index("BCDUpdateCheck.due(") < start.index("Task.detached(priority: .utility)")
      < start.index("Task.sleep(nanoseconds: 8_000_000_000)") < start.index("BCDUpdateCheck.fetch()"),
      "once per run, only when the day's check is due, off the main thread, a few seconds after the library appears")
check(start.index("UserDefaults.standard.set(today") > start.index("BCDUpdateCheck.latest(from: data)"),
      "the day is marked only after an answer (offline asks again at the next start)")
check("[update-check] latest=" in notice, "the log says what the check found")
alert = front[front.index('.alert("Update available"'):]
alert = alert[:alert.index("// First-run setup")]
check("library.current == nil" in alert and "!onboarding.presented" in alert and "library.error == nil" in alert
      and "steam.error == nil" in alert and "memory.message == nil" in alert,
      "the alert waits for the library: no game, no first-run setup, no other alert (the memory box first)")
check(alert.count("Button(") == 1 and 'Button("OK", role: .cancel)' in alert, "OK is its only button")
check("update.start()" in front[front.index(".onAppear {"):front.index(".onChange(of: scenePhase)")],
      "it starts when the library appears")
check('"buildVersion": info["CFBundleVersion"]' in altsource and '"version": info["CFBundleShortVersionString"]' in altsource,
      "the AltStore generator writes the build and version the app reads")

pbx = (root / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check(all(pbx.count(f"/* {f} in Sources */") == 2 and pbx.count(f"path = {f};") == 1 for f in ("BCDUpdateCheck.swift", "UpdateNotice.swift")),
      "the Xcode project builds both files")
check(not any(t in core for t in ("LogStore", "SteamSignIn", "MadeiraConfig")), "BCDUpdateCheck.swift names no engine type (the preview builds it)")

if failures:
    print(f"check-update-check: {failures} FAILED")
    sys.exit(1)
print("check-update-check: all passed")
