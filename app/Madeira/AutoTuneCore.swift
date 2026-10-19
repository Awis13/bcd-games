// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  AutoTuneCore.swift
//  Madeira
//
//  madeira-bcd: auto-tune's logic (docs/AUTO-TUNE.md). Foundation only:
//  no UI, no app state, nothing that lives past a call. tests/host/check-autotune.py
//  compiles it alone with the rule table and replays real session logs through it.
//
//  Auto-tune never runs while a game runs. A session log is read after the session
//  ended (or at the next start, after a killed run), as a stream of 64 KB chunks
//  with fixed-size state, so a 50 MB log costs no more memory than a 3 MB one.
//

import Foundation

// MARK: - Rules

/// One thing to look for in a session log: every string of `has` on one line.
struct AutoTuneSignal: Codable, Hashable {
    var has: [String]
    /// At least this many such lines (1 when absent).
    var count: Int?
}

/// One known fix: the lines an attempt adds when a session failed with these signals.
/// The table is generated from build/tools/autotune_rules.py
/// (AutoTuneRules.generated.swift); Documents/autotune-rules.json may add rules or
/// replace built-in ones of the same id.
struct AutoTuneRule: Codable {
    var id: String
    /// "key = value" each.
    var lines: [String]
    /// Lower is tried first among the rules one session matches.
    var priority: Int
    var all: [AutoTuneSignal]
    var any: [AutoTuneSignal]?
    var none: [AutoTuneSignal]?
    /// The failures it answers; every failure when absent.
    var outcomes: [String]?
    /// "library" (a direct start) or "dock" (a Steam game through Madeira Dock); both when absent.
    var session: String?
    /// "d3d11" or "d3d12"; either when absent.
    var api: String?
    var ramMinMB: Int?
    var ramMaxMB: Int?
    /// How far the session got (AutoTuneLogFacts.stage).
    var minStage: Int?
    var maxStage: Int?
    /// May replace a line auto-tune kept earlier with the same key (a larger swap file
    /// after the first was not enough). Never a key the user sets.
    var upgrade: Bool?
    /// One sentence the app shows with the attempt.
    var reason: String
    /// Fixture names and notes the rule was taken from.
    var evidence: [String]?
    /// A failure only a change in Madeira fixes: the build that fixed it. Such a rule has no
    /// lines; when it is the best match, auto-tune stops and says so instead of spending
    /// attempts on lines that cannot help (Warcraft III's 16-bit back buffer, build 560).
    var fixedIn: Int?
}

enum AutoTuneRules {
    /// The built-in table, then the override file's rules (a rule with a built-in id
    /// replaces it; "lines": [] in an override disables it). Decoded for one analysis.
    static func load(builtIn: String, override: URL?) -> [AutoTuneRule] {
        let decoder = JSONDecoder()
        var rules = (try? decoder.decode([AutoTuneRule].self, from: Data(builtIn.utf8))) ?? []
        if let override, let data = try? Data(contentsOf: override),
           let extra = try? decoder.decode([AutoTuneRule].self, from: data) {
            for rule in extra {
                rules.removeAll { $0.id == rule.id }
                if !rule.lines.isEmpty || rule.fixedIn != nil { rules.append(rule) }
            }
        }
        return rules
    }

    /// The key of a "key = value" line.
    static func key(_ line: String) -> String {
        (line.split(separator: "=", maxSplits: 1).first.map(String.init) ?? line)
            .trimmingCharacters(in: .whitespaces)
    }
}

// MARK: - Log scan

