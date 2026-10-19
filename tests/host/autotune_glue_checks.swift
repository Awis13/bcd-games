// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// madeira-bcd: auto-tune's app side on the host (docs/AUTO-TUNE.md): app/Madeira/AutoTune.swift
// with AutoTuneCore.swift, the rule table and tests/host/autotune_glue_stubs.swift.
//
//   glue off   run with "-madeira.autotune NO" (UserDefaults' argument domain): every entry point is called with a
//              tuned game, a leftover marker and a state file on disk, and nothing may
//              change: files, MADEIRA_CFG_GAME, log lines, the offer.
//   glue default  no argument for the key: auto-tune is on for the app and for every game.
//   glue on    run with "-madeira.autotune YES" (UserDefaults' argument domain): the launch
//              file, the marker, the analysis after a session, a killed run recovered at
//              the next start, and the question when its log says nothing.
// AUTOTUNE_TEST_CONFIGS, AUTOTUNE_TEST_DOCS and AUTOTUNE_TEST_LOG name temporary paths;
// AUTOTUNE_TEST_CRASH and AUTOTUNE_TEST_SILENT name fixtures (a crash, a silent end).

import Foundation

@main
struct AutoTuneGlueChecks {
    static var failures = 0
    static func expect(_ cond: Bool, _ what: String, line: UInt = #line) {
        if cond { print("ok   \(what)") } else { print("FAIL \(what) (line \(line))"); failures += 1 }
    }
    static let env = ProcessInfo.processInfo.environment
    static let configs = URL(fileURLWithPath: env["AUTOTUNE_TEST_CONFIGS"]!, isDirectory: true)
    static let docs = URL(fileURLWithPath: env["AUTOTUNE_TEST_DOCS"]!, isDirectory: true)
    static let log = URL(fileURLWithPath: env["AUTOTUNE_TEST_LOG"]!)
    static let game = LibraryEntry(windowsPath: "C:\\Games\\Test Game\\Game.exe", title: "Test Game", desktop: nil, gameConfigText: nil)

    /// Lets queued work run: the analysis runs on a utility queue and publishes on main.
    static func settle(until: () -> Bool, seconds: Double = 10) -> Bool {
        let end = Date().addingTimeInterval(seconds)
        while Date() < end {
            if until() { return true }
            RunLoop.main.run(until: Date().addingTimeInterval(0.05))
        }
        return until()
    }

