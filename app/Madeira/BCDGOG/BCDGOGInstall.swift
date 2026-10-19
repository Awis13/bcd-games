// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// An installed GOG game: its record, its folder under C:\GOG Games, and the program its build
// names in goggame-<id>.info (gog.md §2.4.1), which becomes an ordinary library entry, so Play is
// the library's normal start of a game added from drive_c. Foundation only.

/// What the game's build says to start: the primary file task, else the first one of category
/// "game". Paths are relative to the game's folder ('/' between folders, as on the disk).
struct BCDGOGPlayTask: Codable, Equatable {
    var path: String
    var arguments: String
    var workingDirectory: String
    var category: String
}

/// One installed game. Paths are drive-relative (a root of a drive letter and a folder), so a
/// later D:\GOG Games needs no migration.
struct BCDGOGInstallRecord: Codable, Equatable {
    var version = 1
    var productID: String
    var title: String
    var buildID: String
    var versionName: String
    var language: String
    var root: BCDInstallRoot
    var folder: String
    var bytes: Int64
    var depots: [String]
    /// Redistributables the build names (VC++, OpenAL, ...): shown, not installed yet (M2).
    var dependencies: [String]
    var supportFiles: Int
    var play: BCDGOGPlayTask?
    /// The library entry Play starts.
    var entryID: UUID?
    var installed: Date

    var windowsFolder: String { root.windowsPath(folder) }
    var hostFolder: URL? { root.url?.appendingPathComponent(folder, isDirectory: true) }
}