/// What one session log says, gathered with fixed-size state.
struct AutoTuneLogFacts: Codable, Equatable {
    var build: Int?
    var ramMB: Int?
    var availableMB: Int?
    var dock = false
    var d3d12 = false
    /// 0 nothing, 1 Wine started, 2 the game's modules loaded, 3 first frame,
    /// 4 presenting for 20 s, 5 played on (5 minutes alive).
    var stage = 0
    var aliveSeconds = 0
    var lastFootprintMB: Int?
    var peakFootprintMB: Int?
    var quitRequested = false
    var returnedToLibrary = false
    var exitStatus: UInt32?
    var unhandled = 0
    var runtimeErrorDialog = false
    var freezeGaps = 0
    var mainStalls = 0
    /// The highest "detach-wait: presents=N" (every 30 s while the app waits on the game;
    /// in a Madeira Dock session the only frame count the log has).
    var presentsLogged = 0
    /// Metal command buffers that failed ("Device error at frame"): a GPU hang or fault.
    var gpuErrors = 0
    /// What the game's own error boxes said ([win-name] texts that read like an error),
    /// at most 4, 200 characters each: Darksiders II's "resolution 1112 x 832 is not
    /// supported for fullscreen display" is the reason, not anything in Wine's lines.
    var dialogs: [String] = []
    /// A process that ended itself with an NTSTATUS error, a child the Dock saw exit with a
    /// negative code, or a DLL whose initialisation failed: in none of the logs of a session
    /// that played (2026-10-16 corpus: 16 of 16 clean), in most crashes.
    var errorExits = 0
    /// Crash evidence already in the log when the user asked to quit: a Steam game through
    /// Madeira Dock can crash and leave the session open until the user closes it.
    var crashEvidenceAtQuit: Int?

    var crashEvidence: Int { unhandled + errorExits + (runtimeErrorDialog ? 1 : 0) + (exitStatus == nil ? 0 : 1) }
}

final class AutoTuneLogScanner {
    static let chunk = 65536
    static let maxLine = 16384

    private(set) var facts = AutoTuneLogFacts()
    /// Lines that matched each signal, in the order of `signals`.
    private(set) var counts: [Int]
    /// The first line that matched each signal (at most 24 lines, 240 bytes each).
    private(set) var evidence: [String] = []
    private let needles: [[[UInt8]]]
    private var carry: [UInt8] = []

    init(signals: [AutoTuneSignal]) {
        needles = signals.map { $0.has.map { Array($0.utf8) } }
        counts = Array(repeating: 0, count: signals.count)
    }

    /// Streams the whole file. bcd-log.txt holds one app run, which runs one Wine
    /// session, and its build and device lines are written before the session starts.
    static func scan(_ url: URL, signals: [AutoTuneSignal]) -> AutoTuneLogScanner? {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        let scanner = AutoTuneLogScanner(signals: signals)
        while let data = try? handle.read(upToCount: chunk), !data.isEmpty {
            scanner.feed(data)
        }
        scanner.finish()
        return scanner
    }

