// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// madeira-bcd: auto-tune's engine checks (docs/AUTO-TUNE.md), compiled on the host by
// tests/host/check-autotune.py with app/Madeira/AutoTuneCore.swift and
// AutoTuneRules.generated.swift. No log files: reports are built by hand, and the
// scanner is fed bytes directly.

import Foundation

@main
struct AutoTuneEngineChecks {
    static var failures = 0
    static func expect(_ cond: Bool, _ what: String, file: StaticString = #fileID, line: UInt = #line) {
        if cond { print("ok   \(what)") } else { print("FAIL \(what) (line \(line))"); failures += 1 }
    }

    static func report(_ outcome: String, stage: Int, seconds: Double = 30, matched: [String] = [],
                       dock: Bool = false) -> AutoTuneReport {
        AutoTuneReport(outcome: outcome, stage: stage, seconds: seconds, presents: 0, peakMB: nil, dock: dock,
                       d3d12: false, ramMB: 11695, matched: matched, evidence: [])
    }

    static func main() {
        let rules = AutoTuneRules.load(builtIn: AutoTuneBuiltInRules.json, override: nil)
        expect(rules.count >= 10, "the built-in table decodes (\(rules.count) rules)")
        expect(Set(rules.map(\.id)).count == rules.count, "rule ids are unique")
        let cage = "cage-release-band-exhausted", jumbo = "jumbo-half-probe", spill = "thread-stack-spill"

        // 1. Baseline fails -> one unchanged retry -> the best-matching rule.
        var s = AutoTuneState()
        s = AutoTuneEngine.advance(s, report: report("crashed", stage: 3, matched: [cage, spill]), rules: rules,
                                   userKeys: [], fingerprint: "a", log: "baseline.txt")
        expect(s.status == "trying" && s.candidate == [] && s.candidateRule == "retry" && s.retried,
               "a first failure runs the same settings once more")
        expect(s.attempts.count == 1 && s.attempts[0].kept == nil && s.attempts[0].rule == nil, "the baseline is recorded")
        s = AutoTuneEngine.advance(s, report: report("crashed", stage: 3, matched: [cage, spill]), rules: rules,
                                   userKeys: [], fingerprint: "a", log: "retry.txt")
        expect(s.attempts.last?.kept == false && s.attempts.last?.rule == "retry", "an unchanged retry that did no better is not kept")
        expect(s.candidate == ["env.MADEIRA_CAGE_RELEASE = 1"] && s.candidateRule == cage,
               "then the first matching rule by priority (cage before stack spill)")
        expect(s.layerLines == ["env.MADEIRA_CAGE_RELEASE = 1"], "the layer carries the candidate")

        // 2. The candidate got further -> kept; the next failure brings the next rule.
        s = AutoTuneEngine.advance(s, report: report("crashed", stage: 4, seconds: 90, matched: [jumbo]), rules: rules,
                                   userKeys: [], fingerprint: "a", log: "a1.txt")
        expect(s.kept == ["env.MADEIRA_CAGE_RELEASE = 1"] && s.attempts.last?.kept == true, "a later stage keeps the line")
        expect(s.candidate == ["env.MADEIRA_JUMBO_HALF = 1"] && s.layerLines.count == 2, "and the next rule is tried on top")

        // 3. It plays -> found.
        s = AutoTuneEngine.advance(s, report: report("quit", stage: 5, seconds: 400), rules: rules,
                                   userKeys: [], fingerprint: "a", log: "a2.txt")
        expect(s.status == "found" && s.kept == ["env.MADEIRA_CAGE_RELEASE = 1", "env.MADEIRA_JUMBO_HALF = 1"] && s.candidate == nil,
               "a session that played ends the search with both lines")

        // 4. No progress -> reverted, the next candidate comes from the new report.
        var r = AutoTuneState()
        r = AutoTuneEngine.advance(r, report: report("crashed", stage: 3, matched: [cage]), rules: rules,
                                   userKeys: [], fingerprint: "b", log: nil)
        r = AutoTuneEngine.advance(r, report: report("crashed", stage: 3, matched: [cage]), rules: rules,
                                   userKeys: [], fingerprint: "b", log: nil)
        r = AutoTuneEngine.advance(r, report: report("crashed", stage: 3, seconds: 31, matched: [cage, spill]), rules: rules,
                                   userKeys: [], fingerprint: "b", log: nil)
        expect(r.kept.isEmpty && r.attempts.last?.kept == false, "no progress reverts the line")
        expect(r.candidateRule == spill, "an untried rule of the same failure comes next")
        r = AutoTuneEngine.advance(r, report: report("crashed", stage: 3, matched: [cage, spill]), rules: rules,
                                   userKeys: [], fingerprint: "b", log: nil)
        expect(r.status == "stuck" && r.candidate == nil, "every matching rule tried: stuck")

        // 5. Same stage but half again as long and 10 s more counts as progress.
        expect(AutoTuneEngine.better(report("crashed", stage: 3, seconds: 45), than: report("crashed", stage: 3, seconds: 30)),
               "1.5x and +10 s at the same stage is better")
        expect(!AutoTuneEngine.better(report("crashed", stage: 3, seconds: 35), than: report("crashed", stage: 3, seconds: 30)),
               "a few seconds more is not")
        expect(AutoTuneEngine.better(report("quit", stage: 3), than: report("crashed", stage: 4)), "playing beats any failure")
        expect(!AutoTuneEngine.better(report("crashed", stage: 2), than: report("crashed", stage: 3)), "an earlier stage is worse")
        expect(AutoTuneEngine.better(report("crashed", stage: 3, matched: [spill]), than: report("crashed", stage: 3, matched: [cage]), tried: cage),
               "the tried rule's own failure gone at the same stage is better")
        expect(!AutoTuneEngine.better(report("crashed", stage: 3, matched: [cage, spill]), than: report("crashed", stage: 3, matched: [cage]), tried: cage),
               "its failure still there plus another is not")
        expect(!AutoTuneEngine.better(report("crashed", stage: 3, matched: [spill]), than: report("crashed", stage: 3, matched: [cage])),
               "a different failure alone, with nothing tried, is not")

        // 6. Inconclusive sessions change nothing and do not count.
        var q = AutoTuneState()
        q = AutoTuneEngine.advance(q, report: report("crashed", stage: 3, matched: [cage]), rules: rules, userKeys: [], fingerprint: "c", log: nil)
        let before = q
        q = AutoTuneEngine.advance(q, report: report("inconclusive", stage: 2), rules: rules, userKeys: [], fingerprint: "c", log: nil)
        expect(q.candidate == before.candidate && q.kept == before.kept && q.decided == before.decided && q.attempts.count == before.attempts.count + 1,
               "an inconclusive session keeps the same candidate for the next start")

        // 7. The user's own keys are never proposed.
        var u = AutoTuneState(retried: true)
        u = AutoTuneEngine.advance(u, report: report("crashed", stage: 3, matched: [cage, spill]), rules: rules,
                                   userKeys: ["env.MADEIRA_CAGE_RELEASE"], fingerprint: "d", log: nil)
        expect(u.candidateRule == spill, "a key the game's own file sets is skipped")

        // 8. A changed fingerprint starts a new baseline and drops the pending candidate.
        var f = AutoTuneState()
        f = AutoTuneEngine.advance(f, report: report("crashed", stage: 3, matched: [cage]), rules: rules, userKeys: [], fingerprint: "e1", log: nil)
        f = AutoTuneEngine.advance(f, report: report("crashed", stage: 3, matched: [cage]), rules: rules, userKeys: [], fingerprint: "e1", log: nil)
        expect(f.candidateRule == cage, "a candidate is pending")
        f = AutoTuneEngine.advance(f, report: report("crashed", stage: 2, matched: [spill]), rules: rules, userKeys: [], fingerprint: "e2", log: nil)
        expect(f.fingerprint == "e2" && f.best?.stage == 2 && f.attempts.last?.kept == nil,
               "after the user changed the settings the session is a new baseline, nothing kept or reverted")
        expect(f.candidateRule == spill, "and the search goes on from it")

        // 9. At most maxAttempts decided attempts (rules with lines: a fixedIn rule stops at once, see 16).
        var m = AutoTuneState(retried: true)
        var n = 0
        let ids = rules.filter { $0.fixedIn == nil }.map(\.id)
        while m.status != "gave-up" && m.status != "stuck" && n < 40 {
            m = AutoTuneEngine.advance(m, report: report("crashed", stage: 3, matched: ids), rules: rules,
                                       userKeys: [], fingerprint: "g", log: nil)
            n += 1
        }
        expect(m.status == "gave-up" && m.decided == AutoTuneEngine.maxAttempts,
               "it stops after \(AutoTuneEngine.maxAttempts) attempts (\(m.decided) decided, status \(m.status))")

        // 10. A game that played at once needs nothing.
        let ok = AutoTuneEngine.advance(AutoTuneState(), report: report("ran", stage: 5, seconds: 600), rules: rules,
                                        userKeys: [], fingerprint: "h", log: nil)
        expect(ok.status == "ok" && ok.layerLines.isEmpty, "a first session that played adds nothing")

        // 11. The override file replaces, adds and disables rules.
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("autotune-override-\(getpid()).json")
        let override = """
        [{"id": "cage-release-band-exhausted", "lines": [], "priority": 1, "all": [{"has": ["x"]}], "reason": "off"},
         {"id": "my-rule", "lines": ["env.MADEIRA_TEST = 1"], "priority": 5, "all": [{"has": ["[my-signal]"]}], "reason": "test"},
         {"id": "jumbo-half-probe", "lines": ["env.MADEIRA_JUMBO_HALF = 1"], "priority": 3, "all": [{"has": ["[jumbo] other"]}], "reason": "replaced"}]
        """
        try? override.write(to: tmp, atomically: true, encoding: .utf8)
        let merged = AutoTuneRules.load(builtIn: AutoTuneBuiltInRules.json, override: tmp)
        expect(!merged.contains { $0.id == cage }, "an override with no lines disables a built-in rule")
        expect(merged.contains { $0.id == "my-rule" }, "an override adds a rule")
        expect(merged.first { $0.id == jumbo }?.all.first?.has == ["[jumbo] other"], "an override replaces a rule of the same id")
        expect(merged.count == rules.count, "one removed, one added, one replaced")
        let broken = FileManager.default.temporaryDirectory.appendingPathComponent("autotune-broken-\(getpid()).json")
        try? "not json".write(to: broken, atomically: true, encoding: .utf8)
        expect(AutoTuneRules.load(builtIn: AutoTuneBuiltInRules.json, override: broken).count == rules.count,
               "a broken override file leaves the built-in table alone")
        try? FileManager.default.removeItem(at: tmp)
        try? FileManager.default.removeItem(at: broken)

        // 12. The scanner: lines cut across chunks, a line without its newline, an overlong line, numbers.
        let signals = [AutoTuneSignal(has: ["[sig] alpha", "beta"]), AutoTuneSignal(has: ["[sig] gamma"])]
        let text = "[build] v0.1.552 (552)\n[device] model=iPhone18,2 ram=11695MB x\n[sig] alpha and beta\n"
            + String(repeating: "x", count: 40000) + " [sig] alpha beta (too far in)\n"
            + "[footprint] rev=ml358 phys=6100 MB (peak 6200) internal\n[freeze] alive t+305s beats\n"
            + "[exit-report] status=0xC0000005\n[sig] gamma at the end"
        for chunk in [1, 3, 7, 64, 4096, 1 << 20] {
            let sc = AutoTuneLogScanner(signals: signals)
            let bytes = Data(text.utf8)
            var i = 0
            while i < bytes.count { sc.feed(bytes.subdata(in: i..<min(i + chunk, bytes.count))); i += chunk }
            sc.finish()
            expect(sc.counts == [1, 1] && sc.facts.build == 552 && sc.facts.ramMB == 11695
                   && sc.facts.lastFootprintMB == 6100 && sc.facts.peakFootprintMB == 6200 && sc.facts.aliveSeconds == 305
                   && sc.facts.stage == 5 && sc.facts.exitStatus == 0xC0000005,
                   "scanner with \(chunk)-byte chunks: counts \(sc.counts), facts \(sc.facts.build ?? -1)/\(sc.facts.ramMB ?? -1)/\(sc.facts.aliveSeconds)")
        }

        // 12b. The quit line freezes the crash evidence seen so far.
        let qsig: [AutoTuneSignal] = []
        let crashFirst = AutoTuneLogScanner(signals: qsig)
        crashFirst.feed(Data("x Unhandled exception code c0000005\n[frontend] graceful close requested\n".utf8)); crashFirst.finish()
        let quitFirst = AutoTuneLogScanner(signals: qsig)
        quitFirst.feed(Data("[frontend] graceful close requested\nx Unhandled exception code c0000005\n".utf8)); quitFirst.finish()
        expect(crashFirst.facts.crashEvidenceAtQuit == 1 && quitFirst.facts.crashEvidenceAtQuit == 0,
               "crash evidence is counted up to the quit request only")

        // 13. Outcomes from the session-end facts.
        var fx = AutoTuneLogFacts()
        fx.stage = 4
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 100), fx) == "quit", "quit after frames: played")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, crashStatus: 0xC0000005, seconds: 100), fx) == "crashed", "a Windows error exit: crashed")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: false, seconds: 2), AutoTuneLogFacts()) == "inconclusive", "never started: inconclusive")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 400), fx) == "ran", "a long session that ended on its own: ran")
        var early = AutoTuneLogFacts(); early.stage = 2
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 8), early) == "exited-early", "ended before a frame: exited early")
        var mem = AutoTuneLogFacts(); mem.stage = 4; mem.lastFootprintMB = 6250; mem.availableMB = 6100
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true), mem) == "killed", "a kill near the memory limit: killed")
        var high = AutoTuneLogFacts(); high.stage = 5; high.lastFootprintMB = 7932; high.availableMB = 8061
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true), high) == "inconclusive",
               "a session that ended 400 MB under the estimated limit is not called a memory kill")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, limitMB: 8050), high) == "killed",
               "with the marker's measured limit 118 MB above it, it is")
        var gpu = AutoTuneLogFacts(); gpu.stage = 5; gpu.gpuErrors = 3
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true), gpu) == "hung", "a kill after GPU errors: hung")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true), fx) == "inconclusive", "a kill with no evidence: ask the user")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "froze"), fx) == "hung", "the user said it froze")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "closed-by-itself"), fx) == "killed", "the user said it closed by itself")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "i-closed"), gpu) == "inconclusive", "the user closed it: nothing learned")
        var cq = AutoTuneLogFacts(); cq.stage = 4; cq.unhandled = 1; cq.crashEvidenceAtQuit = 1; cq.quitRequested = true
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 100), cq) == "crashed",
               "a game that crashed before the user closed the session crashed (Madeira Dock)")
        var dq = AutoTuneLogFacts(); dq.stage = 4; dq.unhandled = 2
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 100), dq) == "quit",
               "a quit game that logged an exception on the way out still played")

        // 14. Memory: the device's swap step, then the upgrade replaces auto-tune's own swap-mb, never the user's.
        var w = AutoTuneState(retried: true)
        let kill = report("killed", stage: 4, seconds: 120, matched: ["swap-12gb-memory-kill"])
        w = AutoTuneEngine.advance(w, report: kill, rules: rules, userKeys: [], fingerprint: "m", log: nil)
        expect(w.candidate == ["swap-mb = 6144", "swap-mode = 2", "swap-min-mb = 1"], "a 12 GB memory kill: the 6144 MB step")
        w = AutoTuneEngine.advance(w, report: report("killed", stage: 4, seconds: 300, matched: ["swap-12gb-memory-kill", "swap-12gb-more"]),
                                   rules: rules, userKeys: [], fingerprint: "m", log: nil)
        expect(w.kept == ["swap-mb = 6144", "swap-mode = 2", "swap-min-mb = 1"], "longer before the kill: the step is kept")
        expect(w.candidate == ["swap-mb = 8192"] && w.layerLines == ["swap-mode = 2", "swap-min-mb = 1", "swap-mb = 8192"],
               "killed again with the swap file in use: 8192 replaces 6144 in the layer (\(w.layerLines))")
        w = AutoTuneEngine.advance(w, report: report("quit", stage: 5, seconds: 900), rules: rules, userKeys: [], fingerprint: "m", log: nil)
        expect(w.status == "found" && w.kept == ["swap-mode = 2", "swap-min-mb = 1", "swap-mb = 8192"], "kept: one swap-mb, the larger")
        var v = AutoTuneState(retried: true)
        v = AutoTuneEngine.advance(v, report: report("killed", stage: 4, matched: ["swap-12gb-memory-kill", "swap-12gb-more"]),
                                   rules: rules, userKeys: ["swap-mb"], fingerprint: "n", log: nil)
        expect(v.status == "stuck" && v.candidate == nil, "the user's own swap-mb is neither added to nor raised")
        var x = AutoTuneState(retried: true)
        x = AutoTuneEngine.advance(x, report: report("killed", stage: 4, matched: ["swap-12gb-more"]), rules: rules,
                                   userKeys: [], fingerprint: "o", log: nil)
        expect(x.status == "stuck", "an upgrade needs a line auto-tune kept: it never adds a first swap-mb on its own")

        // 16. A crash only a change in Madeira fixes (a fixedIn rule): no attempt, not even the retry.
        let layer = "metal-layer-format"
        expect(rules.first { $0.id == layer }?.fixedIn == 560 && rules.first { $0.id == layer }?.lines == [],
               "Warcraft III's layer-format crash is a rule with no lines, fixed in 560")
        var k = AutoTuneState()
        var old = report("crashed", stage: 2, matched: [layer, cage])
        old.build = 559
        k = AutoTuneEngine.advance(k, report: old, rules: rules, userKeys: [], fingerprint: "p", log: nil)
        expect(k.status == "known" && k.candidate == nil && k.candidateRule == nil && !k.retried && k.tried.isEmpty
               && k.known == layer && k.knownFixedIn == 560 && k.knownBuild == 559 && k.layerLines.isEmpty,
               "the best match is a known crash: auto-tune stops, no retry, no line (\(k.status))")
        expect(k.knownAdvice.contains("Fixed in build 560: update BCD."), "on an older build it says to update")
        var newer = old
        newer.build = 561
        let k2 = AutoTuneEngine.advance(AutoTuneState(), report: newer, rules: rules, userKeys: [], fingerprint: "p", log: nil)
        expect(k2.status == "known" && k2.knownAdvice.contains("export the report"),
               "on a build that has the fix it asks for the report instead")
        var later = AutoTuneState(retried: true)
        later = AutoTuneEngine.advance(later, report: report("crashed", stage: 3, matched: [cage, layer]), rules: rules,
                                       userKeys: [], fingerprint: "q", log: nil)
        expect(later.status == "trying" && later.candidateRule == cage,
               "a line rule ranked above the known crash is still tried first")
        k = AutoTuneEngine.advance(k, report: report("quit", stage: 5, seconds: 900), rules: rules, userKeys: [],
                                   fingerprint: "p", log: nil)
        expect(k.status == "ok", "after the update the game plays: nothing was added")
        let tmp2 = FileManager.default.temporaryDirectory.appendingPathComponent("autotune-known-\(getpid()).json")
        try? Data("""
        [{"id": "x-known", "lines": [], "fixedIn": 999, "priority": 2, "all": [{"has": ["x"]}], "reason": "r"},
         {"id": "metal-layer-format", "lines": [], "priority": 1, "all": [{"has": ["x"]}], "reason": "off"}]
        """.utf8).write(to: tmp2)
        let withKnown = AutoTuneRules.load(builtIn: AutoTuneBuiltInRules.json, override: tmp2)
        try? FileManager.default.removeItem(at: tmp2)
        expect(withKnown.contains { $0.id == "x-known" && $0.fixedIn == 999 } && !withKnown.contains { $0.id == layer },
               "an override may add a fixedIn rule; lines [] without fixedIn still disables one")
        let objc = AutoTuneLogScanner(signals: [])
        objc.feed(Data("*** Terminating app due to uncaught exception 'CAMetalLayerInvalid', reason: 'invalid pixel format 40'\n".utf8))
        objc.feed(Data("[redeliv] terminating process rev=ml465\n".utf8))
        objc.finish()
        expect(objc.facts.errorExits == 2, "the app's own end (an Objective-C exception, the redelivery guard) is crash evidence")

        // 15. State survives JSON (the app saves it between sessions).
        if let d = try? JSONEncoder().encode(s), let back = try? JSONDecoder().decode(AutoTuneState.self, from: d) {
            expect(back == s, "the state round-trips through JSON")
        } else { expect(false, "the state encodes") }
        if let d = try? JSONEncoder().encode(k2), let back = try? JSONDecoder().decode(AutoTuneState.self, from: d) {
            expect(back == k2 && back.knownFixedIn == 560, "a known crash's state round-trips through JSON")
        } else { expect(false, "the known state encodes") }
        let saved = Data(#"{"version":1,"status":"stuck","kept":[],"tried":[],"retried":true,"attempts":[]}"#.utf8)
        expect((try? JSONDecoder().decode(AutoTuneState.self, from: saved))?.known == nil,
               "a state saved before known crashes existed still loads")

        print(failures == 0 ? "PASS: auto-tune engine" : "FAILED: auto-tune engine (\(failures))")
        exit(failures == 0 ? 0 : 1)
    }
}
