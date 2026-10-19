#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The device gate (app/Madeira/DeviceCapability.swift; gameplay.md section 1.1 of the iOS 18 audit).

A GPU with Tier 1 argument buffers (A12 and older) cannot run DXMT or the D3D12 runtime, and the
A10 cannot run the 64-bit emulator module (no LSE atomics, no LDAPR); such launches crashed with
no word why. Compiles the gate's own decision code with swiftc and checks the device matrix:
  - A14 and later: nothing refused, no notice, whatever the launch;
  - A13 (Tier 2, no Apple7): nothing refused, the one-time mesh-shader notice;
  - A12 (Tier 1): a game is refused with the GPU message, the Desktop is not;
  - A10 (Tier 1, no LSE/LRCPC): a 64-bit program gets the CPU message, a 32-bit game the GPU one,
    the Desktop starts;
  - env.MADEIRA_DEVICE_GATE = 0 refuses nothing; a CPU that does not answer is never refused;
  - the program explorer is asked to start is the first quoted path of its arguments.
And, from the sources: what the facts are read from (argument-buffer tier, Apple7, the two
sysctls; no GPU or no answer refuses nothing); the hooks in ContentView (library Play before JIT is
asked for; every launch after the JIT check and before the session counts; a refused Dock launch
leaves no request behind; the log line at start-up); the Xcode project; the catalog; CI.
"""
from pathlib import Path
import re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
app = root / "app/Madeira"
gate = (app / "DeviceCapability.swift").read_text()
content = (app / "ContentView.swift").read_text()
dock = (app / "MadeiraDock.swift").read_text()
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


# ---- sources ---------------------------------------------------------------------------------
check("Tier 2 argument buffers and Apple7 come from the GPU; without one nothing is refused",
      "tier2: gpu.map { $0.argumentBuffersSupport == .tier2 } ?? true" in gate
      and "apple7: gpu.map { $0.supportsFamily(.apple7) } ?? true" in gate)
check("LSE and LRCPC come from the kernel's FEAT sysctls; an unknown answer is nil and never refuses",
      'cpuFeature("hw.optional.arm.FEAT_LSE")' in gate and 'cpuFeature("hw.optional.arm.FEAT_LRCPC")' in gate
      and "guard sysctlbyname(name, &value, &size, nil, 0) == 0 else { return nil }" in gate
      and "var x64: Bool { lse != false && lrcpc != false }" in gate)
check("env.MADEIRA_DEVICE_GATE: the game's own file first, then madeira.cfg / the environment, on by default",
      'MadeiraConfig.gameValue("env.MADEIRA_DEVICE_GATE")' in gate and 'return MadeiraConfig.flag("MADEIRA_DEVICE_GATE")' in gate)
lib = function(gate, "static func libraryRefusal(_ entry: LibraryEntry) -> String? {")
check("library Play: a Steam game through BCD Dock is a 64-bit launch; the Desktop entry is the Desktop",
      "let dock = entry.steamAppID != nil && !entry.startsSteamGameDirectly" in lib
      and "let desktop = entry.desktop == true" in lib and "x64: dock ||" in lib)
any_launch = function(gate, "static func launchRefusal(desktopEntry: Bool, dock: Bool) -> String? {")
check("any launch: explorer without a program of its own is the Desktop, never a Dock launch",
      "let desktop = !dock && (desktopEntry || (viaExplorer && program == nil))" in any_launch
      and "x64: dock ||" in any_launch)
check("on an A13 or later device both entry points return before reading anything (no config, no PE header)",
      lib.startswith("static func libraryRefusal(_ entry: LibraryEntry) -> String? {\n        guard !facts.tier2 || !facts.x64 else { return nil }")
      and any_launch.startswith("static func launchRefusal(desktopEntry: Bool, dock: Bool) -> String? {\n        guard !facts.tier2 || !facts.x64 else { return nil }")
      and "guard let text = refusal(facts, launch, gateOn: true), gateOn else { return nil }" in gate)
check("a refusal is logged with the way out", '"[device] \\(via) refused (desktop=' in gate
      and "env.MADEIRA_DEVICE_GATE = 0 starts it anyway" in gate)
check("the start-up line names the family, the argument-buffer tier and the CPU features",
      '"[device] gpu=\\"' in gate and "argument-buffers=tier" in gate and "cpu-lse=" in gate and "cpu-lrcpc=" in gate)
check("the A13 notice is shown once, through the library's alert",
      "!UserDefaults.standard.bool(forKey: noticeKey)" in gate and "LibraryModel.shared.error = text" in gate)

play = function(content, "private func launchLibraryEntry(_ entry: LibraryEntry) {")
check("library Play asks the gate first, before the recommended settings and before JIT",
      play.index("DeviceCapability.libraryRefusal(entry)") < play.index("GameRecommendations.prepare(")
      and play.index("DeviceCapability.libraryRefusal(entry)") < play.index("jitReadyForLaunch("))
run = function(content, "private func runWineFullSequence(profile: LibraryEntry? = nil) {")
check("every launch: after the JIT check, before the session counts as started",
      run.index("guard jit_check_debugged() else {") < run.index("DeviceCapability.launchRefusal(")
      < run.index("Self.wineSequenceStarted = true"))
refuse = run[run.index("DeviceCapability.launchRefusal("):run.index("if Self.wineSequenceStarted {")]
check("a refusal clears a Dock request, returns the library and hides the starting screen (BCDStartingHost)",
      "_ = MadeiraDock.takeLaunchRequest()" in refuse and "LibraryModel.shared.launchFailed(refusal)" in refuse
      and "BCDStartingHost.hide()" in refuse and "dock: MadeiraDock.launchPending" in refuse)
check("MadeiraDock.launchPending reads the request without taking it",
      "static var launchPending: Bool { launchRequest.dock }" in dock)
check("the start-up line is logged after the device's own lines",
      "DeviceDiagnostics.logStartup()\n                DeviceCapability.logStartup()" in content)
check("the Xcode project builds DeviceCapability.swift",
      pbx.count("/* DeviceCapability.swift in Sources */") == 2 and pbx.count("path = DeviceCapability.swift;") == 1)
check("the settings catalog lists env.MADEIRA_DEVICE_GATE, on by default",
      re.search(r'ConfigOption\(key: "env\.MADEIRA_DEVICE_GATE", [^\n]*kind: \.bool, defaultValue: "1"', catalog) is not None)
check("CI runs this check", "run: python3 tests/host/check-device-gate.py" in workflow)

# ---- the decision, compiled -----------------------------------------------------------------
pure = gate[gate.index("enum DeviceCapability {"):gate.index("    // MARK: - This device")]
pure += "    " + function(gate, "static func quotedProgram(_ args: String) -> String? {") + "\n}\n"
tests = r'''
var failures = 0
func expect(_ c: Bool, _ what: String) { if !c { print("FAIL " + what); failures += 1 } }
typealias D = DeviceCapability
let a10 = D.Facts(tier2: false, apple7: false, lse: false, lrcpc: false)
let a12 = D.Facts(tier2: false, apple7: false, lse: true, lrcpc: true)
let a13 = D.Facts(tier2: true, apple7: false, lse: true, lrcpc: true)
let a14 = D.Facts(tier2: true, apple7: true, lse: true, lrcpc: true)
let unknown = D.Facts(tier2: true, apple7: true, lse: nil, lrcpc: nil)
let game = D.Launch(desktop: false, x64: false), game64 = D.Launch(desktop: false, x64: true)
let desktop = D.Launch(desktop: true, x64: false)
for l in [game, game64, desktop] {
    expect(D.refusal(a14, l, gateOn: true) == nil, "A14 and later: nothing refused")
    expect(D.refusal(a13, l, gateOn: true) == nil, "A13: nothing refused")
    expect(D.refusal(unknown, l, gateOn: true) == nil, "a CPU that does not answer is never refused")
    expect(D.refusal(a10, l, gateOn: false) == nil && D.refusal(a12, l, gateOn: false) == nil, "gate off: nothing refused")
}
expect(D.notice(a14) == nil, "A14: no notice")
expect(D.notice(a13) == D.meshNotice, "A13: the mesh-shader notice")
expect(D.notice(a12) == nil && D.notice(a10) == nil, "A12/A10: refused, not noticed")
expect(D.refusal(a12, game, gateOn: true) == D.gpuMessage, "A12: a game gets the GPU message")
expect(D.refusal(a12, game64, gateOn: true) == D.gpuMessage, "A12: a 64-bit game too (its CPU is fine)")
expect(D.refusal(a12, desktop, gateOn: true) == nil, "A12: the Desktop starts")
expect(D.refusal(a10, game64, gateOn: true) == D.cpuMessage, "A10: a 64-bit program gets the CPU message")
expect(D.refusal(a10, game, gateOn: true) == D.gpuMessage, "A10: a 32-bit game gets the GPU message")
expect(D.refusal(a10, desktop, gateOn: true) == nil, "A10: the Desktop starts")
expect(D.quotedProgram("/desktop=shell,1280x720 \"C:\\Games\\X\\x.exe\" -w") == "C:\\Games\\X\\x.exe", "the quoted program")
expect(D.quotedProgram("/desktop=shell,1280x720 C:\\windows\\system32\\services.exe") == nil, "no quoted program: none")
expect(D.quotedProgram("/desktop=shell,1280x720 \"\"") == nil, "an empty quote: none")
if failures == 0 { print("ok") } else { exit(1) }
'''
swiftc = shutil.which("swiftc")
if not swiftc:
    print("SKIP: no swiftc for the gate's decision (macOS CI runs it)")
else:
    with tempfile.TemporaryDirectory() as t:
        s = Path(t) / "main.swift"
        s.write_text("import Foundation\n" + pure + tests)
        exe = Path(t) / "gate"
        b = subprocess.run([swiftc, "-o", str(exe), str(s)], capture_output=True, text=True)
        check("the gate's decision compiles on its own", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-3000:])
        else:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
            if r.returncode:
                print(r.stdout[-3000:])
            check("the device matrix: A14+ untouched, A13 noticed, A12/A10 refused but the Desktop, gate off",
                  r.returncode == 0 and r.stdout.strip().endswith("ok"))

print("PASS: device gate" if ok else "FAILED: device gate")
sys.exit(0 if ok else 1)
