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

        // 15b. A failure that comes back the same every start (a noRetry rule, the JIT pool filling up):
        // no unchanged retry; nocopy first, then the split pool (Counter-Strike 2, 2026-10-20).
        let nocopy = "x64-image-nocopy", split = "pool-split-exhausted"
        expect(rules.first { $0.id == split }?.noRetry == true && rules.first { $0.id == nocopy }?.noRetry == true,
               "the JIT pool rules skip the retry")
        var p = AutoTuneEngine.advance(AutoTuneState(), report: report("crashed", stage: 2, matched: [nocopy, split]),
                                       rules: rules, userKeys: [], fingerprint: "n", log: nil)
        expect(!p.retried && p.candidateRule == nocopy && p.candidate == ["env.MADEIRA_X64_IMAGE_NOCOPY = 1"],
               "a full JIT pool gets its first line at once (\(p.candidateRule ?? "-"))")
        p = AutoTuneEngine.advance(p, report: report("crashed", stage: 2, matched: [nocopy, split]), rules: rules,
                                   userKeys: [], fingerprint: "n", log: nil)
        expect(p.candidateRule == split && p.candidate == ["pool-split = 1"], "then the split pool")
        let plain = AutoTuneEngine.advance(AutoTuneState(), report: report("crashed", stage: 3, matched: [cage]), rules: rules,
                                           userKeys: [], fingerprint: "n2", log: nil)
        expect(plain.candidateRule == "retry", "other failures still get the unchanged retry first")

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

        // 17. Wine Mono missing (Panzer Paladin, 2026-10-20): the game page says where to download it. Only the err line
        // of mscoree counts; the "[wine-mono] ml1275 neither ..." line is written at every start of a device without
        // Mono, for games that do not need .NET too.
        let monoErr = "00a4:err:mscoree:CLRRuntimeInfo_GetRuntimeHost Wine Mono is not installed\n"
        let monoLine = "[wine-mono] ml1275 neither this build nor a download has Wine Mono\n"
        let monoLineNew = "[wine-mono] ml1275 neither this build nor a download has Wine Mono -- a .NET Framework game then exits 0xffffffff with \"Wine Mono is not installed\"; Settings > .NET Framework downloads it\n"
        let otherMscoree = "00a4:err:mscoree:CLRRuntimeInfo_GetRuntimeHost could not load the runtime\n"
        func scanned(_ text: String, chunk: Int = 1 << 20) -> AutoTuneLogScanner {
            let sc = AutoTuneLogScanner(signals: [])
            let bytes = Data(text.utf8)
            var i = 0
            while i < bytes.count { sc.feed(bytes.subdata(in: i..<min(i + chunk, bytes.count))); i += chunk }
            sc.finish()
            return sc
        }
        for chunk in [1, 5, 64, 1 << 20] {
            expect(scanned("[phase] wine-start t+ 0.7s\n" + monoLine + monoErr + "[exit-report] status=0xFFFFFFFF\n", chunk: chunk).facts.monoMissing,
                   "the scanner sees the mscoree err line (\(chunk)-byte chunks)")
        }
        expect(!scanned(monoLine + monoLineNew).facts.monoMissing,
               "the [wine-mono] ml1275 line alone (old or new wording) is no evidence: every start of a device without Mono writes it")
        expect(!scanned(otherMscoree + "00a4:fixme:mscoree:parse_supported_runtime sku=L\".NETFramework,Version=v4.5.2\" not implemented\n").facts.monoMissing,
               "other mscoree lines are not it")
        let failedEnd = AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 40)
        let monoScan = scanned("[phase] wine-start t+ 0.7s\n" + monoErr + "[exit-report] status=0xFFFFFFFF\n")
        let monoReport = AutoTuneEngine.report(failedEnd, monoScan, rules: rules, signals: [])
        expect(monoReport.failed && monoReport.monoMissing == true, "a failed session that showed it: the report says Wine Mono is missing (\(monoReport.outcome))")
        let playedScan = scanned("[phase] wine-start t+ 0.7s\n" + monoErr + "[freeze] alive t+305s beats\n")
        let playedReport = AutoTuneEngine.report(AutoTuneSessionEnd(sawProcess: true, seconds: 400), playedScan, rules: rules, signals: [])
        expect(playedReport.played && playedReport.monoMissing == nil,
               "a session that played shows nothing, whatever a side process logged (\(playedReport.outcome))")
        let plainReport = AutoTuneEngine.report(failedEnd, scanned("[phase] wine-start t+ 0.7s\n" + monoLine + "[exit-report] status=0xC0000005\n"),
                                                rules: rules, signals: [])
        expect(plainReport.failed && plainReport.monoMissing == nil, "a failure without the err line shows nothing, the ml1275 line or not")
        var mono = AutoTuneEngine.advance(AutoTuneState(), report: monoReport, rules: rules, userKeys: [], fingerprint: "mono", log: nil)
        expect(mono.monoMissing == true, "the state keeps it for the game page")
        mono = AutoTuneEngine.advance(mono, report: report("inconclusive", stage: 1), rules: rules, userKeys: [], fingerprint: "mono", log: nil)
        expect(mono.monoMissing == true, "an inconclusive session changes nothing")
        mono = AutoTuneEngine.advance(mono, report: monoReport, rules: rules, userKeys: [], fingerprint: "mono", log: nil)
        expect(mono.monoMissing == true && mono.status == "stuck", "it stays while the failure comes back; auto-tune itself behaves as before (\(mono.status))")
        if let d = try? JSONEncoder().encode(mono), let back = try? JSONDecoder().decode(AutoTuneState.self, from: d) {
            expect(back == mono && back.monoMissing == true, "the flag round-trips through JSON")
        } else { expect(false, "the Mono state encodes") }
        mono = AutoTuneEngine.advance(mono, report: report("quit", stage: 5, seconds: 400), rules: rules, userKeys: [], fingerprint: "mono", log: nil)
        expect(mono.monoMissing == nil, "a session that played clears it")
        let neverMono = AutoTuneEngine.advance(AutoTuneState(), report: report("crashed", stage: 3, matched: [cage]), rules: rules,
                                               userKeys: [], fingerprint: "n3", log: nil)
        expect(neverMono.monoMissing == nil, "a game that never showed it has none")
        if let d = try? JSONEncoder().encode(neverMono) {
            expect(!String(decoding: d, as: UTF8.self).contains("monoMissing"), "and its saved file is the same as before: no new key")
        } else { expect(false, "the plain state encodes") }
        let savedBefore = Data(#"{"version":1,"status":"found","kept":["x = 1"],"tried":[],"retried":false,"attempts":[],"best":{"outcome":"crashed","stage":3,"seconds":30,"presents":0,"dock":false,"d3d12":false,"matched":[],"evidence":[],"dialogs":[]}}"#.utf8)
        let loaded = try? JSONDecoder().decode(AutoTuneState.self, from: savedBefore)
        expect(loaded?.kept == ["x = 1"] && loaded?.monoMissing == nil && loaded?.best?.monoMissing == nil,
               "a state saved before the flag existed still loads, with its kept lines")

        // 18. Round 579, the false positives the logs of 2026-10-21 showed. Each guard only narrows when a rule fires or how
        // a session that is no game's is scored; the first half of every pair is the case it must still catch.
        do {
            let allSignals = AutoTuneEngine.signals(rules)
            func scan(_ text: String) -> AutoTuneLogScanner {
                let sc = AutoTuneLogScanner(signals: allSignals)
                sc.feed(Data(text.utf8)); sc.finish()
                return sc
            }
            func session(_ text: String, end: AutoTuneSessionEnd = AutoTuneSessionEnd(sawProcess: true, seconds: 30)) -> AutoTuneReport {
                AutoTuneEngine.report(end, scan(text), rules: rules, signals: allSignals)
            }
            let head = "[build] v0.1.578 (578)\n[phase] wine-start             t+  0.639s rev=ml526\n"
                + "D 24 Load module Game.exe (game.exe-0123456789abcdef): 140000000\n"
            func exitLine(_ code: String) -> String {
                "0024:err:process:NtTerminateProcess NtTerminateProcess(handle=0xffffffffffffffff, exit_code=\(code)) syscalls=0 unix_calls=4158\n"
            }

            // free-delay-off needs a held release of its own (Minecraft Dungeons CYDN1E: 458,752 refused probes, no hold at all).
            let refused = (1...210).map {
                "[alloc-fail] ml814 #\($0) status=c0000018 hint=0x71f5632000 size=0x1000 type=0x3000 protect=0x40 tid=00a8 | STAGE=fixed-map err=3 at 0x73ffe40000+0x1ac000\n"
            }.joined()
            let held = "[free-delay] #1 release of 0x7300000000+0x200000 held for 2000 ms (1 in flight)\n"
            expect(session(head + refused + exitLine("0xc0000005")).matched.contains("free-delay-off") == false,
                   "200+ refused c0000018 requests without a [free-delay] line: not the free-delay trap")
            expect(session(head + held + refused + exitLine("0xc0000005")).matched.contains("free-delay-off"),
                   "the same with a held release in the log: it is")
            expect(session(head + "[free-delay] #3 reservation at 0x7300000000+0x200000: 1 held release(s) from 0x7300000000 (0x3ff000 bytes) freed at once\n"
                           + refused + exitLine("0xc0000005")).matched.contains("free-delay-off"),
                   "and a reservation line of the hold's reuse path counts as well")

            // exe-window-fixed-base: a one-time installer's helper in %TEMP% is not the game's program.
            let gameMain = "wine: failed to create main module for L\"C:\\\\Program Files (x86)\\\\Steam\\\\steamapps\\\\common\\\\Game\\\\Game.exe\", status c0000018\n"
            let helperMain = "wine: failed to create main module for L\"C:\\\\users\\\\<user>\\\\AppData\\\\Local\\\\Temp\\\\DXa9e1.tmp\\\\infinst.exe\", status c0000018\n"
            expect(session(head + gameMain + exitLine("0xc0000018")).matched.contains("exe-window-fixed-base"),
                   "the game's own main module refused (c0000018): the window rule still fires")
            expect(!session(head + helperMain + exitLine("0xc0000018")).matched.contains("exe-window-fixed-base"),
                   "the DirectX setup's infinst.exe in %TEMP% failing the same way: it does not")
            expect(!session(head + helperMain.replacingOccurrences(of: "Temp\\\\DXa9e1.tmp\\\\", with: "") + exitLine("0xc0000018")).matched.contains("exe-window-fixed-base"),
                   "the helper's file name alone is enough")

            // thread-stack-spill: not for a 32-bit program's stack, which the line cannot move (Shade 6NE7KH).
            let kernelStack = "[thr-create] FAILED at kernel stack: status=0xc0000017 pthread=0(-) reserve=0x0 commit=0x8000 creator pid=0020 tid=0024 peb=0x71ffff0000; task threads 101\n"
            let wowStack = "[thr-create] FAILED at WoW64 32-bit stack: status=0xc0000017 pthread=0(-) reserve=0x100000 commit=0x0 creator pid=0020 tid=0028 peb=0x47ffd0000; task threads 28\n"
            expect(session(head + kernelStack + exitLine("0xc0000005")).matched.contains("thread-stack-spill"),
                   "a thread's kernel stack refused: the spill rule still fires")
            expect(!session(head + wowStack + exitLine("0x3")).matched.contains("thread-stack-spill"),
                   "a WoW64 32-bit stack refused inside its window: it does not")
            expect(!session(head + kernelStack + wowStack + exitLine("0x3")).matched.contains("thread-stack-spill"),
                   "nor when that is one of the session's refusals")

            // A session that only ran the one-time installs, and whose batch ended, is no session of the game.
            let installsOnly = "[madeira-dock] app 271590: this session runs only the one-time installs; the host starts after a restart\n"
            let installsEnd = "[dock-installers] end succeeded=2 failed=0 of=2; the session ends, the game starts after a restart\n"
            let byItself = AutoTuneSessionEnd(sawProcess: true, seconds: 60, killed: true, userSaid: "closed-by-itself")
            let footprint = "[footprint] rev=ml358 phys=2116 MB (peak 2272) internal=1572 MB compressed=541 MB external=316 MB reusable=31 MB (cycle=21)\n"
            let dock = "[dock-launch] app 271590\n"
            expect(session(head + dock + footprint + installsOnly, end: byItself).outcome == "killed",
                   "an installs-only session whose batch never ended and whose user said it closed by itself: scored as before")
            expect(scan(head + dock + installsOnly).facts.installsOnly && !scan(head + dock + installsOnly).facts.installsOver,
                   "the scanner sees the installs-only line, and the batch is not over without its end line")
            for chunk in [1, 5, 64] {
                let sc = AutoTuneLogScanner(signals: allSignals)
                let bytes = Data((head + installsOnly + installsEnd).utf8)
                var i = 0
                while i < bytes.count { sc.feed(bytes.subdata(in: i..<min(i + chunk, bytes.count))); i += chunk }
                sc.finish()
                expect(sc.facts.installsOver, "installs-only and its end line make the session over (\(chunk)-byte chunks)")
            }
            expect(session(head + dock + footprint + installsOnly + installsEnd, end: byItself).outcome == "inconclusive",
                   "the install batch ended and BCD closed itself for a restart: inconclusive, not a memory kill (NM88NQ)")
            let overReport = session(head + dock + footprint + installsOnly + installsEnd + helperMain + exitLine("0xc0000018"))
            expect(overReport.outcome == "inconclusive" && overReport.matched.isEmpty,
                   "an installer's helper failing in such a session: inconclusive, no rule (T3P8HK)")
            let overState = AutoTuneEngine.advance(AutoTuneState(), report: overReport, rules: rules, userKeys: [], fingerprint: "i", log: nil)
            expect(overState.candidate == nil && overState.kept.isEmpty && overState.best == nil && overState.status == "baseline",
                   "and the search has nothing to start from")
            expect(session(head + dock + footprint + installsEnd + exitLine("0xc0000005")).outcome == "crashed",
                   "the end line without the installs-only line (the host follows the installs in the same session): scored normally")
            expect(session(head + dock + footprint + installsOnly, end: AutoTuneSessionEnd(sawProcess: true, seconds: 68, killed: true, userSaid: "froze")).outcome == "hung",
                   "an installer that never ends and a user who says it froze: still a hang (Arkham City's .NET setup)")

            // A window title that only reads like an error box is not one (Hotline Miami's GameMaker form).
            expect(scan(head + "[win-name] #56 hwnd=0x100ae class='' text=\"Error Messages\" rev=ml853\n").facts.dialogs.isEmpty,
                   "GameMaker's hidden \"Error Messages\" form is no error box")
            expect(scan(head + "[win-name] #56 hwnd=0x100ae class='' text=\"Error Messages\" rev=ml853\n"
                        + "[win-name] #57 hwnd=0x100b0 class='' text=\"Critical error\" rev=ml853\n").facts.dialogs == ["Critical error"],
                   "a real caption beside it still is")
            let exitsAlone = session(head + "[launch-view] dismissed reason=present logs=0\n"
                                     + "[win-name] #56 hwnd=0x100ae class='' text=\"Error Messages\" rev=ml853\n" + exitLine("0x0"))
            expect(exitsAlone.outcome == "exited-early" && exitsAlone.dialogs.isEmpty,
                   "a game that ends itself with exit 0 and shows nothing but that form: exited early, not crashed (\(exitsAlone.outcome))")
        }
        // 19. One huge image that got no JIT-pool copy (Stellar Blade Demo's 314 MiB exe, YBX2WT, and FINAL FANTASY RESONANCE
        // DEMO's 474 MiB exe, EJDV7Z, build 576): no region of a split pool takes it, so pool-split-exhausted (imageMaxMB 300)
        // does not match; x64-image-nocopy does. A small image keeps both rules, as does an anonymous allocation that failed.
        expect(rules.first { $0.id == split }?.imageMaxMB == 300 && rules.first { $0.id == nocopy }?.imageMaxMB == nil,
               "only the split pool's rule carries the image size bar")
        let allSignals = AutoTuneEngine.signals(rules)
        func poolFail(_ line: String) -> AutoTuneReport {
            let sc = AutoTuneLogScanner(signals: allSignals)
            sc.feed(Data(("[phase] wine-start t+ 0.7s\n" + line + "\n"
                          + "00ac:err:process:NtTerminateProcess NtTerminateProcess(handle=0xffffffffffffffff, exit_code=0xc0000017) syscalls=0 unix_calls=8210\n").utf8))
            sc.finish()
            return AutoTuneEngine.report(AutoTuneSessionEnd(sawProcess: true, seconds: 5), sc, rules: rules, signals: allSignals)
        }
        let tail = " want=0x13a48000 bump=0xbb44000/0x2bd30000 tail_resv=0x2008000 freelist=0 \u{2014} FAILING the load (was: silent BUS loop)"
        let huge = poolFail("[jit-pool] EXHAUSTED (image 0x140000000+0x13a43000):" + tail)
        expect(huge.jitImageFailMB == 314 && huge.matched.contains(nocopy) && !huge.matched.contains(split),
               "a 314 MiB image: x64-image-nocopy only (\(huge.matched), \(huge.jitImageFailMB ?? -1) MiB)")
        let small = poolFail("[jit-pool] EXHAUSTED (image 0x71e5950000+0x4a36000):" + tail)
        expect(small.jitImageFailMB == 74 && small.matched.contains(nocopy) && small.matched.contains(split),
               "a 74 MiB DLL keeps both rules (\(small.matched))")
        let edge = poolFail("[jit-pool] EXHAUSTED (image 0x71e5950000+0x12c00000):" + tail)
        let edgePlus = poolFail("[jit-pool] EXHAUSTED (image 0x71e5950000+0x12d00000):" + tail)
        expect(edge.matched.contains(split) && !edgePlus.matched.contains(split),
               "300 MiB passes the bar, 301 MiB does not (\(edge.jitImageFailMB ?? -1), \(edgePlus.jitImageFailMB ?? -1))")
        let anon = poolFail("[jit-pool] EXHAUSTED (anon RWX): want=0x40000000 bump=0x10000000/0x25000000 tail_resv=0x0 freelist=0 \u{2014} FAILING allocation")
        expect(anon.jitImageFailMB == nil && anon.matched.contains(split) && !anon.matched.contains(nocopy),
               "an anonymous allocation that failed has no image size: pool-split still matches (\(anon.matched))")
        let both = poolFail("[jit-pool] EXHAUSTED (image 0x71e5950000+0x4a36000):" + tail + "\n[jit-pool] EXHAUSTED (image 0x140000000+0x1da1b000):" + tail)
        expect(both.jitImageFailMB == 474 && !both.matched.contains(split),
               "the largest image counts, whatever order the lines come in (\(both.jitImageFailMB ?? -1) MiB)")
        for chunk in [1, 7, 4096] {
            expect(scanned("[jit-pool] EXHAUSTED (image 0x140000000+0x13a43000):" + tail + "\n", chunk: chunk).facts.jitImageFailMB == 314,
                   "the scanner reads the image size across \(chunk)-byte chunks")
        }
        var hugeState = AutoTuneEngine.advance(AutoTuneState(), report: huge, rules: rules, userKeys: [], fingerprint: "huge", log: nil)
        expect(!hugeState.retried && hugeState.candidateRule == nocopy, "a huge image gets x64-image-nocopy at once (\(hugeState.candidateRule ?? "-"))")
        hugeState = AutoTuneEngine.advance(hugeState, report: huge, rules: rules, userKeys: [], fingerprint: "huge", log: nil)
        expect(hugeState.status == "stuck" && hugeState.candidate == nil,
               "and when that did not help there is no futile pool-split attempt after it (\(hugeState.status))")
        let listed = AutoTuneEngine.advance(AutoTuneState(), report: huge, rules: rules,
                                            userKeys: ["env.MADEIRA_X64_IMAGE_NOCOPY"], fingerprint: "huge2", log: nil)
        expect(listed.status == "stuck" && listed.candidate == nil,
               "with the line already in the game's list, a huge image has no attempt left (\(listed.status))")
        if let d = try? JSONEncoder().encode(huge), let back = try? JSONDecoder().decode(AutoTuneReport.self, from: d) {
            expect(back == huge && back.jitImageFailMB == 314, "the image size round-trips through JSON")
        } else { expect(false, "the report encodes") }

        // 20. A .NET 7+ game whose garbage collector cannot reserve its 256 GB range (Unrailed! 2: Back on Track, CR8H6H, build 576):
        // DOTNET_GCRegionRange = 400000000 (16 GB); not again when the session already ran with it.
        let gcRule = "dotnet-gc-region-range"
        func crashed(_ lines: String) -> AutoTuneReport {
            let sc = AutoTuneLogScanner(signals: allSignals)
            sc.feed(Data(("[phase] wine-start t+ 0.7s\n" + lines
                          + "00ac:err:process:NtTerminateProcess NtTerminateProcess(handle=0xffffffffffffffff, exit_code=0xe0434352) syscalls=0 unix_calls=8210\n").utf8))
            sc.finish()
            return AutoTuneEngine.report(AutoTuneSessionEnd(sawProcess: true, seconds: 5), sc, rules: rules, signals: allSignals)
        }
        let gcFail = "00ac:err:eventlog:ReportEventW L\"Message: GC heap initialization failed with error 0x8007000E\\n\"\n"
        let gcOne = crashed(gcFail)
        expect(gcOne.matched == [gcRule] && rules.first { $0.id == gcRule }?.lines == ["env.DOTNET_GCRegionRange = 400000000"],
               "the GC's out-of-memory at initialisation gets the 16 GB range (\(gcOne.matched))")
        var gcState = AutoTuneEngine.advance(AutoTuneState(), report: gcOne, rules: rules, userKeys: [], fingerprint: "gc", log: nil)
        expect(!gcState.retried && gcState.candidateRule == gcRule, "at once, no unchanged retry (\(gcState.candidateRule ?? "-"))")
        expect(crashed("[madeira-env] game DOTNET_GCRegionRange=400000000\n" + gcFail).matched.isEmpty,
               "a session that already ran with the line does not match again")
        expect(crashed("00ac:err:eventlog:ReportEventW L\"Message: GC heap initialization failed with error 0x80070057\\n\"\n").matched.isEmpty
               && crashed("00ac:err:eventlog:ReportEventW L\"Message: something else\\n\"\n").matched.isEmpty,
               "another GC initialisation error, or another event, is not it")
        gcState = AutoTuneEngine.advance(gcState, report: gcOne, rules: rules, userKeys: [], fingerprint: "gc", log: nil)
        expect(gcState.status == "stuck", "and when the line did not help, nothing else is tried for it (\(gcState.status))")

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