    func feed(_ data: Data) {
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            guard let base = raw.baseAddress else { return }
            var start = 0
            while start < raw.count {
                let rest = raw.count - start
                if let nl = memchr(base + start, 0x0A, rest) {
                    let end = start + (UnsafeRawPointer(nl) - (base + start))
                    let piece = UnsafeRawBufferPointer(start: base + start, count: end - start)
                    if carry.isEmpty {
                        line(piece)
                    } else {
                        append(piece)
                        carry.withUnsafeBytes { line($0) }
                        carry.removeAll(keepingCapacity: true)
                    }
                    start = end + 1
                } else {
                    append(UnsafeRawBufferPointer(start: base + start, count: rest))
                    break
                }
            }
        }
    }

    func finish() {
        if !carry.isEmpty {
            carry.withUnsafeBytes { line($0) }
            carry.removeAll()
        }
        if facts.aliveSeconds >= 300 { facts.stage = max(facts.stage, 5) }
    }

    /// A line longer than maxLine is kept to its first maxLine bytes.
    private func append(_ piece: UnsafeRawBufferPointer) {
        let room = Self.maxLine - carry.count
        if room > 0 { carry.append(contentsOf: piece.prefix(room)) }
    }

    private func line(_ whole: UnsafeRawBufferPointer) {
        // The same cut whether the line came whole from one chunk or through `carry`.
        let l = whole.count > Self.maxLine ? UnsafeRawBufferPointer(rebasing: whole[0..<Self.maxLine]) : whole
        guard l.count > 4 else { return }
        for i in needles.indices where Self.hasAll(l, needles[i]) {
            counts[i] += 1
            if counts[i] == 1 && evidence.count < 24 {
                evidence.append(String(decoding: l.prefix(240), as: UTF8.self))
            }
        }
        markers(l)
    }

    private static func find(_ l: UnsafeRawBufferPointer, _ needle: [UInt8]) -> Int? {
        guard let base = l.baseAddress, !needle.isEmpty, l.count >= needle.count else { return nil }
        return needle.withUnsafeBytes { n -> Int? in
            guard let hit = memmem(base, l.count, n.baseAddress!, n.count) else { return nil }
            return UnsafeRawPointer(hit) - base
        }
    }

    private static func hasAll(_ l: UnsafeRawBufferPointer, _ list: [[UInt8]]) -> Bool {
        for n in list where find(l, n) == nil { return false }
        return !list.isEmpty
    }

    /// The decimal number right after `marker`, if any.
    private static func number(_ l: UnsafeRawBufferPointer, after marker: [UInt8], hex: Bool = false) -> UInt64? {
        guard let at = find(l, marker) else { return nil }
        var i = at + marker.count, value: UInt64 = 0, digits = 0
        while i < l.count, digits < 16 {
            let c = l[i]
            let d: UInt64
            if c >= 0x30 && c <= 0x39 { d = UInt64(c - 0x30) }
            else if hex && c >= 0x61 && c <= 0x66 { d = UInt64(c - 0x61 + 10) }
            else if hex && c >= 0x41 && c <= 0x46 { d = UInt64(c - 0x41 + 10) }
            else { break }
            value = value * (hex ? 16 : 10) + d
            digits += 1
            i += 1
        }
        return digits > 0 ? value : nil
    }

    private static let mBuild = Array("[build] v0.1.".utf8)
    private static let mDevice = Array("[device] model=".utf8)
    private static let mRam = Array(" ram=".utf8)
    private static let mLaunch = Array("[device] launch:".utf8)
    private static let mAvailable = Array("available=".utf8)
    private static let mDock = Array("[dock-launch]".utf8)
    /// A D3D12 game opens a command queue; a D3D11 game may only probe D3D12CreateDevice (GTA V Legacy).
    private static let mD3D12 = Array("[madeira-d3d12] command queue: type".utf8)
    private static let mWineStart = Array("[phase] wine-start".utf8)
    private static let mModule = Array("Load module ".utf8)
    private static let mX64Image = Array("[x64-image]".utf8)
    private static let mPresenting = Array("Game is presenting (#1".utf8)
    private static let mSettled = Array("Early detach: game presenting and settled".utf8)
    private static let mAlive = Array("[freeze] alive t+".utf8)
    private static let mFootprint = Array("[footprint] rev=ml358 phys=".utf8)
    private static let mPeak = Array("(peak ".utf8)
    private static let mQuit = Array("[frontend] graceful close requested".utf8)
    private static let mReturned = Array("[frontend] returned to library".utf8)
    private static let mExitReport = Array("[exit-report] status=0x".utf8)
    private static let mUnhandled = Array("Unhandled exception".utf8)
    private static let mRootFrame = Array("ACCEPTING terminal root frame".utf8)
    private static let mRuntimeError = Array("Runtime error!".utf8)
    private static let mFreeze = Array("cause=unexplained-FREEZE".utf8)
    private static let mMainStall = Array("[main-stall] #".utf8)
    private static let mDetachWait = Array("detach-wait: presents=".utf8)
    private static let mLaunchView = Array("[launch-view] dismissed reason=".utf8)
    private static let mDeviceError = Array("Device error at frame".utf8)
    private static let mTerminate = Array("NtTerminateProcess(".utf8)
    private static let mErrorCode = Array("exit_code=0xc".utf8)
    /// 0x8... codes too: a breakpoint (0x80000003) or Wine's assertion exit (0x80000101).
    private static let mWarningCode = Array("exit_code=0x8".utf8)
    private static let mChildNegative = Array("child exited with code -".utf8)
    private static let mInitFailed = Array("\" failed to initialize, aborting".utf8)
    /// The whole app ended: an Objective-C exception (Warcraft III's 16-bit back buffer,
    /// 'CAMetalLayerInvalid'), or the redelivery guard ending a fault loop (Warcraft II's
    /// self-modifying code). Both leave a killed run whose log says why.
    private static let mObjCException = Array("*** Terminating app due to uncaught exception".utf8)
    private static let mRedelivKill = Array("[redeliv] terminating process".utf8)
    private static let mWinName = Array("[win-name] #".utf8)
    private static let mText = Array(" text=\"".utf8)
    private static let errorWords = ["error", "Error", "ERROR", "could not", "Could not", "Could Not", "failed", "Failed",
                                     "not supported", "unable", "Unable", "cannot", "Cannot", "Fatal", "fatal"].map { Array($0.utf8) }

    private func markers(_ l: UnsafeRawBufferPointer) {
        typealias S = AutoTuneLogScanner
        if facts.build == nil, let v = S.number(l, after: S.mBuild) { facts.build = Int(v) }
        if facts.ramMB == nil, S.find(l, S.mDevice) != nil, let v = S.number(l, after: S.mRam) { facts.ramMB = Int(v) }
        if facts.availableMB == nil, S.find(l, S.mLaunch) != nil, let v = S.number(l, after: S.mAvailable) {
            facts.availableMB = Int(v)
        }
        if !facts.dock, S.find(l, S.mDock) != nil { facts.dock = true }
        if !facts.d3d12, S.find(l, S.mD3D12) != nil { facts.d3d12 = true }
        if facts.stage < 1, S.find(l, S.mWineStart) != nil { facts.stage = 1 }
        if facts.stage < 2, S.find(l, S.mModule) != nil || S.find(l, S.mX64Image) != nil { facts.stage = 2 }
        if facts.stage < 3, S.find(l, S.mPresenting) != nil { facts.stage = 3 }
        if facts.stage < 4, S.find(l, S.mSettled) != nil { facts.stage = 4 }
        if let v = S.number(l, after: S.mAlive) { facts.aliveSeconds = max(facts.aliveSeconds, Int(v)) }
        if let v = S.number(l, after: S.mFootprint) {
            facts.lastFootprintMB = Int(v)
            if let p = S.number(l, after: S.mPeak) { facts.peakFootprintMB = max(facts.peakFootprintMB ?? 0, Int(p)) }
        }
        if S.find(l, S.mQuit) != nil {
            if !facts.quitRequested { facts.crashEvidenceAtQuit = facts.crashEvidence }
            facts.quitRequested = true
        }
        if S.find(l, S.mReturned) != nil { facts.returnedToLibrary = true }
        if let v = S.number(l, after: S.mExitReport, hex: true) { facts.exitStatus = UInt32(truncatingIfNeeded: v) }
        if S.find(l, S.mUnhandled) != nil || S.find(l, S.mRootFrame) != nil { facts.unhandled += 1 }
        if S.find(l, S.mRuntimeError) != nil { facts.runtimeErrorDialog = true }
        if S.find(l, S.mFreeze) != nil { facts.freezeGaps += 1 }
        if S.find(l, S.mMainStall) != nil { facts.mainStalls += 1 }
        if let v = S.number(l, after: S.mDetachWait) {
            facts.presentsLogged = max(facts.presentsLogged, Int(v))
            if v >= 1 { facts.stage = max(facts.stage, 3) }
            if v >= 600 { facts.stage = max(facts.stage, 4) }   // about 20 s of frames
        }
        if facts.stage < 3, S.find(l, S.mLaunchView) != nil { facts.stage = 3 }
        if S.find(l, S.mDeviceError) != nil { facts.gpuErrors += 1 }
        if (S.find(l, S.mTerminate) != nil && (S.find(l, S.mErrorCode) != nil || S.find(l, S.mWarningCode) != nil))
            || S.find(l, S.mChildNegative) != nil || S.find(l, S.mInitFailed) != nil
            || S.find(l, S.mObjCException) != nil || S.find(l, S.mRedelivKill) != nil { facts.errorExits += 1 }
        if facts.dialogs.count < 4, S.find(l, S.mWinName) != nil, let t = S.find(l, S.mText) {
            let start = t + S.mText.count
            var end = start
            while end < l.count, l[end] != 0x22 { end += 1 }      // the closing quote
            let text = String(decoding: UnsafeRawBufferPointer(rebasing: l[start..<min(end, start + 200)]), as: UTF8.self)
            // A caption alone ("Critical error") says less than the box's own line; keep both, once each.
            if S.errorWords.contains(where: { S.find(UnsafeRawBufferPointer(rebasing: l[start..<end]), $0) != nil }),
               !facts.dialogs.contains(text) { facts.dialogs.append(text) }
        }
    }
}

