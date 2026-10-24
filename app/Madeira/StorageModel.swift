//
//  StorageModel.swift
//  Madeira
//
//  madeira-bcd: Settings > Storage -- what the runtime keeps on the phone and a
//  way to take back the space. Frame captures (the overlay's CAP), session logs
//  and the shader caches all grow without the user ever seeing them; a phone
//  that fills up kills the game mid-session in ways that look like our bugs.
//

import SwiftUI

/// A user-authorized SSD folder, exposed to Wine through a link in the local C:.
/// The bookmark's scope stays open for native Wine/FEX file I/O, not just the picker.
///
/// The folder bookmark, the owned C:\External link, the containment rules and the write probe
/// are Pardoxum's (thebcd69/bcd-games#12, "Support games stored on USB-C external SSDs").
/// BCD's changes to them (2026-10-24):
///  - Opt-in by use. Until a folder has been chosen in Settings > Storage (`hasSavedFolder`)
///    nothing here runs: no link, no scope, no log line, and the one call at launch
///    (`restore()`) is a single UserDefaults read. Every hook in the library code keeps its
///    original test first and asks `admits` / `relativePath` only about what that test refuses;
///    `admits` is false at once while no folder is connected.
///  - `reconnect()` opens the saved bookmark again without the picker. When iOS no longer
///    resolves it (an unplugged or restarted SSD often breaks it) the caller gets
///    `.chooseAgain(message)` and shows the picker.
///  - Launch and Play restore the scope without a write probe (`verifyWrite: false`); the probe
///    runs when a folder is chosen or reconnected by hand.
///  - Two locks: `workLock` makes connecting one-at-a-time (it holds disk I/O on the SSD);
///    `stateLock` guards the few fields other threads read and is never held across I/O.
///  - Nothing here deletes or overwrites a user's file: the only file written is the probe
///    (a new, uniquely named, empty file that is removed at once), the only thing removed is
///    BCD's own link.
final class ExternalGameStorage {
    static let shared = ExternalGameStorage()
    static let errorDomain = "BCD.ExternalStorage"

    /// What `reconnect()` did.
    enum Reconnect: Equatable {
        case connected
        /// No folder was ever chosen: nothing happened.
        case noSavedFolder
        /// The saved bookmark does not resolve, or iOS refused the folder: show the picker (with this message).
        case chooseAgain(String)
        /// A rule refused the folder (not writable, C:\External is in use): the picker would not help.
        case refused(String)
    }

    private let key = "bcd.external-games.bookmark"
    private let workLock = NSRecursiveLock()
    private let stateLock = NSLock()
    private let defaults: UserDefaults
    private let beginAccess: (URL) -> Bool
    private let endAccess: (URL) -> Void
    private var folder: URL?
    private var statusText = "Choose a folder on a connected SSD. Games there appear in C:\\External."

    private var link: URL? { GameLibrary.driveC?.appendingPathComponent("External", isDirectory: true) }
    init(defaults: UserDefaults = .standard,
         beginAccess: @escaping (URL) -> Bool = { $0.startAccessingSecurityScopedResource() },
         endAccess: @escaping (URL) -> Void = { $0.stopAccessingSecurityScopedResource() }) {
        self.defaults = defaults
        self.beginAccess = beginAccess
        self.endAccess = endAccess
    }

    /// What Settings > Storage tells about the connection.
    var message: String {
        stateLock.lock(); defer { stateLock.unlock() }
        return statusText
    }

    private func setMessage(_ text: String) {
        stateLock.lock(); statusText = text; stateLock.unlock()
    }

    private var mountedFolder: URL? {
        stateLock.lock(); defer { stateLock.unlock() }
        return folder
    }

    /// A folder was chosen once (its bookmark is saved). False for everyone who never used the feature.
    var hasSavedFolder: Bool { defaults.data(forKey: key) != nil }

    /// The saved folder's name, for the Reconnect button; nil before a folder was chosen.
    var savedFolderName: String? {
        guard hasSavedFolder, let path = defaults.string(forKey: key + ".path") else { return nil }
        return URL(fileURLWithPath: path).lastPathComponent
    }

    /// The folder's scope is open in this run (it may still have been unplugged since).
    var isConnected: Bool { mountedFolder != nil }

    var freeBytes: Int64? {
        guard let folder = mountedFolder, FileManager.default.fileExists(atPath: folder.path) else { return nil }
        return (try? folder.resourceValues(forKeys: [.volumeAvailableCapacityKey]))?.volumeAvailableCapacity.map(Int64.init)
    }

    /// Launch: the saved folder's scope opens again, with no picker and no write probe. A single
    /// UserDefaults read when no folder was ever chosen. A folder iOS no longer resolves stays saved:
    /// Settings > Storage offers Reconnect.
    func restore() {
        guard defaults.data(forKey: key) != nil else { return }
        _ = attachSaved(verifyWrite: false)
    }

