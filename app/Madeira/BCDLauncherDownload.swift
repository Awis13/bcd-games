// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// The download of a launcher's installer (BCDLaunchers.swift): the company's own address over HTTPS,
// every redirect checked against the company's own download host, the size watched, and the file looked at
// before it is kept (a Windows program with a PE header, not an error page). Foundation only: the host
// check (tests/host/check-bcd-launchers.py) compiles this file and serves the answers from a URLProtocol.
// Nothing is run here; the file is saved in drive_c and the engine starts it as a session's first program.

// MARK: - Errors, in the words the card shows

enum BCDLauncherError: LocalizedError, Equatable {
    case network(String)
    case redirected(String)
    case http(Int, company: String)
    case tooLarge
    case notAnInstaller(company: String)
    case notEnoughSpace(title: String, gigabytes: Int)
    case saving(String)
    /// A downloaded component whose size or SHA-256 is not the pinned one (BCDGecko.swift).
    case checksum(what: String)
    case cancelled

    /// A short word for the log: what failed, never the server's words or an address.
    var logReason: String {
        switch self {
        case .network: return "network"
        case .redirected: return "redirected"
        case .http(let code, _): return "http-\(code)"
        case .tooLarge: return "too-large"
        case .notAnInstaller: return "not-an-installer"
        case .notEnoughSpace: return "no-space"
        case .saving: return "saving"
        case .checksum: return "checksum"
        case .cancelled: return "cancelled"
        }
    }

    var errorDescription: String? {
        switch self {
        case .network(let detail):
            return "The download failed: \(detail) Check the connection and try again."
        case .redirected(let address):
            return "The download was sent to \(address), which is not the company's own download server, so nothing was saved."
        case .http(let code, let company):
            return "\(company)'s download server answered \(code). Try again in a little while."
        case .tooLarge:
            return "The download was far larger than the installer should be, so it was stopped."
        case .notAnInstaller(let company):
            return "What came back is not a Windows installer from \(company), so it was not kept. Try again in a little while."
        case .notEnoughSpace(let title, let gigabytes):
            return "Not enough free space: \(title) needs about \(gigabytes) GB free on this device."
        case .saving(let detail):
            return "The file could not be saved: \(detail)"
        case .checksum(let what):
            return "The \(what) download does not match the checksum it is pinned to, so it was not kept. Try again."
        case .cancelled:
            return "Cancelled."
        }
    }
}

// MARK: - Which addresses are the company's

enum BCDLauncherNet {
    /// HTTPS on the default port to one of `hosts`, with no user name or password.
    static func isAllowed(_ url: URL, hosts: Set<String>) -> Bool {
        guard url.scheme?.lowercased() == "https", let host = url.host?.lowercased(), hosts.contains(host) else { return false }
        return (url.port == nil || url.port == 443) && url.user == nil && url.password == nil
    }

    /// scheme://host[:port] of `url`: what a message and the log may say (never the path or query).
    static func origin(_ url: URL) -> String {
        "\(url.scheme ?? "?")://\(url.host ?? "?")" + (url.port.map { ":\($0)" } ?? "")
    }
}

// MARK: - Looking at the file

enum BCDLauncherPE {
    struct Info: Equatable {
        /// 0x14c (x86) or 0x8664 (x64).
        var machine: Int
        var bits: Int { machine == 0x8664 ? 64 : 32 }
        /// A certificate table (Authenticode) is attached.
        var signed: Bool
    }