// MARK: - One session's result

/// What the app knew when the session ended (LibraryModel.finish), or what a
/// leftover session marker says about a run that never came back.
struct AutoTuneSessionEnd: Codable {
    var sawProcess = true
    var quitRequested = false
    var crashStatus: UInt32?
    var presents: Int?
    var seconds: Double = 0
    /// The app did not come back: recovered from a leftover marker at the next start.
    var killed = false
    /// After a killed run the app asks: "closed-by-itself", "froze" or "i-closed".
    var userSaid: String?
    /// Memory limit at launch (phys_footprint + available), for a likely memory kill.
    var limitMB: Int?
}

struct AutoTuneReport: Codable, Equatable {
    /// "crashed", "killed", "hung", "exited-early" (failures); "ran", "quit" (it played);
    /// "inconclusive" (never started, or the user closed the app).
    var outcome: String
    var stage: Int
    var seconds: Double
    var presents: Int
    var peakMB: Int?
    var dock: Bool
    var d3d12: Bool
    var ramMB: Int?
    /// Ids of the rules whose signals this session showed, best first.
    var matched: [String]
    var evidence: [String]
    /// The game's own error boxes (AutoTuneLogFacts.dialogs).
    var dialogs: [String] = []
    /// The Madeira build the session ran ("[build] v0.1.N").
    var build: Int?