    /// The saved folder, again, without the picker (Settings > Storage > Reconnect, and Play on an
    /// external game while the folder is not connected). Writes the probe file like a new choice.
    @discardableResult
    func reconnect(verifyWrite: Bool = true) -> Reconnect { attachSaved(verifyWrite: verifyWrite) }

    private static let chooseAgainText =
        "BCD can't reopen the SSD folder. iOS often forgets it after the SSD is unplugged or the phone restarts. "
        + "Plug the SSD in and choose its games folder again; the games on it are not touched."

    private func attachSaved(verifyWrite: Bool) -> Reconnect {
        workLock.lock(); defer { workLock.unlock() }
        guard let data = defaults.data(forKey: key) else { return .noSavedFolder }
        let url: URL
        var stale = false
        do {
            url = try URL(resolvingBookmarkData: data, options: [.withoutUI], relativeTo: nil, bookmarkDataIsStale: &stale)
        } catch {
            setMessage(Self.chooseAgainText)
            log("saved folder does not resolve (\(Self.describe(error)))")
            return .chooseAgain(Self.chooseAgainText)
        }
        do {
            try attach(url, verifyWrite: verifyWrite)
            log("saved folder opened again without the picker (stale=\(stale ? 1 : 0), probe=\(verifyWrite ? 1 : 0))")
            return .connected
        } catch {
            let error = error as NSError
            if error.domain == Self.errorDomain, error.code == 1 {
                setMessage(error.localizedDescription)
                log("saved folder refused: \(error.localizedDescription)")
                return .refused(error.localizedDescription)
            }
            setMessage(Self.chooseAgainText)
            log("saved folder not usable (\(Self.describe(error)))")
            return .chooseAgain(Self.chooseAgainText)
        }
    }

    /// The folder the player picked in Files.
    func connect(_ url: URL) throws {
        workLock.lock(); defer { workLock.unlock() }
        try attach(url, verifyWrite: true)
    }

