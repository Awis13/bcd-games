// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// The screen orientation lock's choice and rule, compiled on the host by tests/host/check-bcd-orientation.py with
// app/Madeira/BCDOrientation.swift (its UIKit part is left out on the Mac).

import Foundation

var failures = 0
func check(_ what: String, _ ok: Bool, _ detail: String = "") {
    print((ok ? "ok   " : "FAIL ") + what + (ok || detail.isEmpty ? "" : "\n     " + detail))
    if !ok { failures += 1 }
}

@main enum Run {
    static func main() {
        // Off is the default: no key, an empty key, a word nobody knows
        check("no setting follows the device", BCDOrientationChoice(setting: nil) == .free)
        check("an empty setting follows the device", BCDOrientationChoice(setting: "") == .free)
        check("a word this build does not know follows the device", BCDOrientationChoice(setting: "sideways") == .free)
        check("the word off follows the device", BCDOrientationChoice(setting: "off") == .free)
        check("landscape is landscape, in any case and with spaces", BCDOrientationChoice(setting: "  Landscape ") == .landscape)
        check("the two sides are known", BCDOrientationChoice(setting: "landscape-camera-left") == .landscapeCameraLeft
              && BCDOrientationChoice(setting: "landscape-camera-right") == .landscapeCameraRight)
        check("free is not locked, the rest are", !BCDOrientationChoice.free.locked && BCDOrientationChoice.allCases.filter(\.locked).count == 3)
        check("free is the key removed; the others are their words",
              BCDOrientationChoice.free.setting == nil && BCDOrientationChoice.landscape.setting == "landscape"
              && BCDOrientationChoice.landscapeCameraLeft.setting == "landscape-camera-left")
        check("a setting round-trips", BCDOrientationChoice.allCases.allSatisfy { BCDOrientationChoice(setting: $0.setting) == $0 })
        check("the choices offered are the four, follow-the-device first", BCDOrientationChoice.offered == [.free, .landscape, .landscapeCameraLeft, .landscapeCameraRight]
              && Set(BCDOrientationChoice.offered) == Set(BCDOrientationChoice.allCases))
        check("the labels are short plain English and all different",
              Set(BCDOrientationChoice.allCases.map(\.label)).count == 4 && BCDOrientationChoice.allCases.allSatisfy { $0.label.count <= 32 && !$0.label.contains("  ") }
              && BCDOrientationChoice.free.label == "Follow the device" && BCDOrientationChoice.landscape.label == "Landscape")

        // The rule: the game's own choice first, else Settings', else the device's
        let words: [String?] = [nil, "", "off", "landscape", "landscape-camera-left", "landscape-camera-right", "junk"]
        check("no choice anywhere is the device's", BCDOrientationRules.effective(global: nil, game: nil) == .free)
        check("no game choice takes Settings'", BCDOrientationRules.effective(global: "landscape", game: nil) == .landscape
              && BCDOrientationRules.effective(global: "landscape", game: "") == .landscape)
        check("a game's own choice beats Settings'", BCDOrientationRules.effective(global: "landscape", game: "landscape-camera-right") == .landscapeCameraRight
              && BCDOrientationRules.effective(global: nil, game: "landscape") == .landscape)
        check("a game's own off beats a Settings landscape", BCDOrientationRules.effective(global: "landscape", game: "off") == .free)
        check("a game word this build does not know falls back to Settings", BCDOrientationRules.effective(global: "landscape", game: "tilted") == .landscape
              && BCDOrientationRules.effective(global: nil, game: "tilted") == .free)
        var bad: [String] = []
        for g in words { for e in words {
            let want: BCDOrientationChoice
            if let e, !e.isEmpty, let own = BCDOrientationChoice(rawValue: e) { want = own } else { want = BCDOrientationChoice(setting: g) }
            if BCDOrientationRules.effective(global: g, game: e) != want { bad.append("\(g ?? "nil")/\(e ?? "nil")") }
        } }
        check("every pair of words gives the game's own choice, else Settings', else the device's", bad.isEmpty, bad.joined(separator: ", "))

        // The game page's list
        let none = BCDOrientationRules.gameChoices(global: nil)
        check("the page's first choice follows Settings and says what Settings has", none.first?.value == "" && none.first?.label == "As in Settings: follow the device",
              none.first.map { $0.label } ?? "")
        check("... and names the landscape when Settings has it", BCDOrientationRules.gameChoices(global: "landscape").first?.label == "As in Settings: landscape"
              && BCDOrientationRules.gameChoices(global: "landscape-camera-left").first?.label == "As in Settings: landscape, camera on the left")
        check("the rest are the four choices, by their words", none.dropFirst().map { $0.value } == ["off", "landscape", "landscape-camera-left", "landscape-camera-right"])
        check("the page's values are all distinct", Set(none.map { $0.value }).count == none.count)

        // The `[device] window:` line of a launch (log only)
        typealias F = BCDWindowReport.Facts
        func facts(scene: (Double, Double) = (1180, 820), screen: (Double, Double) = (1180, 820), fullScreen: Bool? = true, restrictions: Bool? = false,
                   scenes: Int = 1, screens: Int = 1, idiom: String = "pad", plist: Int = 3, requires: Bool = false, delegate: Int = 3) -> F {
            F(scene: scene, screen: screen, fullScreen: fullScreen, restrictions: restrictions, scenes: scenes, screens: screens, idiom: idiom,
              plistOrientations: plist, requiresFullScreen: requires, delegateOrientations: delegate)
        }
        check("the window line of a full-screen iPad: the scene fills the screen, three orientations, no full-screen key",
              BCDWindowReport.line(facts()) == "[device] window: scene=1180x820 pt screen=1180x820 pt (full size) full-screen=1 size-restrictions=0 scenes=1 screens=1 idiom=pad plist-orientations=3 requires-full-screen=0 delegate-orientations=3",
              BCDWindowReport.line(facts()))
        check("a scene smaller than the screen is a window", BCDWindowReport.line(facts(scene: (900, 640), fullScreen: false, restrictions: true)).contains("(a window) full-screen=0 size-restrictions=1"))
        check("a scene that is the screen turned is full size", BCDWindowReport.line(facts(scene: (820, 1180))).contains("(full size)"))
        check("no scene says so and keeps the other facts", BCDWindowReport.line(facts(scene: (0, 0), screen: (0, 0), fullScreen: nil, restrictions: nil, scenes: 0, screens: 0))
              == "[device] window: no scene full-screen=unknown size-restrictions=unknown scenes=0 screens=0 idiom=pad plist-orientations=3 requires-full-screen=0 delegate-orientations=3")
        check("an external display is a second screen", BCDWindowReport.line(facts(scenes: 2, screens: 2)).contains("scenes=2 screens=2"))
        check("the full-screen key and a locked delegate show", BCDWindowReport.line(facts(requires: true, delegate: 2)).hasSuffix("requires-full-screen=1 delegate-orientations=2"))
        check("the line has no name, path or identifier: digits, flags and the idiom only",
              BCDWindowReport.line(facts()).range(of: "^\\[device\\] window: [a-z0-9=\\- ().]+$", options: .regularExpression) != nil)

        if failures > 0 { exit(1) }
    }
}