    var failed: Bool { ["crashed", "killed", "hung", "exited-early"].contains(outcome) }
    var played: Bool { outcome == "ran" || outcome == "quit" }
}

enum AutoTuneEngine {
    static let maxAttempts = 8

    static func outcome(_ end: AutoTuneSessionEnd, _ f: AutoTuneLogFacts) -> String {
        let crashedInLog = f.unhandled > 0 || f.runtimeErrorDialog || f.exitStatus != nil || f.errorExits > 0
            || (!f.dialogs.isEmpty && f.stage < 4)
        if end.killed {
            if end.userSaid == "i-closed" { return "inconclusive" }
            if end.userSaid == "froze" || f.freezeGaps > 0 || f.gpuErrors > 0
                || (f.quitRequested && !f.returnedToLibrary) { return "hung" }
            if crashedInLog { return "crashed" }
            // The limit the marker measured, else the memory left at launch plus the app's own
            // share then; the last footprint line can be 2 s behind the kill, hence a margin.
            // Kept narrow on purpose: a kill missed here is asked about, a session wrongly
            // called a memory kill would steer the search (HFW 2026-10-13 17:10 ended at
            // 7932 MB of about 8317 and had played).
            if let last = f.lastFootprintMB {
                if let limit = end.limitMB, last >= limit - max(150, limit / 50) { return "killed" }
                if end.limitMB == nil, let avail = f.availableMB {
                    let limit = avail + 256
                    if last >= limit - max(200, limit * 3 / 100) { return "killed" }
                }
            }
            if end.userSaid == "closed-by-itself" { return "killed" }
            return "inconclusive"
        }
        if !end.sawProcess && end.crashStatus == nil { return "inconclusive" }
        // The user quit a game that was on screen: whatever it logged on the way out, it played,
        // unless it had crashed before the user asked (the quit line's moment, when it is logged).
        if end.quitRequested && f.stage >= 3 && (f.crashEvidenceAtQuit ?? 0) == 0 { return "quit" }
        if end.crashStatus != nil || crashedInLog { return "crashed" }
        if end.quitRequested { return "exited-early" }
        return f.stage >= 4 || f.aliveSeconds >= 120 ? "ran" : "exited-early"
    }

    static func report(_ end: AutoTuneSessionEnd, _ scanner: AutoTuneLogScanner,
                       rules: [AutoTuneRule], signals: [AutoTuneSignal]) -> AutoTuneReport {
        let f = scanner.facts
        let out = outcome(end, f)
        var stage = f.stage
        let presents = max(end.presents ?? 0, f.presentsLogged)
        if presents >= 3000 { stage = max(stage, 5) }
        let r = AutoTuneReport(outcome: out, stage: stage, seconds: max(end.seconds, Double(f.aliveSeconds)),
                               presents: presents, peakMB: f.peakFootprintMB, dock: f.dock,
                               d3d12: f.d3d12, ramMB: f.ramMB, matched: [], evidence: scanner.evidence,
                               dialogs: f.dialogs, build: f.build)
        var report = r
        report.matched = rules.filter { matches($0, report: r, counts: scanner.counts, signals: signals) }
            .sorted { ($0.priority, $0.id) < ($1.priority, $1.id) }
            .map(\.id)
        return report
    }