    /// The header of a Windows x86 or x64 program, with every offset checked; nil for anything else.
    static func inspect(_ data: Data) -> Info? {
        func u16(_ at: Int) -> Int? { at >= 0 && at + 2 <= data.count ? Int(data[data.startIndex + at]) | Int(data[data.startIndex + at + 1]) << 8 : nil }
        func u32(_ at: Int) -> Int? {
            guard at >= 0, at + 4 <= data.count else { return nil }
            return (0..<4).reduce(0) { $0 | Int(data[data.startIndex + at + $1]) << ($1 * 8) }
        }
        guard u16(0) == 0x5A4D, let pe = u32(0x3C), pe >= 0x40, pe < 16 << 20, u32(pe) == 0x0000_4550,
              let machine = u16(pe + 4), machine == 0x14C || machine == 0x8664,
              let optionalSize = u16(pe + 20), let magic = u16(pe + 24) else { return nil }
        let directories: Int
        switch magic {
        case 0x20B: directories = pe + 24 + 112
        case 0x10B: directories = pe + 24 + 96
        default: return nil
        }
        // Data directory 4 (the certificate table) holds a file offset and a size.
        var signed = false
        if let count = u32(directories - 4), count > 4, directories + 40 <= pe + 24 + optionalSize,
           let offset = u32(directories + 32), let size = u32(directories + 36), size >= 8, offset > 0, offset + size <= data.count {
            signed = true
        }
        return Info(machine: machine, signed: signed)
    }
}

// MARK: - The download

/// One download of a launcher's installer: redirects only to `hosts`, progress, a size ceiling, and the finished
/// file moved out of URLSession's temporary folder to `destination`. Its delegate calls arrive one at a time on
/// the session's own queue.
final class BCDLauncherDownloader: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    private let hosts: Set<String>
    private let maxBytes: Int64
    private let company: String
    private let destination: URL
    private let progress: (Int64, Int64) -> Void
    private let finished: (Result<URL, Error>) -> Void
    private var session: URLSession?
    private var task: URLSessionDownloadTask?
    private var outcome: Result<URL, Error>?
    private var refusedOrigin: String?
    private var tooLarge = false
    private var reported: Int64 = -1

    /// No cookies or cache kept; a request that is silent for 30 seconds fails, and the whole download has a quarter of an hour.
    static func configuration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 15 * 60
        configuration.waitsForConnectivity = false
        return configuration
    }

    init(url: URL, hosts: Set<String>, maxBytes: Int64, company: String, destination: URL,
         configuration: URLSessionConfiguration = BCDLauncherDownloader.configuration(),
         progress: @escaping (Int64, Int64) -> Void, finished: @escaping (Result<URL, Error>) -> Void) {
        self.hosts = hosts
        self.maxBytes = maxBytes
        self.company = company
        self.destination = destination
        self.progress = progress
        self.finished = finished
        super.init()
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        self.session = session
        task = session.downloadTask(with: url)
    }

    func start() { task?.resume() }
    func cancel() { task?.cancel() }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        if let url = request.url, BCDLauncherNet.isAllowed(url, hosts: hosts) {
            completionHandler(request)
        } else {
            refusedOrigin = request.url.map(BCDLauncherNet.origin) ?? "an unknown address"
            completionHandler(nil)
            task.cancel()
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        if totalBytesWritten > maxBytes || totalBytesExpectedToWrite > maxBytes {
            tooLarge = true
            downloadTask.cancel()
            return
        }
        // At most one report per 64 KB: the installers are small.
        guard reported < 0 || totalBytesWritten - reported >= 64 << 10 || totalBytesWritten == totalBytesExpectedToWrite else { return }
        reported = totalBytesWritten
        progress(totalBytesWritten, max(totalBytesExpectedToWrite, 0))
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        // URLSession deletes `location` when this returns: check the answer and move the file now.
        guard let response = downloadTask.response as? HTTPURLResponse, let url = response.url else {
            outcome = .failure(BCDLauncherError.network("No answer from the server."))
            return
        }
        guard BCDLauncherNet.isAllowed(url, hosts: hosts) else {
            outcome = .failure(BCDLauncherError.redirected(BCDLauncherNet.origin(url)))
            return
        }
        guard response.statusCode == 200 else {
            outcome = .failure(BCDLauncherError.http(response.statusCode, company: company))
            return
        }
        do {
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.moveItem(at: location, to: destination)
            outcome = .success(url)
        } catch {
            outcome = .failure(BCDLauncherError.saving(error.localizedDescription))
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let result: Result<URL, Error>
        if let origin = refusedOrigin {
            result = .failure(BCDLauncherError.redirected(origin))
        } else if tooLarge {
            result = .failure(BCDLauncherError.tooLarge)
        } else if let error {
            let cancelled = (error as? URLError)?.code == .cancelled
            result = .failure(cancelled ? BCDLauncherError.cancelled : BCDLauncherError.network((error as? URLError)?.localizedDescription ?? "The connection broke."))
        } else {
            result = outcome ?? .failure(BCDLauncherError.network("The download ended without a file."))
        }
        if case .failure = result { try? FileManager.default.removeItem(at: destination) }
        session.finishTasksAndInvalidate()                             // releases this delegate
        self.session = nil
        finished(result)
    }
}

