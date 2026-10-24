// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import CryptoKit
import Foundation

// Wine Gecko, the HTML engine Wine's mshtml loads for programs that show a web page: an installer with an embedded
// page (the EA app's) asks for it, and Wine's own dialog then downloads it inside the emulator at a crawl. Settings ›
// "HTML engine for installers" downloads it with BCD's own downloader instead, once, checks both files against the
// SHA-256 Wine pins for them (dlls/appwiz.cpl/addons.c: GECKO_VERSION, GECKO_SHA per architecture), and keeps them
// outside the Wine prefix. At every session start (ContentView) BCDGeckoFiles.exportEnvironment points the guest's
// cache folder at them: appwiz.cpl looks for `$WINE_HOST_XDG_CACHE_HOME/wine/<file>` (the guest sees the host's
// XDG_CACHE_HOME under that name, build/ntdll-unix/env_ios.c), checks its SHA-256 and installs it with msiexec; only
// when no file is there does it show its download dialog, which is what happens today.
//
// Only the presence of the two files matters: not downloaded, nothing is exported and every session is as before. A
// session that never needs mshtml never reads the folder. The launchers' flow (BCDLaunchers.swift) downloads Gecko
// before an installer that needs it.
//
// Foundation and CryptoKit only: tests/host/check-bcd-launchers.py compiles this file with BCDLauncherDownload.swift
// and serves the answers from a URLProtocol. No UI and no engine type here (BCDGeckoSection.swift draws the page).
// Log tag: [gecko].

// MARK: - What Wine pins

enum BCDGeckoPin {
    /// addons.c's GECKO_VERSION (tests/host/check-bcd-launchers.py compares the three constants with it).
    static let version = "2.47.4"

    struct Package: Equatable {
        /// addons.c's GECKO_ARCH: "x86" for 32-bit programs, "x86_64" for 64-bit ones.
        let arch: String
        let file: String
        let url: URL
        /// addons.c's GECKO_SHA for the architecture.
        let sha256: String
        let bytes: Int64
    }

    static let x86 = Package(
        arch: "x86", file: "wine-gecko-\(version)-x86.msi",
        url: URL(string: "https://dl.winehq.org/wine/wine-gecko/\(version)/wine-gecko-\(version)-x86.msi")!,
        sha256: "26cecc47706b091908f7f814bddb074c61beb8063318e9efc5a7f789857793d6", bytes: 55_187_968)
    static let x86_64 = Package(
        arch: "x86_64", file: "wine-gecko-\(version)-x86_64.msi",
        url: URL(string: "https://dl.winehq.org/wine/wine-gecko/\(version)/wine-gecko-\(version)-x86_64.msi")!,
        sha256: "e590b7d988a32d6aa4cf1d8aa3aa3d33766fdd4cf4c89c2dcc2095ecb28d066f", bytes: 53_898_752)
    static let packages = [x86, x86_64]

    /// WineHQ's download host, the only one a download may touch (the first request and every redirect).
    static let hosts: Set<String> = ["dl.winehq.org"]
    static var totalBytes: Int64 { packages.reduce(0) { $0 + $1.bytes } }
    /// Free room needed: the two files and a partial one.
    static let spaceNeeded: Int64 = 300_000_000
}

// MARK: - Files on the device

enum BCDGeckoFiles {
    /// Library/Application Support/WineGecko, kept out of backups. `cache` in it is what XDG_CACHE_HOME names.
    static var defaultRoot: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("WineGecko", isDirectory: true)
    }
    /// The folder the guest takes for its cache; `wine` inside it holds the files.
    static func cacheFolder(_ root: URL) -> URL { root.appendingPathComponent("cache", isDirectory: true) }
    static func wineFolder(_ root: URL) -> URL { cacheFolder(root).appendingPathComponent("wine", isDirectory: true) }
    static func file(_ root: URL, _ package: BCDGeckoPin.Package) -> URL { wineFolder(root).appendingPathComponent(package.file) }

    /// Every file is there at exactly its pinned size (the SHA-256 was checked when it was downloaded; Wine checks it again).
    static func ready(_ root: URL, packages: [BCDGeckoPin.Package] = BCDGeckoPin.packages) -> Bool {
        packages.allSatisfy { package in
            let size = (try? FileManager.default.attributesOfItem(atPath: file(root, package).path)[.size] as? NSNumber)?.int64Value
            return size == package.bytes
        }
    }

    static func sha256(of file: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1 << 20), !chunk.isEmpty { hasher.update(data: chunk) }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    /// For every session: when both files are downloaded, the cache folder the guest looks in is theirs
    /// (XDG_CACHE_HOME; the guest reads it as WINE_HOST_XDG_CACHE_HOME). Returns the log line, or nil when nothing was changed.
    /// Not downloaded: the environment is left alone.
    @discardableResult
    static func exportEnvironment(root: URL = defaultRoot, packages: [BCDGeckoPin.Package] = BCDGeckoPin.packages,
                                  set: (String, String) -> Void = { setenv($0, $1, 1) }) -> String? {
        guard ready(root, packages: packages) else { return nil }
        set("XDG_CACHE_HOME", cacheFolder(root).path)
        return "[gecko] downloaded Wine Gecko \(BCDGeckoPin.version) is this session's cache (XDG_CACHE_HOME)"
    }
}