    /// Every signal a rule set needs, deduplicated, in a stable order.
    static func signals(_ rules: [AutoTuneRule]) -> [AutoTuneSignal] {
        var seen = Set<AutoTuneSignal>(), list: [AutoTuneSignal] = []
        for r in rules {
            for s in r.all + (r.any ?? []) + (r.none ?? []) {
                let plain = AutoTuneSignal(has: s.has)
                if seen.insert(plain).inserted { list.append(plain) }
            }
        }
        return list
    }

    static func matches(_ rule: AutoTuneRule, report: AutoTuneReport, counts: [Int],
                        signals: [AutoTuneSignal]) -> Bool {
        func seen(_ s: AutoTuneSignal) -> Bool {
            guard let i = signals.firstIndex(of: AutoTuneSignal(has: s.has)) else { return false }
            return counts[i] >= (s.count ?? 1)
        }
        guard report.failed else { return false }
        if let o = rule.outcomes, !o.contains(report.outcome) { return false }
        if let s = rule.session, s != (report.dock ? "dock" : "library") { return false }
        if let a = rule.api, a != (report.d3d12 ? "d3d12" : "d3d11") { return false }
        if let lo = rule.ramMinMB, (report.ramMB ?? 0) < lo { return false }
        if let hi = rule.ramMaxMB, (report.ramMB ?? Int.max) > hi { return false }
        if let lo = rule.minStage, report.stage < lo { return false }
        if let hi = rule.maxStage, report.stage > hi { return false }
        guard !rule.all.isEmpty, rule.all.allSatisfy(seen) else { return false }
        if let any = rule.any, !any.isEmpty, !any.contains(where: seen) { return false }
        if let none = rule.none, none.contains(where: seen) { return false }
        return true
    }

    /// Did `now` get further than `best`? `tried`: the rule whose lines this session added.
    static func better(_ now: AutoTuneReport, than best: AutoTuneReport, tried: String? = nil) -> Bool {
        if now.played != best.played { return now.played }
        if now.stage != best.stage { return now.stage > best.stage }
        // Same stage: a longer life by half again, and at least 10 s, counts.
        if now.seconds >= best.seconds * 1.5 && now.seconds - best.seconds >= 10 { return true }
        // Same stage, still failing, but the failure the line was for is gone from the log:
        // the line did its job and the game now stops on something else.
        if let tried, now.failed, best.failed, best.matched.contains(tried), !now.matched.contains(tried) { return true }
        return false
    }
}

// MARK: - One game's search

struct AutoTuneAttempt: Codable, Equatable {
    var date: Date
    /// The lines this session ran with on top of the kept ones (empty: the baseline or the retry).
    var lines: [String]
    var rule: String?
    var outcome: String
    var stage: Int
    var seconds: Double
    var presents: Int
    /// nil while undecided or for an inconclusive session.
    var kept: Bool?
    var log: String?
}

struct AutoTuneState: Codable, Equatable {
    var version = 1
    /// "baseline" (play once), "trying", "found" (it plays with the kept lines),
    /// "ok" (it played with nothing added), "stuck" (no known fix fits), "gave-up",
    /// "known" (a crash only a change in Madeira fixes: a rule with fixedIn).
    var status = "baseline"
    var kept: [String] = []
    var candidate: [String]?
    var candidateRule: String?
    var tried: [String] = []
    var retried = false
    var best: AutoTuneReport?
    var attempts: [AutoTuneAttempt] = []
    /// The game's own settings when the baseline ran; a change starts a new baseline.
    var fingerprint: String?
    /// status "known": the rule (one with fixedIn) that named a failure only Madeira can fix,
    /// its reason, the build that fixed it and the build the session ran.
    var known: String?
    var knownReason: String?
    var knownFixedIn: Int?
    var knownBuild: Int?