    static func snapshot(_ dirs: [URL]) -> [String: Date] {
        var out: [String: Date] = [:]
        for d in dirs {
            guard let e = FileManager.default.enumerator(at: d, includingPropertiesForKeys: [.contentModificationDateKey]) else { continue }
            for case let u as URL in e {
                out[u.path] = (try? u.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
            }
        }
        return out
    }

    /// What WineProcessBridge prints at launch for the lines this session runs with.
    static func echoCandidate() {
        let lines = AutoTune.state(game.windowsPath).candidate ?? []
        guard !lines.isEmpty, let h = try? FileHandle(forWritingTo: log) else { return }
        h.seekToEndOfFile()
        h.write(Data(lines.map { AutoTune.echo($0) + "\n" }.joined().utf8))
        try? h.close()
    }

    static func copy(_ from: String, to: URL) {
        try? FileManager.default.removeItem(at: to)
        try? FileManager.default.copyItem(at: URL(fileURLWithPath: from), to: to)
    }

    static func main() {
        let mode = CommandLine.arguments.dropFirst().first ?? ""
        // The persistent domain may hold an earlier run's values (cfprefsd does not follow
        // CFFIXED_USER_HOME): start clean, before anything reads AutoTune.enabled. "on"
        // comes from the argument domain only; both keys are removed again at the end.
        UserDefaults.standard.removeObject(forKey: AutoTune.globalKey)
        UserDefaults.standard.removeObject(forKey: AutoTune.gamesKey)
        defer {
            UserDefaults.standard.removeObject(forKey: AutoTune.globalKey)
            UserDefaults.standard.removeObject(forKey: AutoTune.gamesKey)
        }
        let profile = GameProfile(windowsPath: game.windowsPath)
        profile.text = "# the user's own lines\nvram-mb = 3072\nenv.MADEIRA_X = 2\n"
        copy(env["AUTOTUNE_TEST_CRASH"]!, to: log)
        if mode == "off" { off(profile) } else if mode == "default" { byDefault() } else { on(profile) }
        UserDefaults.standard.removeObject(forKey: AutoTune.globalKey)
        UserDefaults.standard.removeObject(forKey: AutoTune.gamesKey)
        print(failures == 0 ? "PASS: auto-tune app side (\(mode))" : "FAILED: auto-tune app side (\(mode), \(failures))")
        exit(failures == 0 ? 0 : 1)
    }

    static func off(_ profile: GameProfile) {
        expect(!AutoTune.enabled, "with -madeira.autotune NO auto-tune is off")
        // Everything a tuned game could have on disk, as if auto-tune had been on before.
        UserDefaults.standard.set([game.windowsPath.lowercased(): true], forKey: AutoTune.gamesKey)
        var st = AutoTuneState()
        st.status = "trying"; st.candidate = ["env.MADEIRA_CAGE_RELEASE = 1"]; st.candidateRule = "cage-release-band-exhausted"
        AutoTune.save(st, game.windowsPath)
        try? Data("{}".utf8).write(to: AutoTune.markerURL!)
        setenv("MADEIRA_CFG_GAME", "/original/path.cfg", 1)
        Thread.sleep(forTimeInterval: 1.1)   // so a rewrite would show in the modification times
        let before = snapshot([configs, docs])

        AutoTune.exportLayer(for: game)
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        AutoTune.sessionEnded(AutoTuneSessionEnd(sawProcess: true, crashStatus: 0xC0000005, seconds: 20))
        AutoTune.recoverUnfinishedSession()
        AutoTune.answer("froze")
        _ = settle(until: { false }, seconds: 1.5)

        expect(snapshot([configs, docs]) == before, "off: no file is written, moved or removed (\(before.count) files)")
        expect(String(cString: getenv("MADEIRA_CFG_GAME")) == "/original/path.cfg", "off: MADEIRA_CFG_GAME is untouched")
        expect(LogStore.shared.lines.isEmpty, "off: no log line (\(LogStore.shared.lines.count))")
        expect(AutoTuneOffers.shared.offer == nil, "off: no offer")
        expect(!FileManager.default.fileExists(atPath: configs.appendingPathComponent("autotune-launch.cfg").path),
               "off: no launch file")
    }

    /// No argument for the key at all, as on a device that never touched the setting: auto-tune is on,
    /// for the app and for every game; a stored off is kept, and Settings' off applies from the next start.
    static func byDefault() {
        expect(UserDefaults.standard.object(forKey: AutoTune.globalKey) == nil, "the setting is absent")
        expect(AutoTune.enabled && AutoTune.preferred, "with the setting absent auto-tune is on")
        expect(AutoTune.isOn(game.windowsPath), "and so is every game")
        AutoTune.preferred = false
        expect(!AutoTune.preferred && AutoTune.enabled, "turned off in Settings: stored now, this run keeps its value")
        AutoTune.preferred = true
        expect(AutoTune.preferred, "and on again")
        AutoTune.setOn(game.windowsPath, false)
        expect(!AutoTune.isOn(game.windowsPath) && AutoTune.isOn("c:\\other\\game.exe"), "a game's own off is kept for that game only")
        AutoTune.setOn(game.windowsPath, true)
        expect(AutoTune.isOn(game.windowsPath), "and its on")
    }

    static func on(_ profile: GameProfile) {
        expect(AutoTune.enabled, "with -madeira.autotune YES auto-tune is on")
        let launch = configs.appendingPathComponent("autotune-launch.cfg")

        // Every game starts with its switch on; the owner of the device can turn it off, and that is kept.
        expect(AutoTune.isOn(game.windowsPath), "a game's own switch is on until it is turned off")
        AutoTune.setOn(game.windowsPath, false)
        expect(!AutoTune.isOn(game.windowsPath) && !AutoTune.isOn(game.windowsPath.uppercased()), "a game turned off stays off, any case")
        expect(AutoTune.isOn(game.windowsPath + "-other"), "turning one game off leaves the others on")
        setenv("MADEIRA_CFG_GAME", profile.url!.path, 1)
        AutoTune.exportLayer(for: game)
        expect(String(cString: getenv("MADEIRA_CFG_GAME")) == profile.url!.path && !FileManager.default.fileExists(atPath: launch.path),
               "a game whose own switch is off runs as before")
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        expect(!FileManager.default.fileExists(atPath: AutoTune.markerURL!.path), "and gets no marker")

        AutoTune.setOn(game.windowsPath, true)
        expect(AutoTune.isOn(game.windowsPath) && AutoTune.isOn(game.windowsPath.uppercased()), "the game's switch is per game, any case")

        // A first session: nothing to add yet, the game runs exactly as configured.
        AutoTune.exportLayer(for: game)
        expect(String(cString: getenv("MADEIRA_CFG_GAME")) == profile.url!.path, "the baseline session runs the game's own file")

        // The baseline crashed (the Rift Apart 23:02 fixture): marker, analysis, offer.
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        expect(FileManager.default.fileExists(atPath: AutoTune.markerURL!.path), "a tuned session writes the marker")
        AutoTune.sessionEnded(AutoTuneSessionEnd(sawProcess: true, seconds: 25))
        expect(settle(until: { AutoTuneOffers.shared.offer != nil }), "the session's analysis publishes an offer")
        var s = AutoTune.state(game.windowsPath)
        expect(s.attempts.count == 1 && s.attempts[0].outcome == "crashed", "the baseline is recorded as crashed")
        expect(s.candidate == [] && s.candidateRule == "retry", "first: the same settings once more")
        // The analysis publishes the offer on the main queue and removes the marker when it
        // returns on its own queue: wait for that too (a loaded Mac lost this race, 2026-10-18).
        expect(settle(until: { !FileManager.default.fileExists(atPath: AutoTune.markerURL!.path) }),
               "the marker is gone after the analysis")
        expect(AutoTuneOffers.shared.offer?.state.candidate == [], "the offer says what the next start runs")
        AutoTuneOffers.shared.offer = nil

        // The retry: the same crash -> the first rule.
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        AutoTune.sessionEnded(AutoTuneSessionEnd(sawProcess: true, seconds: 25))
        expect(settle(until: { AutoTuneOffers.shared.offer != nil }), "the retry is analysed")
        s = AutoTune.state(game.windowsPath)
        expect(s.candidate == ["env.MADEIRA_CAGE_RELEASE = 1"], "then the cage line (\(s.candidate ?? []))")
        AutoTuneOffers.shared.offer = nil

        // The launch file: the candidate under the game's own lines, a key the user sets is left out.
        var forced = s
        forced.kept = ["vram-mb = 4096"]   // as if kept before the user set vram-mb
        AutoTune.save(forced, game.windowsPath)
        AutoTune.exportLayer(for: game)
        let text = (try? String(contentsOf: launch, encoding: .utf8)) ?? ""
        expect(String(cString: getenv("MADEIRA_CFG_GAME")) == launch.path, "MADEIRA_CFG_GAME names the launch file")
        expect(text.hasPrefix("# BCD auto-tune, attempt"), "the launch file says what it is")
        expect(!text.split(separator: "\n").contains { $0.hasPrefix("#") && $0.range(of: "madeira", options: .caseInsensitive) != nil },
               "its comment lines say nothing of Madeira")
        expect(text.contains("env.MADEIRA_CAGE_RELEASE = 1\n"), "it carries the candidate")
        expect(!text.contains("vram-mb = 4096"), "a key the game's own file sets is never added")
        if let c = text.range(of: "env.MADEIRA_CAGE_RELEASE = 1"), let own = text.range(of: "vram-mb = 3072") {
            expect(c.lowerBound < own.lowerBound, "the game's own lines come after it, so they win")
        } else { expect(false, "the game's own lines are in the launch file") }
        expect(LogStore.shared.lines.contains { $0.hasPrefix("[autotune] Test Game: this session adds") }, "the launch says what it adds")
        AutoTune.save(s, game.windowsPath)

        // Not eligible: the Desktop.
        setenv("MADEIRA_CFG_GAME", "/desktop.cfg", 1)
        AutoTune.exportLayer(for: LibraryEntry(windowsPath: game.windowsPath, title: "Desktop", desktop: true, gameConfigText: nil))
        expect(String(cString: getenv("MADEIRA_CFG_GAME")) == "/desktop.cfg", "the Desktop is never tuned")
        // A Dock session without a library entry is never armed.
        AutoTune.sessionBegan(game, remember: false, dockWithoutEntry: true)
        expect(!FileManager.default.fileExists(atPath: AutoTune.markerURL!.path), "a Dock start without an entry gets no marker")

        // A session whose log never shows the candidate (it did not reach the game) counts for nothing.
        let notApplied = AutoTune.state(game.windowsPath)
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        AutoTune.sessionEnded(AutoTuneSessionEnd(sawProcess: true, seconds: 25))
        expect(settle(until: { AutoTune.state(game.windowsPath).attempts.count == notApplied.attempts.count + 1 }),
               "a session without the candidate in its log is analysed")
        let na = AutoTune.state(game.windowsPath)
        expect(na.attempts.last?.outcome == "inconclusive" && na.candidate == notApplied.candidate && na.kept == notApplied.kept,
               "and counts for nothing: the same candidate runs next time")
        expect(LogStore.shared.lines.contains { $0.contains("the session ran without env.MADEIRA_CAGE_RELEASE = 1") },
               "the log says the line did not reach the session")
        AutoTuneOffers.shared.offer = nil

        // A killed run: the marker is left behind, the next start finds the log by its inode.
        let logs = docs.appendingPathComponent("logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        // The session ran with the candidate: WineProcessBridge echoes it at launch.
        if let h = try? FileHandle(forWritingTo: log) {
            h.seekToEndOfFile(); h.write(Data("[madeira-env] game MADEIRA_CAGE_RELEASE=1\n".utf8)); try? h.close()
        }
        let hardLink = logs.appendingPathComponent("Game.exe-2026-10-16_10-00-00.txt")
        try? FileManager.default.linkItem(at: log, to: hardLink)   // what LogStore.startSessionLog does
        try? FileManager.default.removeItem(at: log)                 // the next start rotated bcd-log.txt away
        let attemptsBefore = AutoTune.state(game.windowsPath).attempts.count
        AutoTune.recoverUnfinishedSession()
        expect(settle(until: { AutoTune.state(game.windowsPath).attempts.count == attemptsBefore + 1 }),
               "a killed run is analysed at the next start from its logs/ hard link")
        expect(AutoTune.state(game.windowsPath).attempts.last?.outcome == "crashed", "its crash is recognised from the log alone")
        expect(!FileManager.default.fileExists(atPath: AutoTune.markerURL!.path)
               && !FileManager.default.fileExists(atPath: configs.appendingPathComponent("autotune-session.pending.json").path),
               "the marker and its pending copy are gone")
        AutoTuneOffers.shared.offer = nil

        // A killed run whose log says nothing: the user is asked, and the answer decides.
        copy(env["AUTOTUNE_TEST_SILENT"]!, to: log)
        echoCandidate()
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        let before = AutoTune.state(game.windowsPath).attempts.count
        // The next start: LogStore rotates bcd-log.txt to bcd-log.prev.txt (same inode).
        let prev = docs.appendingPathComponent("bcd-log.prev.txt")
        try? FileManager.default.removeItem(at: prev)
        try? FileManager.default.moveItem(at: log, to: prev)
        AutoTune.recoverUnfinishedSession()
        expect(settle(until: { AutoTuneOffers.shared.offer?.ask == true }), "a silent end asks what happened")
        expect(AutoTune.state(game.windowsPath).attempts.count == before, "nothing is decided before the answer")
        AutoTuneOffers.shared.offer = nil
        AutoTune.answer("froze")
        expect(settle(until: { AutoTune.state(game.windowsPath).attempts.count == before + 1 }), "the answer is analysed")
        expect(AutoTune.state(game.windowsPath).attempts.last?.outcome == "hung", "\"It froze\" makes it a hang")
        AutoTune.answer("froze")
        _ = settle(until: { false }, seconds: 0.5)
        expect(AutoTune.state(game.windowsPath).attempts.count == before + 1, "a second answer does nothing")

        // A game that already plays is not asked about after a silent end.
        AutoTuneOffers.shared.offer = nil
        var playing = AutoTune.state(game.windowsPath)
        playing.status = "ok"; playing.candidate = nil; playing.candidateRule = nil
        AutoTune.save(playing, game.windowsPath)
        copy(env["AUTOTUNE_TEST_SILENT"]!, to: log)
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        try? FileManager.default.removeItem(at: prev)
        try? FileManager.default.moveItem(at: log, to: prev)
        AutoTune.recoverUnfinishedSession()
        _ = settle(until: { false }, seconds: 1.5)
        expect(AutoTuneOffers.shared.offer == nil && AutoTune.state(game.windowsPath) == playing,
               "a game that plays is not asked about a silent end, and nothing changes")

        expect(AutoTune.echo("env.MADEIRA_CAGE_RELEASE = 1") == "[madeira-env] game MADEIRA_CAGE_RELEASE=1"
               && AutoTune.echo("swap-mb = 6144") == "[game-cfg]   swap-mb = 6144",
               "the echo matches WineProcessBridge.m's two formats")

        // Undo all.
        AutoTune.reset(game.windowsPath)
        expect(AutoTune.state(game.windowsPath) == AutoTuneState(), "Undo all removes the lines and the history")
        // Turning it off: this run keeps its value (the argument domain also keeps "preferred"
        // on here), and a marker on disk is removed so the next start finds nothing to recover.
        try? Data("{}".utf8).write(to: AutoTune.markerURL!)
        AutoTune.preferred = false
        expect(AutoTune.enabled, "turning it off applies from the next start")
        expect(!FileManager.default.fileExists(atPath: AutoTune.markerURL!.path), "turning it off removes a session marker")
    }
}