enum BCDGOGInstall {
    /// The game's play task from its goggame-<id>.info.
    static func playTask(fromInfo data: Data) throws -> BCDGOGPlayTask {
        guard data.count <= 4 << 20, let info = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw BCDGOGError.badReply("info")
        }
        let tasks = (info["playTasks"] as? [[String: Any]] ?? []).filter { ($0["type"] as? String) == "FileTask" }
        guard let task = tasks.first(where: { ($0["isPrimary"] as? Bool) == true })
                ?? tasks.first(where: { ($0["category"] as? String)?.lowercased() == "game" }),
              let raw = task["path"] as? String else { throw BCDGOGError.noPlayTask }
        func relative(_ text: String) throws -> String {
            var text = text.replacingOccurrences(of: "\\", with: "/")
            while text.hasPrefix("./") { text.removeFirst(2) }
            while text.hasSuffix("/") { text.removeLast() }
            return text.isEmpty ? "" : try BCDContentPaths.components(text).joined(separator: "/")
        }
        guard let path = try? relative(raw), !path.isEmpty else { throw BCDGOGError.noPlayTask }
        let arguments = (task["arguments"] as? String ?? "").replacingOccurrences(of: "\0", with: "")
        let working = (try? relative(task["workingDir"] as? String ?? "")) ?? ""
        return BCDGOGPlayTask(path: path, arguments: String(arguments.prefix(2000)), workingDirectory: working,
                              category: (task["category"] as? String ?? "game").lowercased())
    }

    /// The build's own info file in the game's folder, whatever its letter case.
    static func infoFile(in folder: URL, productID: String) -> URL? {
        let wanted = "goggame-\(productID).info"
        let names = (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []
        return names.first { $0.lowercased() == wanted }.map { folder.appendingPathComponent($0) }
    }

    /// A Windows-cased relative path as the device spells it: the device's files are case-sensitive
    /// and the build's info file may spell a folder differently from its manifest. nil when it is
    /// not there.
    static func onDisk(_ relative: String, in folder: URL, directory: Bool) -> String? {
        guard !relative.isEmpty else { return directory ? "" : nil }
        var spelled: [String] = []
        var at = folder
        let pieces = relative.split(separator: "/").map(String.init)
        for (i, piece) in pieces.enumerated() {
            let names = (try? FileManager.default.contentsOfDirectory(atPath: at.path)) ?? []
            guard let name = names.first(where: { $0 == piece }) ?? names.first(where: { $0.lowercased() == piece.lowercased() }) else {
                return nil
            }
            spelled.append(name)
            at = at.appendingPathComponent(name)
            var isDirectory: ObjCBool = false
            guard FileManager.default.fileExists(atPath: at.path, isDirectory: &isDirectory),
                  isDirectory.boolValue == (i < pieces.count - 1 || directory) else { return nil }
        }
        return spelled.joined(separator: "/")
    }

    /// The install folder's name: the build's own, then "<name> (GOG)", "(GOG 2)", ... when a folder
    /// of that name is already there.
    static func folderName(preferred: String, in root: URL) -> String {
        let base = BCDContentPaths.folderName(preferred) ?? "GOG game"
        var candidate = base
        var n = 1
        while FileManager.default.fileExists(atPath: root.appendingPathComponent(candidate).path) {
            candidate = n == 1 ? base + " (GOG)" : base + " (GOG \(n))"
            n += 1
        }
        return candidate
    }

    /// The library entry's launch fields for a play task: the program relative to drive_c, its
    /// arguments as GOG wrote them, the working folder as a Windows path (the game's folder when
    /// the task names none), and the Wine desktop for a launcher (it may exit once the game runs).
    static func launch(_ record: BCDGOGInstallRecord, play: BCDGOGPlayTask, program: String, working: String)
        -> (relativePath: String, arguments: String, workingDirectory: String, launchMode: String?) {
        let relative = [record.root.folder, record.folder, program].joined(separator: "/")
        let folder = working.isEmpty ? record.windowsFolder : record.root.windowsPath(record.folder, working)
        return (relative, play.arguments, folder, play.category == "launcher" ? "desktop" : nil)
    }

    /// The downloaded files go into place (one rename into the root, under a free folder name),
    /// and the build's play task is read and matched to the files on the disk. Off the main thread.
    static func finish(_ plan: BCDContentPlan, info: BCDGOGPlanInfo, stage: URL, root: BCDInstallRoot,
                       now: Date = Date()) throws -> BCDGOGInstallRecord {
        guard let rootURL = root.url else { throw BCDContentError.driveUnavailable(root.drive) }
        let folder = folderName(preferred: plan.folder, in: rootURL)
        let gameFolder = rootURL.appendingPathComponent(folder, isDirectory: true)
        try BCDContentEngine.commit(stage: stage, to: gameFolder)
        var record = BCDGOGInstallRecord(productID: plan.key.id, title: info.title, buildID: info.buildID, versionName: info.version,
                                         language: info.language, root: root, folder: folder, bytes: plan.totalBytes,
                                         depots: info.depots, dependencies: info.dependencies, supportFiles: info.supportFiles,
                                         play: nil, entryID: nil, installed: now)
        if let file = infoFile(in: gameFolder, productID: plan.key.id), let data = try? Data(contentsOf: file),
           var play = try? playTask(fromInfo: data), let program = onDisk(play.path, in: gameFolder, directory: false) {
            play.path = program
            play.workingDirectory = onDisk(play.workingDirectory, in: gameFolder, directory: true) ?? ""
            record.play = play
        }
        BCDContentLog.write("[gog-install] installed product=\(plan.key.id) files=\(plan.files.count) bytes=\(plan.totalBytes) " +
                            "play=\(record.play == nil ? 0 : 1) redist=\(info.dependencies.count)")
        return record
    }

    // MARK: Records

    static func recordsFolder(_ base: URL) -> URL { base.appendingPathComponent("installs", isDirectory: true) }

    static func loadRecords(_ base: URL) -> [String: BCDGOGInstallRecord] {
        let folder = recordsFolder(base)
        var out: [String: BCDGOGInstallRecord] = [:]
        for name in (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? [] where name.hasSuffix(".json") {
            guard let data = try? Data(contentsOf: folder.appendingPathComponent(name)),
                  let record = try? JSONDecoder().decode(BCDGOGInstallRecord.self, from: data),
                  name == record.productID + ".json", BCDGOGCatalog.validID(record.productID),
                  BCDContentPaths.folderName(record.folder) == record.folder else { continue }
            out[record.productID] = record
        }
        return out
    }

    static func save(_ record: BCDGOGInstallRecord, base: URL) throws {
        let folder = recordsFolder(base)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(record).write(to: folder.appendingPathComponent(record.productID + ".json"), options: .atomic)
    }

    static func delete(_ productID: String, base: URL) {
        try? FileManager.default.removeItem(at: recordsFolder(base).appendingPathComponent(productID + ".json"))
    }
}