    /// What to tell the user about a "known" failure.
    var knownAdvice: String {
        var text = "A crash in BCD itself, not a setting: \(knownReason ?? known ?? "")"
        if let fixed = knownFixedIn {
            if let ran = knownBuild, ran >= fixed {
                text += " Build \(fixed) fixed this crash, yet build \(ran) still showed it: export the report to send it."
            } else {
                text += " Fixed in build \(fixed): update BCD."
            }
        }
        return text
    }

    /// The layer file's lines for the next session: a candidate's key replaces a kept one.
    var layerLines: [String] { AutoTuneState.merge(kept, candidate ?? []) }
    static func merge(_ kept: [String], _ add: [String]) -> [String] {
        let keys = Set(add.map(AutoTuneRules.key))
        return kept.filter { !keys.contains(AutoTuneRules.key($0)) } + add
    }
    var decided: Int { attempts.filter { $0.kept != nil && $0.rule != nil }.count }
}

extension AutoTuneEngine {
    /// One session ended: decide on the line it tried and pick the next one.
    /// `userKeys`: keys the game's own file, the entry's lines or bcd.cfg set (never proposed).
    static func advance(_ state: AutoTuneState, report: AutoTuneReport, rules: [AutoTuneRule],
                        userKeys: Set<String>, fingerprint: String, log: String?, now: Date = Date()) -> AutoTuneState {
        var s = state
        if s.fingerprint != fingerprint {
            // The user changed the game's settings: what follows is compared with a new baseline.
            s.fingerprint = fingerprint
            s.best = nil
            s.candidate = nil
            s.candidateRule = nil
            if s.status != "baseline" { s.status = "baseline" }
        }
        var attempt = AutoTuneAttempt(date: now, lines: s.candidate ?? [], rule: s.candidateRule,
                                      outcome: report.outcome, stage: report.stage, seconds: report.seconds,
                                      presents: report.presents, kept: nil, log: log)
        if report.outcome == "inconclusive" {
            s.attempts.append(attempt)
            return s   // the same lines run once more
        }
        if let best = s.best, s.candidateRule != nil {
            let keep = better(report, than: best, tried: s.candidateRule)
            attempt.kept = keep
            if keep {
                s.kept = AutoTuneState.merge(s.kept, s.candidate ?? [])
                s.best = report
            }
            s.candidate = nil
            s.candidateRule = nil
        } else {
            s.best = report
        }
        s.attempts.append(attempt)

        if report.played {
            s.status = s.kept.isEmpty ? "ok" : "found"
            return s
        }
        // The best match is a crash only a change in Madeira fixes: no line can help, so no
        // attempt (not even the unchanged retry) is spent on it.
        if let rule = report.matched.lazy.compactMap({ id in rules.first { $0.id == id } }).first,
           rule.fixedIn != nil {
            s.status = "known"
            s.known = rule.id
            s.knownReason = rule.reason
            s.knownFixedIn = rule.fixedIn
            s.knownBuild = report.build
            s.candidate = nil
            s.candidateRule = nil
            return s
        }
        if s.decided >= maxAttempts {
            s.status = "gave-up"
            return s
        }
        // A failure the first time round may be the first start after an update
        // (four such failures in the history cleared on the second start): run once unchanged.
        if !s.retried && s.kept.isEmpty && s.tried.isEmpty {
            s.retried = true
            s.candidate = []
            s.candidateRule = "retry"
            s.status = "trying"
            return s
        }
        let keptKeys = Set(s.kept.map(AutoTuneRules.key))
        let next = report.matched.compactMap { id in rules.first { $0.id == id } }.first { rule in
            if rule.fixedIn != nil { return false }
            let keys = rule.lines.map(AutoTuneRules.key)
            if s.tried.contains(rule.id) || keys.contains(where: userKeys.contains) { return false }
            // An upgrade raises what auto-tune kept; anything else adds only new keys.
            return rule.upgrade == true ? keys.contains(where: keptKeys.contains) : !keys.contains(where: keptKeys.contains)
        }
        guard let rule = next else {
            s.status = "stuck"
            return s
        }
        s.tried.append(rule.id)
        s.candidate = rule.lines
        s.candidateRule = rule.id
        s.status = "trying"
        return s
    }
}