// MARK: - The model

enum BCDGeckoState: Equatable {
    case missing
    /// Bytes so far and in all (both files), and the architecture being fetched.
    case downloading(received: Int64, total: Int64, arch: String)
    case installed
}

/// What the Settings page needs: the state and four actions.
@MainActor protocol BCDGeckoBackend: ObservableObject {
    var state: BCDGeckoState { get }
    var lastError: String? { get }
    /// Downloads what is missing (also after a failure).
    func install()
    func cancel()
    /// Deletes the downloaded files; sessions are then as they were before.
    func remove()
    func refresh()
}

@MainActor final class BCDGeckoModel: ObservableObject, BCDGeckoBackend {
    static let shared = BCDGeckoModel(root: BCDGeckoFiles.defaultRoot)

    @Published private(set) var state: BCDGeckoState = .missing
    @Published private(set) var lastError: String?

    let root: URL
    let packages: [BCDGeckoPin.Package]
    /// The session settings of the download; the host check serves WineHQ's answers from a URLProtocol through them.
    var configuration: URLSessionConfiguration = BCDGeckoModel.downloadConfiguration()
    /// Where the log lines go ([gecko]: sizes, hosts, reasons; never an address with a path).
    var log: (String) -> Void = { _ in }
    private var current: Task<Void, Error>?
    private var control = BCDLauncherDownloadControl()

    init(root: URL, packages: [BCDGeckoPin.Package] = BCDGeckoPin.packages) {
        self.root = root
        self.packages = packages
        refresh()
    }