    /// The caller holds `workLock`.
    private func attach(_ url: URL, verifyWrite: Bool) throws {
        let opened = beginAccess(url)
        var retained = false
        defer { if opened && !retained { endAccess(url) } }
        guard opened, let link, let drive = GameLibrary.driveC else {
            throw failure("BCD couldn't access this folder. Choose it again in Files.", access: true)
        }
        let target = url.resolvingSymlinksInPath().standardizedFileURL
        let local = Self.withoutPrivate(drive.resolvingSymlinksInPath().standardizedFileURL.path)
        let chosen = Self.withoutPrivate(target.path)
        guard chosen != local, !chosen.hasPrefix(local + "/"), !local.hasPrefix(chosen + "/") else {
            throw failure("Choose a separate games folder on the SSD, outside BCD's Windows drive.")
        }
        // A copy iOS made in the app's temporary Inbox (the picker's "copy" mode) is not the SSD, and iOS deletes it soon.
        guard !target.pathComponents.contains(where: { $0.hasSuffix("-Inbox") }) else {
            throw failure("iOS gave BCD a temporary copy of the folder, not the folder on the SSD. Choose it again in Files.")
        }
        let isFolder: Bool
        do { isFolder = (try target.resourceValues(forKeys: [.isDirectoryKey])).isDirectory == true }
        catch { throw failure("BCD can't read this folder. Is the SSD still connected?", access: true) }
        guard isFolder else { throw failure("Choose a folder on the SSD, not a file.") }
        // Probe write access before changing the mount. Never overwrite a user's folder: the probe is a new, uniquely
        // named, empty file, written without overwriting and removed at once.
        if verifyWrite {
            let probe = target.appendingPathComponent(".bcd-write-test-" + UUID().uuidString)
            var wrote = false
            do {
                try Data().write(to: probe, options: .withoutOverwriting)
                wrote = true
                try FileManager.default.removeItem(at: probe)
            } catch {
                if wrote { try? FileManager.default.removeItem(at: probe) }
                throw failure("BCD couldn't write to this folder. Choose a folder on a drive that isn't read-only "
                              + "(APFS or exFAT; NTFS is read-only on iPhone).")
            }
        }
        let bookmark = try url.bookmarkData(options: [.minimalBookmark], includingResourceValuesForKeys: nil, relativeTo: nil)
        try FileManager.default.createDirectory(at: drive, withIntermediateDirectories: true)
        let previous = try? FileManager.default.destinationOfSymbolicLink(atPath: link.path)
        if previous == target.path {
            // C:\External already leads to this folder: nothing to change.
        } else if FileManager.default.fileExists(atPath: link.path) || previous != nil {
            // Only BCD's own link is ever replaced (removeItem on a link removes the link, not what it leads to).
            guard let previous, previous == defaults.string(forKey: key + ".path") else {
                throw failure("C:\\External already exists. Rename that folder in Files before connecting the SSD.")
            }
            try FileManager.default.removeItem(at: link)
            do { try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target) }
            catch {
                try? FileManager.default.createSymbolicLink(atPath: link.path, withDestinationPath: previous)
                throw error
            }
        } else {
            try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target)
        }
        stateLock.lock()
        let old = folder
        folder = url
        statusText = "\(url.lastPathComponent) is connected as C:\\External. Keep the SSD connected while playing."
        stateLock.unlock()
        if let old { endAccess(old) }
        retained = true
        defaults.set(bookmark, forKey: key)
        defaults.set(target.path, forKey: key + ".path")
        log("connected: \(Self.volumeWords(target))")
    }

    /// The path relative to drive_c, or nil when it is outside. drive_c is tested exactly as the library always did
    /// (links resolved); only what that refuses may be the connected SSD folder, shown as External/... A path
    /// under C:\External while a folder is saved but not connected is refused: a missing SSD must not
    /// silently become a new folder on the phone.
    func relativePath(for url: URL, drive: URL) -> String? {
        let resolved = url.resolvingSymlinksInPath().path
        if resolved.hasPrefix(drive.path + "/") {
            let relative = String(resolved.dropFirst(drive.path.count + 1))
            if (relative == "External" || relative.hasPrefix("External/")) && hasSavedFolder {
                return externalRelativePath(for: url)
            }
            return relative
        }
        return externalRelativePath(for: url)
    }

    /// Whether `url` lies in the connected SSD folder, links resolved. False at once while no folder is connected, so
    /// a hook written `original test || admits(url)` cannot change anything before the feature is used.
    func admits(_ url: URL) -> Bool {
        guard mountedFolder != nil else { return false }
        return externalRelativePath(for: url) != nil
    }

    /// The children of `dir` when it lies in the connected folder, listed by path, or nil (the caller lists as it always
    /// did). Foundation's URL listing (`contentsOfDirectory(at:)`) fails on a link that is the last component of the
    /// URL (ENOTDIR) and, below a link, returns the resolved path instead of the one asked for (both found by
    /// check-external-storage.py). C:\External is such a link; the path-based call follows it, and the names keep
    /// `dir` as their parent, so they stay under drive_c. False at once unless the folder is connected.
    func linkedChildren(of dir: URL) -> [URL]? {
        guard admits(dir), let names = try? FileManager.default.contentsOfDirectory(atPath: dir.path) else { return nil }
        return names.filter { !$0.hasPrefix(".") }.map { dir.appendingPathComponent($0) }
    }

    /// The connected folder itself (C:\External), not something in it.
    func isMountRoot(_ url: URL) -> Bool {
        guard mountedFolder != nil else { return false }
        return externalRelativePath(for: url) == "External"
    }

    /// Accept only the authorized mount; links that escape either root stay rejected.
    private func externalRelativePath(for url: URL) -> String? {
        guard let folder = mountedFolder, let link else { return nil }
        let external = folder.resolvingSymlinksInPath().standardizedFileURL.path
        guard link.resolvingSymlinksInPath().standardizedFileURL.path == external else { return nil }
        let path = resolvedPathIncludingMissingLeaf(url)
        if path == external { return "External" }
        guard path.hasPrefix(external + "/") else { return nil }
        return "External/" + String(path.dropFirst(external.count + 1))
    }

    /// Foundation can leave the whole path unresolved when its final file is
    /// absent. Resolve the closest existing ancestor before appending the suffix.
    private func resolvedPathIncludingMissingLeaf(_ url: URL) -> String {
        var ancestor = url
        var suffix: [String] = []
        while !FileManager.default.fileExists(atPath: ancestor.path), ancestor.path != "/" {
            suffix.append(ancestor.lastPathComponent)
            ancestor.deleteLastPathComponent()
        }
        return suffix.reversed().reduce(ancestor.resolvingSymlinksInPath()) {
            $0.appendingPathComponent($1)
        }.standardizedFileURL.path
    }

    /// iOS spells the same folder /var/... and /private/var/...; compare them alike.
    private static func withoutPrivate(_ path: String) -> String {
        path.hasPrefix("/private/") ? String(path.dropFirst(8)) : path
    }

    private func failure(_ text: String, access: Bool = false) -> NSError {
        NSError(domain: Self.errorDomain, code: access ? 2 : 1, userInfo: [NSLocalizedDescriptionKey: text])
    }

    private static func describe(_ error: Error) -> String {
        let e = error as NSError
        return "\(e.domain) \(e.code)"
    }

    /// For the log: whether the folder is on an external, local volume and in which format (no names, no paths).
    private static func volumeWords(_ url: URL) -> String {
        let keys: Set<URLResourceKey> = [.volumeIsInternalKey, .volumeIsLocalKey, .volumeLocalizedFormatDescriptionKey]
        guard let values = try? url.resourceValues(forKeys: keys) else { return "volume unknown" }
        func flag(_ value: Bool?) -> String { value.map { $0 ? "1" : "0" } ?? "?" }
        return "volume internal=\(flag(values.volumeIsInternal)) local=\(flag(values.volumeIsLocal)) format=\(values.volumeLocalizedFormatDescription ?? "?")"
    }

    private func log(_ text: String) { LogStore.shared.log("[ext-storage] " + text) }
}