// MARK: - Download, look, keep

enum BCDLauncherInstaller {
    /// Downloads `spec`'s installer into `folder` as `spec.installerFile`: first as a ".part" file, which is
    /// looked at (its size, then a Windows x86 or x64 program's header) and only then renamed over an older copy.
    /// Throws BCDLauncherError. `log` gets short facts (the host, a status, byte counts), never an address with a path.
    static func fetch(_ spec: BCDLauncherSpec, into folder: URL, url: URL? = nil,
                      configuration: URLSessionConfiguration = BCDLauncherDownloader.configuration(),
                      control: BCDLauncherDownloadControl = BCDLauncherDownloadControl(),
                      log: @escaping (String) -> Void = { _ in },
                      progress: @escaping @Sendable (Int64, Int64) -> Void) async throws -> URL {
        let fm = FileManager.default
        do { try fm.createDirectory(at: folder, withIntermediateDirectories: true) }
        catch { throw BCDLauncherError.saving(error.localizedDescription) }
        let final = folder.appendingPathComponent(spec.installerFile)
        let part = folder.appendingPathComponent(spec.installerFile + ".part")
        try? fm.removeItem(at: part)
        let address = url ?? spec.installerURL
        guard BCDLauncherNet.isAllowed(address, hosts: spec.hosts) else {
            throw BCDLauncherError.redirected(BCDLauncherNet.origin(address))
        }
        log("[launcher] \(spec.id) download from \(BCDLauncherNet.origin(address))")
        let reached: URL = try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<URL, Error>) in
                let downloader = BCDLauncherDownloader(url: address, hosts: spec.hosts, maxBytes: spec.maxBytes, company: spec.company,
                                                       destination: part, configuration: configuration, progress: progress) { result in
                    control.clear()
                    continuation.resume(with: result)
                }
                control.set(downloader)
                downloader.start()
            }
        } onCancel: {
            control.cancel()
        }
        log("[launcher] \(spec.id) download answered from \(BCDLauncherNet.origin(reached))")
        do {
            let size = (try fm.attributesOfItem(atPath: part.path)[.size] as? NSNumber)?.int64Value ?? 0
            guard size >= spec.minBytes else { throw BCDLauncherError.notAnInstaller(company: spec.company) }
            guard size <= spec.maxBytes else { throw BCDLauncherError.tooLarge }
            let data = try Data(contentsOf: part, options: .alwaysMapped)
            guard let info = BCDLauncherPE.inspect(data) else { throw BCDLauncherError.notAnInstaller(company: spec.company) }
            log("[launcher] \(spec.id) installer kept bytes=\(size) bits=\(info.bits) signed=\(info.signed ? 1 : 0)")
            try? fm.removeItem(at: final)
            try fm.moveItem(at: part, to: final)
        } catch let error as BCDLauncherError {
            try? fm.removeItem(at: part)
            throw error
        } catch {
            try? fm.removeItem(at: part)
            throw BCDLauncherError.saving(error.localizedDescription)
        }
        return final
    }
}

/// Lets the card's Cancel reach the download that is running.
final class BCDLauncherDownloadControl: @unchecked Sendable {
    private let lock = NSLock()
    private var downloader: BCDLauncherDownloader?
    private var cancelled = false
    func set(_ next: BCDLauncherDownloader) {
        lock.lock(); defer { lock.unlock() }
        downloader = next
        if cancelled { next.cancel() }
    }
    func clear() { lock.lock(); downloader = nil; lock.unlock() }
    func cancel() { lock.lock(); cancelled = true; let current = downloader; lock.unlock(); current?.cancel() }
}