    /// No cookies or cache kept; a request silent for 30 seconds fails, and the two files have an hour.
    static func downloadConfiguration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 60 * 60
        configuration.waitsForConnectivity = false
        return configuration
    }

    var isReady: Bool { BCDGeckoFiles.ready(root, packages: packages) }
    var busy: Bool { current != nil }

    func refresh() {
        guard !busy else { return }
        let next: BCDGeckoState = isReady ? .installed : .missing
        if state != next { state = next }
    }

    /// Settings' Download button: the same download, with its outcome in `state` and `lastError`.
    func install() {
        guard !busy else { return }
        lastError = nil
        Task { [weak self] in
            do { try await self?.ensure() }
            catch { self?.failed(error) }
        }
    }

    func cancel() {
        control.cancel()
        current?.cancel()
    }

    func remove() {
        guard !busy else { return }
        try? FileManager.default.removeItem(at: BCDGeckoFiles.cacheFolder(root))
        lastError = nil
        log("[gecko] removed")
        refresh()
    }

    /// Makes both files present: downloads what is missing, checks each against its pinned size and SHA-256, and keeps it
    /// only then. Already there: returns at once. While Settings' download runs, a second caller joins it.
    /// `progress` gets the bytes of both files so far and in all.
    func ensure(progress: @escaping @Sendable (Int64, Int64) -> Void = { _, _ in }) async throws {
        if let running = current { try await running.value; return }
        if isReady { refresh(); return }
        let task = Task { try await self.run(progress) }
        current = task
        defer { current = nil; refresh() }
        try await task.value
    }

    private func run(_ progress: @escaping @Sendable (Int64, Int64) -> Void) async throws {
        let fm = FileManager.default
        let folder = BCDGeckoFiles.wineFolder(root)
        do { try fm.createDirectory(at: folder, withIntermediateDirectories: true) }
        catch { throw BCDLauncherError.saving(error.localizedDescription) }
        // Kept out of backups, like the other downloaded runtimes.
        var excluded = URLResourceValues()
        excluded.isExcludedFromBackup = true
        var top = root
        try? top.setResourceValues(excluded)

        if let free = (try? URL(fileURLWithPath: NSHomeDirectory()).resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?
            .volumeAvailableCapacityForImportantUsage, free < BCDGeckoPin.spaceNeeded {
            log("[gecko] refused: free=\(free / (1 << 20))MB")
            throw BCDLauncherError.saving("not enough free space (about \(BCDGeckoPin.spaceNeeded / 1_000_000) MB are needed)")
        }
        let total = packages.reduce(0) { $0 + $1.bytes }
        var done: Int64 = 0
        for package in packages {
            if BCDGeckoFiles.ready(root, packages: [package]) { done += package.bytes; continue }
            let base = done
            publish(.downloading(received: base, total: total, arch: package.arch))
            log("[gecko] download \(package.arch) from \(BCDLauncherNet.origin(package.url))")
            let box = ProgressBox()
            try await fetch(package, into: folder) { received, _ in
                progress(base + received, total)
                Task { @MainActor [weak self] in
                    guard box.accept(received) else { return }
                    self?.publish(.downloading(received: base + received, total: total, arch: package.arch))
                }
            }
            done += package.bytes
        }
        progress(total, total)
        log("[gecko] ready version=\(BCDGeckoPin.version) bytes=\(total)")
    }

    private func publish(_ next: BCDGeckoState) { if state != next { state = next } }

    private func failed(_ error: Error) {
        let cancelled = (error as? BCDLauncherError) == .cancelled || error is CancellationError
        let reason = (error as? BCDLauncherError)?.logReason ?? (cancelled ? "cancelled" : "other")
        log("[gecko] download failed reason=\(reason)")
        lastError = cancelled ? "Cancelled." : (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    }

    private func fetch(_ package: BCDGeckoPin.Package, into folder: URL, progress: @escaping @Sendable (Int64, Int64) -> Void) async throws {
        let fm = FileManager.default
        let final = folder.appendingPathComponent(package.file)
        let part = folder.appendingPathComponent(package.file + ".part")
        try? fm.removeItem(at: part)
        guard BCDLauncherNet.isAllowed(package.url, hosts: BCDGeckoPin.hosts) else {
            throw BCDLauncherError.redirected(BCDLauncherNet.origin(package.url))
        }
        let control = BCDLauncherDownloadControl()
        self.control = control
        let configuration = self.configuration
        let reached: URL = try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<URL, Error>) in
                let downloader = BCDLauncherDownloader(url: package.url, hosts: BCDGeckoPin.hosts, maxBytes: package.bytes + (1 << 20),
                                                       company: "WineHQ", destination: part, configuration: configuration,
                                                       progress: progress) { result in
                    control.clear()
                    continuation.resume(with: result)
                }
                control.set(downloader)
                downloader.start()
            }
        } onCancel: {
            control.cancel()
        }
        log("[gecko] \(package.arch) answered from \(BCDLauncherNet.origin(reached))")
        do {
            let size = (try fm.attributesOfItem(atPath: part.path)[.size] as? NSNumber)?.int64Value ?? 0
            guard size == package.bytes else { throw BCDLauncherError.checksum(what: "HTML engine") }
            guard try BCDGeckoFiles.sha256(of: part) == package.sha256 else { throw BCDLauncherError.checksum(what: "HTML engine") }
            try? fm.removeItem(at: final)
            try fm.moveItem(at: part, to: final)
        } catch let error as BCDLauncherError {
            try? fm.removeItem(at: part)
            throw error
        } catch {
            try? fm.removeItem(at: part)
            throw BCDLauncherError.saving(error.localizedDescription)
        }
    }
}

/// Lets the page follow a download without a state change for every 64 KB: at most about 200 a file.
private final class ProgressBox: @unchecked Sendable {
    private let lock = NSLock()
    private var last: Int64 = -1
    func accept(_ received: Int64) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard last < 0 || received - last >= 256 << 10 else { return false }
        last = received
        return true
    }
}