struct StorageItem: Identifiable {
    let id: String
    let title: String
    let detail: String
    let deletable: Bool
    let urls: () -> [URL]
}

final class StorageModel: ObservableObject {
    @Published private(set) var sizes: [String: Int64] = [:]
    @Published private(set) var busy = false

    static var documents: URL? { FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first }

    /// C:\users\<name>\AppData\Local\BCD\ShaderCache for every profile.
    static func d3d12ShaderCaches() -> [URL] {
        guard let users = GameLibrary.driveC?.appendingPathComponent("users"),
              let names = try? FileManager.default.contentsOfDirectory(atPath: users.path) else { return [] }
        return names.map {
            users.appendingPathComponent($0).appendingPathComponent("AppData/Local/BCD/ShaderCache", isDirectory: true)
        }.filter { FileManager.default.fileExists(atPath: $0.path) }
    }

    static let items: [StorageItem] = [
        StorageItem(id: "capture", title: "Frame captures",
                    detail: "Written by the overlay's CAP button", deletable: true,
                    urls: { [StorageModel.documents?.appendingPathComponent("capture", isDirectory: true)].compactMap { $0 } }),
        StorageItem(id: "logs", title: "Session logs",
                    detail: "Earlier games' logs (the newest 40 are kept)", deletable: true,
                    urls: { [StorageModel.documents?.appendingPathComponent("logs", isDirectory: true),
                             StorageModel.documents?.appendingPathComponent("bcd-log.prev.txt")].compactMap { $0 } }),
        StorageItem(id: "sc12", title: "D3D12 shader cache",
                    detail: "Converted shaders; deleting it makes the next start convert them again", deletable: true,
                    urls: { StorageModel.d3d12ShaderCaches() }),
        StorageItem(id: "sc11", title: "D3D11 shader cache",
                    detail: "DXMT's compiled shaders", deletable: true,
                    urls: { [StorageModel.documents?.appendingPathComponent("shadercache", isDirectory: true)].compactMap { $0 } }),
        StorageItem(id: "drive", title: "Windows drive (C:)",
                    detail: "Games, saves and the Windows files", deletable: false,
                    urls: { [GameLibrary.driveC].compactMap { $0 } }),
    ]

    func refresh() {
        guard !busy else { return }
        busy = true
        DispatchQueue.global(qos: .utility).async {
            var out: [String: Int64] = [:]
            for item in StorageModel.items {
                out[item.id] = item.urls().reduce(Int64(0)) { $0 + StorageModel.size(of: $1) }
            }
            DispatchQueue.main.async {
                self.sizes = out
                self.busy = false
            }
        }
    }

    func clear(_ item: StorageItem) {
        guard item.deletable else { return }
        DispatchQueue.global(qos: .utility).async {
            for u in item.urls() { try? FileManager.default.removeItem(at: u) }
            DispatchQueue.main.async { self.refresh() }
        }
    }

    /// Allocated bytes under `url` (a file or a directory; symlinks are not followed).
    static func size(of url: URL) -> Int64 {
        let fm = FileManager.default
        let keys: [URLResourceKey] = [.isRegularFileKey, .totalFileAllocatedSizeKey, .fileAllocatedSizeKey]
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: url.path, isDirectory: &isDir) else { return 0 }
        if !isDir.boolValue {
            let v = try? url.resourceValues(forKeys: Set(keys))
            return Int64(v?.totalFileAllocatedSize ?? v?.fileAllocatedSize ?? 0)
        }
        var total: Int64 = 0
        if let e = fm.enumerator(at: url, includingPropertiesForKeys: keys, options: [], errorHandler: { _, _ in true }) {
            for case let f as URL in e {
                guard let v = try? f.resourceValues(forKeys: Set(keys)), v.isRegularFile == true else { continue }
                total += Int64(v.totalFileAllocatedSize ?? v.fileAllocatedSize ?? 0)
            }
        }
        return total
    }
}
