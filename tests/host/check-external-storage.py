#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The external SSD games folder (Settings > Storage > External SSD, C:\\External; docs/EXTERNAL-STORAGE.md).

Based on thebcd69/bcd-games#12 by Pardoxum ("Support games stored on USB-C external SSDs"): the Foundation
harness below grows from the one in that pull request, which exercised the production class with real folders,
symlinks and bookmarks. BCD's additions (2026-10-24): the opt-in proof, the one-tap reconnect, the safety cases.

Part 1 reads the sources (any host):
  - Opt-in by use. The only call at launch is ExternalGameStorage.restore(), whose first statement is one
    UserDefaults read. Every hook in the library code keeps its original test first and asks the SSD class only
    about what that test refuses (`original || ExternalGameStorage.shared.admits(...)`), or is conditioned on a
    saved folder. The set of files that name the class is fixed here: a new hook is a deliberate edit.
  - The folder picker is attached to the External SSD group, not to the page (which carries the saves backup's
    importer), asks for folders only, and the settings screen names no engine type.
  - Only the probe file and BCD's own link are ever removed; the probe is the only file written.
Part 2 (macOS with swift; --require-swift makes it mandatory, as CI does) compiles the production class and the
real library scanner (GameLibrary, with its two SSD lines turned back into the original ones as the reference) and
runs real folders:
  - Unused: restore() and every query leave the disk, the defaults, the log and the scope alone, and give
    exactly the answers the original code gave (also for a player's own local C:\\External folder).
  - Used: games on the SSD are listed and read; local paths keep their answers; links that escape stay out.
  - Reconnect: the saved bookmark opens again without the picker; a moved folder (stale bookmark) is relinked; a
    bookmark that does not resolve asks for the picker and changes nothing; restore() never writes.
  - Safety: no user file is deleted, replaced or overwritten by connecting, reconnecting or refusing.
Run: python3 tests/host/check-external-storage.py [--require-swift]
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()

failed = []


def check(what, ok):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failed.append(what)


def read(path):
    return (root / path).read_text(encoding="utf-8")


storage_src = read("app/Madeira/StorageModel.swift")
start = storage_src.index("final class ExternalGameStorage {")
end = storage_src.index("struct StorageItem:")
manager = storage_src[start:end]

# ---------------------------------------------------------------- part 1: the sources

# Launch: one call, and its first statement is one UserDefaults read.
app = read("app/Madeira/MadeiraApp.swift")
check("launch calls ExternalGameStorage.shared.restore() once", app.count("ExternalGameStorage.shared.restore()") == 1)
restore = manager[manager.index("func restore() {"):]
restore = restore[:restore.index("\n    }\n") + 7]
check("restore() starts with the single UserDefaults read and returns when no folder was chosen",
      re.match(r"func restore\(\) \{\n\s+guard defaults\.data\(forKey: key\) != nil else \{ return \}\n", restore) is not None)
check("restore() restores without the write probe", "attachSaved(verifyWrite: false)" in restore)

# The files that name the class: a fixed set.
named = sorted(str(p.relative_to(root)) for p in (root / "app").rglob("*.swift") if "ExternalGameStorage" in p.read_text(encoding="utf-8"))
expected = ["app/Madeira/BCDAddGameLive.swift", "app/Madeira/BCDLiveStore.swift", "app/Madeira/BCDSettingsLive.swift",
            "app/Madeira/ContentView.swift", "app/Madeira/GameLibrary.swift", "app/Madeira/Library.swift",
            "app/Madeira/MadeiraApp.swift", "app/Madeira/StorageModel.swift"]
check("only the expected files name ExternalGameStorage (a new hook is a deliberate edit)", named == expected)
check("the settings screen (also built in the preview) names no engine type",
      "ExternalGameStorage" not in read("app/Madeira/BCDSettingsSections.swift"))

# Hooks: the original test first.
hooks = {
    "app/Madeira/BCDAddGameLive.swift": [
        'url.resolvingSymlinksInPath().path.hasPrefix(drive.path + "/") || ExternalGameStorage.shared.admits(url)',
        "if let linked = ExternalGameStorage.shared.linkedChildren(of: base) {"],
    "app/Madeira/Library.swift": [
        'guard url.path.hasPrefix(drive.path + "/") || ExternalGameStorage.shared.admits(url),',
        'guard folder.path.hasPrefix(drive.path + "/") || ExternalGameStorage.shared.admits(folder), !Task.isCancelled else',
        'guard parent.path.hasPrefix(drive.path + "/") || ExternalGameStorage.shared.admits(parent),',
        'if file.resolvingSymlinksInPath().path.hasPrefix(drive.path + "/") || ExternalGameStorage.shared.admits(file)',
        '($0.resolvingSymlinksInPath().path.hasPrefix(drive.path + "/") || ExternalGameStorage.shared.admits($0))',
        "guard let relative = ExternalGameStorage.shared.relativePath(for: url, drive: drive) else {",
        "countBytes && !ExternalGameStorage.shared.isMountRoot(installation)"],
    "app/Madeira/GameLibrary.swift": [
        "guard let entries = ExternalGameStorage.shared.linkedChildren(of: dir) ?? (try? fm.contentsOfDirectory(",
        'let externalMount = depth == 0 && entry.lastPathComponent == "External" && ExternalGameStorage.shared.admits(entry)',
        "let values = try? (externalMount ? entry.resolvingSymlinksInPath() : entry).resourceValues(forKeys: [.isDirectoryKey, .fileSizeKey])",
        "title: externalMount ? nil : title ?? titleFor(entry))"],
    "app/Madeira/ContentView.swift": [
        'if entry.relativePath.hasPrefix("External/"), ExternalGameStorage.shared.hasSavedFolder {'],
    "app/Madeira/BCDLiveStore.swift": [
        'if entry.relativePath.hasPrefix("External/"), ExternalGameStorage.shared.hasSavedFolder { game.place = .externalDrive }'],
}
for path, needles in hooks.items():
    text = read(path)
    for needle in needles:
        check(f"{path.split('/')[-1]}: hook {needle[:70]}...", text.count(needle) == 1)
library = read("app/Madeira/Library.swift")
check("Library.swift keeps the original size-walk stop list (the SSD folder is the isMountRoot test, not a name)",
      '["program files", "program files (x86)", "games", "common", "steamapps"].contains(parent.lastPathComponent.lowercased())' in library)
check("no code outside the class reads the bookmark key",
      read("app/Madeira/BCDSettingsLive.swift").count("bcd.external-games") == 0 and library.count("bcd.external-games") == 0)

# Settings: the picker.
sections = read("app/Madeira/BCDSettingsSections.swift")
group = sections[sections.index("private var externalGames: some View {"):sections.index("    private var games: [BCDGame] {")]
check("the folder picker is attached to the External SSD group",
      ".fileImporter(isPresented: $choosingExternalGames, allowedContentTypes: [.folder])" in group)
check("the picker asks for folders only", group.count("allowedContentTypes:") == 1)
check("the saves backup's importer is still alone on the page",
      sections.count(".fileImporter(isPresented: $choosingBackup, allowedContentTypes: [.zip])") == 1
      and sections.count(".fileImporter(") == 2)
check("the group is shown only where the backend supports it", "if backend.supportsExternalGames { externalGames }" in sections)
check("the picker's open, answer and close are logged (a silent Open shows in the log)",
      'noteExternalPicker("opened")' in group and 'noteExternalPicker("answered")' in group and 'noteExternalPicker("closed")' in group)
check("Reconnect tries the saved folder before the picker", "backend.reconnectExternalGames {" in group)
live = read("app/Madeira/BCDSettingsLive.swift")
check("folder work runs off the main thread", live.count("DispatchQueue.global(qos: .userInitiated).async {") >= 2
      and "ExternalGameStorage.shared.connect(url)" in live and "ExternalGameStorage.shared.reconnect()" in live)
check("the external drive's free space stays unset in the store (installs still go to the device)",
      "system.freeExternalBytes = nil" in read("app/Madeira/BCDLiveStore.swift"))

# The class: what it removes and writes.
removed = sorted(set(re.findall(r"removeItem\(at: (\w+)\)", manager)))
check("the class removes only the probe file and BCD's own link", removed == ["link", "probe"])
check("the class writes only the probe (a new, empty, uniquely named file, never overwriting)",
      manager.count(".write(to:") == 1 and 'Data().write(to: probe, options: .withoutOverwriting)' in manager
      and '".bcd-write-test-" + UUID().uuidString' in manager)
check("only BCD's own link is replaced: the saved path must equal the link's destination",
      'guard let previous, previous == defaults.string(forKey: key + ".path") else {' in manager)
check("a link that already leads to the chosen folder is left alone", "if previous == target.path {" in manager)

# Docs and CI.
doc = read("docs/EXTERNAL-STORAGE.md")
check("docs/EXTERNAL-STORAGE.md credits Pardoxum and names the PR", "Pardoxum" in doc and "thebcd69/bcd-games#12" in doc)
ci = read(".github/workflows/build-ipa.yml")
check("CI runs this check", "run: python3 tests/host/check-external-storage.py --require-swift" in ci)

# ---------------------------------------------------------------- part 2: real folders (macOS + swift)

# The real library scanner, and the same text with the two SSD lines turned back into the original lines.
game_src = read("app/Madeira/GameLibrary.swift")
scanner = game_src[game_src.index("struct GuestExecutable"):game_src.index("// MARK: - Fixed-base images")]
drive_property = """    static var driveC: URL? {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first?
            .appendingPathComponent("wine")
            .appendingPathComponent("drive_c")
    }
"""
check("GameLibrary.driveC is the Documents/wine/drive_c property the harness replaces", scanner.count(drive_property) == 1)
scanner = scanner.replace(drive_property, "    static var driveC: URL?\n")
hook_lines = [
    ('''                let externalMount = depth == 0 && entry.lastPathComponent == "External" && ExternalGameStorage.shared.admits(entry)
                let values = try? (externalMount ? entry.resolvingSymlinksInPath() : entry).resourceValues(forKeys: [.isDirectoryKey, .fileSizeKey])
''', '''                let values = try? entry.resourceValues(forKeys: [.isDirectoryKey, .fileSizeKey])
'''),
    ("title: externalMount ? nil : title ?? titleFor(entry))", "title: title ?? titleFor(entry))"),
    ("""            guard let entries = ExternalGameStorage.shared.linkedChildren(of: dir) ?? (try? fm.contentsOfDirectory(
                at: dir,
                includingPropertiesForKeys: [.isDirectoryKey, .fileSizeKey],
                options: [.skipsHiddenFiles]
            )) else { return }
""", """            guard let entries = try? fm.contentsOfDirectory(
                at: dir,
                includingPropertiesForKeys: [.isDirectoryKey, .fileSizeKey],
                options: [.skipsHiddenFiles]
            ) else { return }
"""),
]
legacy = scanner
for new, old in hook_lines:
    check("GameLibrary hook text is as the harness expects: " + new.strip().splitlines()[0][:60], legacy.count(new) == 1)
    legacy = legacy.replace(new, old)
legacy = re.sub(r"\bGuestExecutable\b", "LegacyGuestExecutable", legacy)
legacy = re.sub(r"\bGameLibrary\b", "LegacyGameLibrary", legacy)
check("the harness's legacy scanner no longer calls the SSD class", "ExternalGameStorage.shared" not in legacy)

stored = manager.replace("static let shared = ExternalGameStorage()", "static var shared = ExternalGameStorage()")
check("the production singleton is the line the harness swaps", stored != manager)

stubs = r'''
import Foundation

final class LogStore {
    static let shared = LogStore()
    private let lock = NSLock()
    private var collected: [String] = []
    var lines: [String] { lock.lock(); defer { lock.unlock() }; return collected }
    func log(_ message: String) { lock.lock(); collected.append(message); lock.unlock() }
    func reset() { lock.lock(); collected = []; lock.unlock() }
}
'''

tests = r'''
let fm = FileManager.default
var checks = 0, failures = 0
func expect(_ condition: @autoclosure () -> Bool, _ what: String, line: Int = #line) {
    checks += 1
    if !condition() { failures += 1; print("FAIL (line \(line)): \(what)") }
}
func throwsError(_ action: () throws -> Void) -> NSError? {
    do { try action(); return nil } catch { return error as NSError }
}

final class Counters { var opens = 0; var closes = 0; var open: Int { opens - closes } }

final class World {
    let temp: URL, drive: URL, ssd: URL, other: URL, suite: String, defaults: UserDefaults
    let counters = Counters()
    init() {
        temp = fm.temporaryDirectory.appendingPathComponent("bcd-ext-" + UUID().uuidString)
        drive = temp.appendingPathComponent("Documents/wine/drive_c")
        ssd = temp.appendingPathComponent("SSD/Games")
        other = temp.appendingPathComponent("Other")
        suite = "bcd-external-test-" + UUID().uuidString
        defaults = UserDefaults(suiteName: suite)!
        for dir in [drive, ssd, other] { try! fm.createDirectory(at: dir, withIntermediateDirectories: true) }
        GameLibrary.driveC = drive
        LegacyGameLibrary.driveC = drive
        LogStore.shared.reset()
    }
    var link: URL { drive.appendingPathComponent("External") }
    /// What Library.drive is: drive_c with links resolved.
    var driveResolved: URL { drive.resolvingSymlinksInPath() }
    func storage() -> ExternalGameStorage {
        let c = counters
        return ExternalGameStorage(defaults: defaults, beginAccess: { _ in c.opens += 1; return true }, endAccess: { _ in c.closes += 1 })
    }
    func put(_ path: String, in base: URL, _ text: String = "MZ") {
        let url = base.appendingPathComponent(path)
        try! fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try! Data(text.utf8).write(to: url)
    }
    func cleanup() {
        for dir in [ssd, other] { try? fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: dir.path) }
        try? fm.removeItem(at: temp)
        defaults.removePersistentDomain(forName: suite)
    }
}

/// Every file, folder and link below `root` without following links: name, kind, size, link destination, content.
func snapshot(_ root: URL) -> [String] {
    var out: [String] = []
    func walk(_ dir: URL, _ prefix: String) {
        let names = ((try? fm.contentsOfDirectory(atPath: dir.path)) ?? []).sorted()
        for name in names {
            let url = dir.appendingPathComponent(name)
            let attrs = (try? fm.attributesOfItem(atPath: url.path)) ?? [:]
            let kind = attrs[.type] as? FileAttributeType
            if kind == .typeSymbolicLink {
                out.append("\(prefix)\(name) -> \((try? fm.destinationOfSymbolicLink(atPath: url.path)) ?? "?")")
            } else if kind == .typeDirectory {
                out.append("\(prefix)\(name)/")
                walk(url, prefix + name + "/")
            } else {
                let text = (try? String(contentsOf: url, encoding: .isoLatin1)) ?? "?"
                out.append("\(prefix)\(name) \(attrs[.size] ?? 0) \(text.prefix(40))")
            }
        }
    }
    walk(root, "")
    return out
}

/// What the library tested before the SSD hooks (Library.inspect and BCDAddGameLive.inside), verbatim.
func legacyRelative(_ url: URL, drive: URL) -> String? {
    guard url.resolvingSymlinksInPath().path.hasPrefix(drive.path + "/") else { return nil }
    return String(url.resolvingSymlinksInPath().path.dropFirst(drive.path.count + 1))
}
/// LibraryModel.executable's test, verbatim.
func legacyExecutable(_ relative: String, drive: URL) -> Bool {
    let url = drive.appendingPathComponent(relative).resolvingSymlinksInPath().standardizedFileURL
    return url.path.hasPrefix(drive.path + "/") && fm.fileExists(atPath: url.path)
}

/// A drive_c of a player who never used the SSD feature, with the traps: a real local C:\External, a link
/// inside drive_c, a link out of it.
func buildLocalDrive(_ w: World) {
    w.put("Games/Alpha/alpha.exe", in: w.drive)
    w.put("Games/Beta/bin64/beta.exe", in: w.drive)
    w.put("users/test/Desktop/tool.exe", in: w.drive)
    w.put("windows/system32/notepad.exe", in: w.drive)
    w.put("External/Mine/mine.exe", in: w.drive)       // the player's own folder named External
    w.put("evil.exe", in: w.other)
    try! fm.createSymbolicLink(at: w.drive.appendingPathComponent("links"), withDestinationURL: w.drive.appendingPathComponent("Games"))
    try! fm.createSymbolicLink(at: w.drive.appendingPathComponent("outside"), withDestinationURL: w.other)
}

func scanWords(_ list: [GuestExecutable]) -> [String] { list.map { "\($0.title)|\($0.windowsPath)" }.sorted() }
func legacyScanWords(_ list: [LegacyGuestExecutable]) -> [String] { list.map { "\($0.title)|\($0.windowsPath)" }.sorted() }

/// A battery of paths: the same answer as the original code for each.
func localURLs(_ w: World) -> [URL] {
    let d = w.drive
    return [d.appendingPathComponent("Games/Alpha/alpha.exe"), d.appendingPathComponent("Games/Alpha/missing.exe"),
            d.appendingPathComponent("Games/Beta/bin64"), d.appendingPathComponent("Games/../Games/Alpha/alpha.exe"),
            d.appendingPathComponent("links/Alpha/alpha.exe"), d.appendingPathComponent("outside/evil.exe"),
            d.appendingPathComponent("outside"), d, w.driveResolved, d.deletingLastPathComponent(), w.other.appendingPathComponent("evil.exe"),
            d.appendingPathComponent("users/test/Desktop/tool.exe"), d.appendingPathComponent("ExternalOther/x.exe"),
            w.temp, URL(fileURLWithPath: "/")]
}

// ---------------------------------------------------------------- 1. unused: nothing changes

do {
    let w = World(); defer { w.cleanup() }
    buildLocalDrive(w)
    let storage = w.storage()
    ExternalGameStorage.shared = storage
    let before = snapshot(w.temp)
    let driveBefore = snapshot(w.drive)

    storage.restore()
    expect(!storage.hasSavedFolder, "1: no folder saved")
    expect(storage.savedFolderName == nil, "1: no saved folder name")
    expect(!storage.isConnected, "1: not connected")
    expect(storage.freeBytes == nil, "1: no free-space reading")
    expect(w.counters.opens == 0 && w.counters.closes == 0, "1: no security scope opened or closed")
    expect(LogStore.shared.lines.isEmpty, "1: restore() logs nothing")
    expect(w.defaults.dictionaryRepresentation().keys.filter { $0.hasPrefix("bcd.external") }.isEmpty, "1: no key written to the defaults")
    expect(snapshot(w.temp) == before, "1: restore() changes nothing on disk (no link, no folder, no probe)")

    for url in localURLs(w) {
        let name = url.path.replacingOccurrences(of: w.temp.path, with: "<t>")
        expect(storage.relativePath(for: url, drive: w.driveResolved) == legacyRelative(url, drive: w.driveResolved), "1: relativePath equals the original test for \(name)")
        expect(!storage.admits(url), "1: admits() is false at once for \(name)")
        expect(!storage.isMountRoot(url), "1: isMountRoot is false for \(name)")
    }
    // A player's own local C:\External folder is just another folder.
    let own = w.drive.appendingPathComponent("External/Mine/mine.exe")
    expect(storage.relativePath(for: own, drive: w.driveResolved) == "External/Mine/mine.exe", "1: the player's own C:\\External\\Mine\\mine.exe keeps its relative path")
    expect(!storage.admits(own) && !storage.isMountRoot(w.drive.appendingPathComponent("External")), "1: ...and is not the SSD")
    expect(legacyExecutable("External/Mine/mine.exe", drive: w.driveResolved), "1: ...and LibraryModel.executable's original test accepts it")

    // The scanner gives the original's answer.
    let now = scanWords(GameLibrary.scan()), then = legacyScanWords(LegacyGameLibrary.scan())
    expect(now == then, "1: GameLibrary.scan() equals the original scanner on a drive with links and a local External folder")
    expect(now.contains("External|C:\\External\\Mine\\mine.exe"), "1: the player's own External folder is listed under the title External, as before")
    expect(!now.contains(where: { $0.contains("links") || $0.contains("evil") || $0.contains("notepad") }), "1: links and Wine's own folders stay out")
    expect(snapshot(w.drive) == driveBefore, "1: the scans change nothing")
    expect(w.counters.opens == 0, "1: still no scope opened")
    expect(LogStore.shared.lines.isEmpty, "1: still no log line")
}

// ---------------------------------------------------------------- 2. choosing a folder (the pull request's cases)

do {
    let w = World(); defer { w.cleanup() }
    let storage = w.storage(); ExternalGameStorage.shared = storage
    let userFile = w.link.appendingPathComponent("keep.txt")
    try! fm.createDirectory(at: w.link, withIntermediateDirectories: true)
    try! Data("keep".utf8).write(to: userFile)

    // A user's own C:\External folder is never replaced.
    let e1 = throwsError { try storage.connect(w.ssd) }
    expect(e1 != nil && e1!.domain == ExternalGameStorage.errorDomain && e1!.code == 1, "2: an existing folder C:\\External refuses the SSD")
    expect((try? String(contentsOf: userFile, encoding: .utf8)) == "keep", "2: ...and its file is kept")
    expect(w.counters.open == 0, "2: ...with the scope closed again")
    expect(!storage.hasSavedFolder && !storage.isConnected, "2: ...and nothing saved")
    try! fm.removeItem(at: w.link)

    try! storage.connect(w.ssd)
    expect(w.counters.open == 1, "2: connecting keeps one scope open")
    expect(storage.hasSavedFolder && storage.isConnected && storage.savedFolderName == "Games", "2: saved and connected as Games")
    expect(storage.message.contains("connected as C:\\External"), "2: the message says it is connected")
    expect(storage.freeBytes != nil, "2: free space is read")
    w.put("Game/game.exe", in: w.ssd)
    let exe = w.ssd.appendingPathComponent("Game/game.exe")
    let d = w.driveResolved
    expect(storage.relativePath(for: exe, drive: d) == "External/Game/game.exe", "2: a program on the SSD is External/Game/game.exe")
    expect(storage.relativePath(for: w.link.appendingPathComponent("Game/game.exe"), drive: d) == "External/Game/game.exe", "2: ...through C:\\External too")
    expect(storage.relativePath(for: w.link.appendingPathComponent("Game/new.exe"), drive: d) == "External/Game/new.exe", "2: a missing file below the mount keeps its place")
    expect(storage.relativePath(for: d.appendingPathComponent("Local/game.exe"), drive: d) == "Local/game.exe", "2: a local program keeps its path")
    expect(storage.relativePath(for: w.other, drive: d) == nil, "2: another folder is outside")
    expect(storage.admits(exe) && storage.admits(w.link) && storage.isMountRoot(w.link) && storage.isMountRoot(w.ssd), "2: admits() and isMountRoot() know the mount")
    expect(!storage.isMountRoot(exe) && !storage.admits(w.other), "2: ...and nothing else")

    // Links that escape stay out.
    let escape = w.ssd.appendingPathComponent("escape")
    try! fm.createSymbolicLink(at: escape, withDestinationURL: w.other)
    w.put("game.exe", in: w.other)
    expect(storage.relativePath(for: escape.appendingPathComponent("game.exe"), drive: d) == nil, "2: a link on the SSD to another folder is rejected")
    expect(!storage.admits(escape.appendingPathComponent("game.exe")), "2: ...also by admits()")
    expect(storage.relativePath(for: w.ssd.appendingPathComponent("../private.exe"), drive: d) == nil, "2: .. cannot leave the SSD folder")
    let toLocal = w.ssd.appendingPathComponent("toLocal")
    w.put("Games/Alpha/alpha.exe", in: w.drive)
    try! fm.createSymbolicLink(at: toLocal, withDestinationURL: w.drive.appendingPathComponent("Games"))
    expect(storage.relativePath(for: toLocal.appendingPathComponent("Alpha/alpha.exe"), drive: d) == "Games/Alpha/alpha.exe", "2: a link from the SSD into drive_c is a drive_c program")

    // Folders that must not be chosen.
    let snapshotBefore = snapshot(w.temp)
    let refusedFolders: [(String, URL)] = [
        ("drive_c itself", w.drive), ("its parent", w.temp), ("Documents", w.temp.appendingPathComponent("Documents")),
        ("a folder inside drive_c", w.drive.appendingPathComponent("Games")),
        ("a missing folder", w.ssd.appendingPathComponent("missing")), ("a file", exe)]
    for (what, bad) in refusedFolders {
        expect(throwsError { try storage.connect(bad) } != nil, "2: \(what) is refused")
    }
    expect(snapshot(w.temp) == snapshotBefore, "2: refused folders change nothing on disk")
    expect(w.counters.open == 1, "2: ...and leave exactly the one scope open")
    expect((try? Data(contentsOf: w.link.appendingPathComponent("Game/game.exe"))) == Data("MZ".utf8), "2: the SSD's files are intact")

    // The picker's temporary copy is not the SSD.
    let inbox = w.temp.appendingPathComponent("tmp/com.example-Inbox/Games")
    try! fm.createDirectory(at: inbox, withIntermediateDirectories: true)
    let e2 = throwsError { try storage.connect(inbox) }
    expect(e2 != nil && e2!.localizedDescription.contains("temporary copy"), "2: a picker copy in the -Inbox is refused")

    // Choosing the same folder again keeps the link; choosing another one replaces only BCD's own link.
    try! storage.connect(w.ssd)
    expect(w.counters.open == 1, "2: the same folder again keeps one scope")
    expect((try? fm.destinationOfSymbolicLink(atPath: w.link.path)) == w.ssd.resolvingSymlinksInPath().path, "2: C:\\External leads to the folder")
    let second = w.temp.appendingPathComponent("SSD/Second")
    w.put("Other/other.exe", in: second)
    let ssdBefore = snapshot(w.ssd)
    try! storage.connect(second)
    expect(w.counters.open == 1, "2: another folder moves the scope, it does not add one")
    expect(snapshot(w.ssd) == ssdBefore, "2: replacing the link leaves the old folder untouched")
    expect(storage.relativePath(for: second.appendingPathComponent("Other/other.exe"), drive: d) == "External/Other/other.exe", "2: the new folder is C:\\External")
    expect(storage.relativePath(for: exe, drive: d) == nil, "2: the old folder is no longer a mount")
    let restored = ExternalGameStorage(defaults: w.defaults, beginAccess: { _ in true }, endAccess: { _ in })
    restored.restore()
    expect(restored.relativePath(for: second.appendingPathComponent("Other/other.exe"), drive: d) == "External/Other/other.exe", "2: a new instance restores the saved folder")
    expect(restored.freeBytes != nil, "2: ...and reads its free space")
}

// A link BCD does not own is never replaced.
do {
    let w = World(); defer { w.cleanup() }
    let storage = w.storage(); ExternalGameStorage.shared = storage
    w.put("Game/game.exe", in: w.ssd)
    try! fm.createSymbolicLink(at: w.link, withDestinationURL: w.other)
    let e = throwsError { try storage.connect(w.ssd) }
    expect(e != nil && e!.code == 1, "3: a foreign link C:\\External refuses the SSD")
    expect((try? fm.destinationOfSymbolicLink(atPath: w.link.path)) == w.other.path, "3: ...and keeps its destination")
    expect(storage.relativePath(for: w.ssd.appendingPathComponent("Game/game.exe"), drive: w.driveResolved) == nil, "3: ...and the SSD is not mounted")
    try! fm.removeItem(at: w.link)
    try! fm.createDirectory(at: w.link, withIntermediateDirectories: true)
    expect(throwsError { try storage.connect(w.ssd) } != nil, "3: a plain folder C:\\External refuses the SSD")
    expect(fm.fileExists(atPath: w.link.path), "3: ...and stays")
    expect(w.counters.open == 0, "3: no scope left open")
}

// A read-only folder is refused before anything is mapped.
if getuid() != 0 {
    let w = World(); defer { w.cleanup() }
    let storage = w.storage(); ExternalGameStorage.shared = storage
    w.put("Game/game.exe", in: w.ssd)
    try! fm.setAttributes([.posixPermissions: 0o555], ofItemAtPath: w.ssd.path)
    let before = snapshot(w.temp)
    let e = throwsError { try storage.connect(w.ssd) }
    expect(e != nil && e!.localizedDescription.contains("couldn't write"), "4: a read-only folder is refused with a clear message")
    expect(snapshot(w.temp) == before, "4: ...with no link and no probe left behind")
    expect(!storage.hasSavedFolder && w.counters.open == 0, "4: ...nothing saved, no scope left open")
}

// ---------------------------------------------------------------- 5. the scanner and the library with the SSD connected

do {
    let w = World(); defer { w.cleanup() }
    buildLocalDrive(w)
    w.put("Game/game.exe", in: w.ssd)
    w.put("Pack/One/one.exe", in: w.ssd); w.put("Pack/Two/two.exe", in: w.ssd)
    w.put("root.exe", in: w.ssd)
    try! fm.createSymbolicLink(at: w.ssd.appendingPathComponent("escape"), withDestinationURL: w.other)
    // The player's own local External folder has to go first: the SSD needs the name.
    try! fm.removeItem(at: w.drive.appendingPathComponent("External"))
    let storage = w.storage(); ExternalGameStorage.shared = storage
    let localBefore = scanWords(GameLibrary.scan()).filter { !$0.contains("C:\\External") }
    try! storage.connect(w.ssd)
    let words = scanWords(GameLibrary.scan())
    expect(words.contains("Game|C:\\External\\Game\\game.exe"), "5: the SSD's game is listed under its folder name")
    expect(words.contains("One|C:\\External\\Pack\\One\\one.exe") && words.contains("Two|C:\\External\\Pack\\Two\\two.exe"), "5: a container folder is split into its titles")
    expect(words.contains("root|C:\\External\\root.exe"), "5: a program in the SSD folder itself is listed by its name")
    expect(!words.contains(where: { $0.contains("evil") }), "5: a link on the SSD to another folder is not followed")
    expect(words.filter { !$0.contains("C:\\External") } == localBefore, "5: the local games are listed as before")
    expect(words.filter { $0.contains("C:\\External") }.count == 4, "5: and the SSD adds exactly its four")
    let relative = ExternalGameStorage.shared.relativePath(for: w.drive.appendingPathComponent("External/Game/game.exe"), drive: w.driveResolved)
    expect(relative == "External/Game/game.exe", "5: the scanned URL maps back to External/Game/game.exe (what the library saves)")
    // Disconnected (a new run, the SSD gone): the link dangles and nothing of it is listed or accepted.
    try! fm.removeItem(at: w.ssd.deletingLastPathComponent())
    let away = ExternalGameStorage(defaults: w.defaults, beginAccess: { _ in true }, endAccess: { _ in })
    ExternalGameStorage.shared = away
    away.restore()
    expect(!away.isConnected, "5: with the SSD away nothing is connected")
    expect(scanWords(GameLibrary.scan()).filter { $0.contains("C:\\External") }.isEmpty, "5: ...nothing of it is listed")
    expect(away.relativePath(for: w.drive.appendingPathComponent("External/Game/game.exe"), drive: w.driveResolved) == nil,
           "5: ...and a path under C:\\External is refused, not taken for a folder on the phone")
    expect(!away.admits(w.drive.appendingPathComponent("External/Game/game.exe")), "5: ...also by admits()")
}

// ---------------------------------------------------------------- 6. reconnect

do {
    let w = World(); defer { w.cleanup() }
    let first = w.storage(); ExternalGameStorage.shared = first
    expect(first.reconnect() == .noSavedFolder, "6: nothing saved: nothing to reconnect")
    expect(!fm.fileExists(atPath: w.link.path) && w.counters.opens == 0, "6: ...and nothing happened")
    w.put("Game/game.exe", in: w.ssd)
    try! first.connect(w.ssd)
    let exePath = "External/Game/game.exe"
    let url = w.ssd.appendingPathComponent("Game/game.exe")
    let d = w.driveResolved

    // A new run (the phone restarted): the saved bookmark opens without the picker.
    let run2 = w.storage()
    let ssdBefore = snapshot(w.ssd)
    expect(run2.reconnect() == .connected, "6: the saved bookmark reconnects without the picker")
    expect(run2.relativePath(for: url, drive: d) == exePath, "6: ...and the games are there")
    expect(snapshot(w.ssd) == ssdBefore, "6: ...the probe leaves no trace")
    expect(LogStore.shared.lines.contains(where: { $0.hasPrefix("[ext-storage] saved folder opened again without the picker") }), "6: ...and the log says so")

    // restore() never writes, even where writing is impossible now.
    if getuid() != 0 {
        try! fm.setAttributes([.posixPermissions: 0o555], ofItemAtPath: w.ssd.path)
        let run3 = w.storage()
        let beforeRO = snapshot(w.ssd)
        run3.restore()
        expect(run3.isConnected && run3.relativePath(for: url, drive: d) == exePath, "6: restore() reconnects a folder that is read-only now (it never writes)")
        expect(snapshot(w.ssd) == beforeRO, "6: ...and wrote nothing")
        let refused = w.storage().reconnect()
        if case .refused(let text) = refused { expect(text.contains("couldn't write"), "6: a by-hand reconnect to a read-only folder says why") }
        else { expect(false, "6: a by-hand reconnect to a read-only folder is refused, got \(refused)") }
        try! fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: w.ssd.path)
    }

    // The folder moved (a stale bookmark): resolved, relinked, saved again.
    let moved = w.temp.appendingPathComponent("SSD/Renamed")
    try! fm.moveItem(at: w.ssd, to: moved)
    let run4 = w.storage()
    let outcome = run4.reconnect()
    expect(outcome == .connected, "6: a folder that moved is found again by its bookmark, got \(outcome)")
    expect(run4.relativePath(for: moved.appendingPathComponent("Game/game.exe"), drive: d) == exePath, "6: ...and C:\\External follows it")
    expect((try? fm.destinationOfSymbolicLink(atPath: w.link.path)) == moved.resolvingSymlinksInPath().path, "6: ...the link was replaced")
    expect(run4.savedFolderName == "Renamed", "6: ...and the saved name follows")
    expect((try? Data(contentsOf: moved.appendingPathComponent("Game/game.exe"))) == Data("MZ".utf8), "6: ...the games are intact")

    // The folder is gone (the SSD is away and iOS cannot resolve the bookmark): the picker is asked for, nothing else changes.
    try! fm.removeItem(at: moved)
    let run5 = w.storage()
    let linkBefore = try? fm.destinationOfSymbolicLink(atPath: w.link.path)
    let openBefore = w.counters.open
    let lost = run5.reconnect()
    if case .chooseAgain(let text) = lost { expect(text.contains("choose its games folder again"), "6: an unresolvable bookmark says to choose the folder again") }
    else { expect(false, "6: an unresolvable bookmark asks for the picker, got \(lost)") }
    expect(run5.hasSavedFolder && !run5.isConnected, "6: ...the bookmark stays saved, nothing is connected")
    expect((try? fm.destinationOfSymbolicLink(atPath: w.link.path)) == linkBefore, "6: ...and the link is as it was")
    expect(run5.message.contains("BCD can't reopen the SSD folder"), "6: ...and the message is set")
    expect(w.counters.open == openBefore, "6: ...no scope leaks")
    run5.restore()
    expect(!run5.isConnected, "6: restore() with the folder away connects nothing")
}

// A bookmark that is not a bookmark.
do {
    let w = World(); defer { w.cleanup() }
    let storage = w.storage(); ExternalGameStorage.shared = storage
    w.defaults.set(Data([1, 2, 3]), forKey: "bcd.external-games.bookmark")
    w.defaults.set(w.ssd.path, forKey: "bcd.external-games.bookmark.path")
    w.put("Game/game.exe", in: w.ssd)
    let before = snapshot(w.temp)
    if case .chooseAgain = storage.reconnect() { expect(true, "7") } else { expect(false, "7: a damaged bookmark asks for the picker") }
    expect(snapshot(w.temp) == before && w.counters.opens == 0, "7: ...and changes nothing")
    storage.restore()
    expect(!storage.isConnected && snapshot(w.temp) == before, "7: restore() with a damaged bookmark is quiet")
}

// The link is only replaced when it is BCD's own, also when reconnecting.
do {
    let w = World(); defer { w.cleanup() }
    let first = w.storage(); ExternalGameStorage.shared = first
    w.put("Game/game.exe", in: w.ssd)
    try! first.connect(w.ssd)
    try! fm.removeItem(at: w.link)
    try! fm.createSymbolicLink(at: w.link, withDestinationURL: w.other)      // the player replaced it with a link of their own
    let run = w.storage()
    let outcome = run.reconnect()
    if case .refused = outcome { expect(true, "8") } else { expect(false, "8: a link that is not BCD's refuses the reconnect, got \(outcome)") }
    expect((try? fm.destinationOfSymbolicLink(atPath: w.link.path)) == w.other.path, "8: ...and stays as the player made it")
    expect(w.counters.open == 1, "8: ...with no scope left open by the refused run")
}

print("External storage: \(checks) checks, \(failures) failed")
exit(failures == 0 ? 0 : 1)
'''

swiftc = shutil.which("xcrun") and shutil.which("swiftc")
if sys.platform != "darwin" or not swiftc:
    check("macOS with swift is here for the Foundation part", not args.require_swift)
    print("SKIP: the Foundation part needs macOS Foundation and Swift (bookmarks, links, real folders).")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-external-") as directory:
        d = Path(directory)
        (d / "main.swift").write_text(stubs + scanner + "\n" + legacy + "\n" + stored + "\n" + tests, encoding="utf-8")
        compiled = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(d / "run"), str(d / "main.swift")],
                                  capture_output=True, text=True)
        check("the production class and the scanner compile on the host", compiled.returncode == 0)
        if compiled.returncode != 0:
            print(compiled.stderr[-3000:])
        else:
            ran = subprocess.run([str(d / "run")], capture_output=True, text=True)
            print(ran.stdout.rstrip())
            if ran.stderr.strip():
                print(ran.stderr.rstrip()[-2000:])
            check("the Foundation checks pass on real folders and bookmarks", ran.returncode == 0)

if failed:
    print(f"\n{len(failed)} check(s) failed")
    sys.exit(1)
print("External storage: all checks passed")
