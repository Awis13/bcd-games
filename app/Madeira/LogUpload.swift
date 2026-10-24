// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  LogUpload.swift
//  Madeira
//
//  madeira-bcd FORK-ONLY: never in a pull request to upstream (owner, 2026-10-17).
//
//  Settings › "Send Log": a session log goes to the fork's own log intake (a
//  Cloudflare Worker, bcd-logs/worker) so that testers' crashes reach auto-tune's rules
//  without anyone mailing files. The log is scrubbed and gzip'ed on the phone
//  (LogUploadCore.swift), sent, and the compressed copy deleted the moment the upload
//  ends, sent or not. The app holds no storage key; the intake answers with a short code
//  the tester can quote.
//
//  By hand only, and only the 20 newest logs (owner, 2026-10-17: build 562's automatic
//  sending would bring far too many logs, working sessions included, and an older log must
//  not be one tap away from being sent: "Yoksa çok suistimal edilir").
//

import Foundation
import SwiftUI
import UIKit

enum LogUpload {
    static let endpoint = URL(string: "https://bcd.bcd-logs.workers.dev/v1/logs")!
    static let intake = "madeira-bcd/2"
    static let maxPacked = 25 << 20
    /// The newest logs the list offers; nothing older can be sent.
    static let listed = 20
    private static let historyKey = "madeira.bcd.logs.sent"

    struct Sent: Codable, Identifiable {
        var name: String
        var code: String
        var date: Date
        var id: String { code }
    }

    static var history: [Sent] {
        guard let d = UserDefaults.standard.data(forKey: historyKey),
              let list = try? JSONDecoder().decode([Sent].self, from: d) else { return [] }
        return list
    }

    private static func remember(_ s: Sent) {
        let list = Array(([s] + history).prefix(20))
        if let d = try? JSONEncoder().encode(list) { UserDefaults.standard.set(d, forKey: historyKey) }
    }

    enum UploadError: LocalizedError {
        case tooLarge(Int), refused(Int, String), noCode, notRecent
        var errorDescription: String? {
            switch self {
            case .notRecent: return "Only the \(LogUpload.listed) newest logs can be sent."
            case .tooLarge(let n): return "The compressed log is \(n >> 20) MB; the limit is \(LogUpload.maxPacked >> 20) MB."
            case .refused(let status, let why): return "The server refused it (\(status)\(why.isEmpty ? "" : ": " + why))."
            case .noCode: return "The server did not answer with a code."
            }
        }
    }

    private static let queue = DispatchQueue(label: "madeira.bcd.log-upload", qos: .utility)

    /// The program a session log is named after ("war3.exe" from "war3.exe-2026-10-16_22-24-07.txt").
    static func game(of log: URL) -> String {
        let name = log.deletingPathExtension().lastPathComponent
        if let r = name.range(of: #"-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$"#, options: .regularExpression) {
            return String(name[..<r.lowerBound])
        }
        return name
    }

    private static var deviceModel: String {
        var u = utsname()
        uname(&u)
        return withUnsafeBytes(of: &u.machine) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
    }

    /// Packs and sends one of the `listed` newest logs; `done` runs on the main queue with the
    /// intake's code.
    static func send(_ log: URL, game: String, comment: String? = nil,
                     progress: ((String) -> Void)? = nil, done: ((Result<String, Error>) -> Void)? = nil) {
        func finish(_ r: Result<String, Error>) {
            switch r {
            case .success(let code):
                remember(Sent(name: log.lastPathComponent, code: code, date: Date()))
                let commented = LogUploadCore.commentLines(comment).isEmpty ? "" : " (with the sender's comment)"
                LogStore.shared.log("[bcd-logs] sent \(log.lastPathComponent): code \(code)\(commented)")
            case .failure(let e):
                LogStore.shared.log("[bcd-logs] could not send \(log.lastPathComponent): \(e.localizedDescription)", level: .error)
            }
            if let done { DispatchQueue.main.async { done(r) } }
        }
        let say: (String) -> Void = { text in if let progress { DispatchQueue.main.async { progress(text) } } }
        queue.async {
            guard recentLogs().contains(where: { $0.standardizedFileURL == log.standardizedFileURL }) else {
                return finish(.failure(UploadError.notRecent))
            }
            let gz = FileManager.default.temporaryDirectory.appendingPathComponent("bcd-log-\(UUID().uuidString).gz")
            let packed: (raw: Int, packed: Int)
            do {
                say("Compressing…")
                packed = try LogUploadCore.pack(log, to: gz, comment: comment) { f in say("Compressing… \(Int(f * 100))%") }
            } catch {
                try? FileManager.default.removeItem(at: gz)
                return finish(.failure(error))
            }
            guard packed.packed <= maxPacked else {
                try? FileManager.default.removeItem(at: gz)
                return finish(.failure(UploadError.tooLarge(packed.packed)))
            }
            say("Sending \(max(1, packed.packed >> 20)) MB…")
            var req = URLRequest(url: endpoint)
            req.httpMethod = "POST"
            req.setValue("application/gzip", forHTTPHeaderField: "Content-Type")
            req.setValue(intake, forHTTPHeaderField: "X-BCD-Intake")
            let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
            let os = ProcessInfo.processInfo.operatingSystemVersion
            for (k, v) in ["Build": build, "Game": game, "Device": deviceModel,
                           "Os": "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)",
                           "Kind": "manual"] {
                req.setValue(v, forHTTPHeaderField: "X-Madeira-" + k)
            }
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForResource = 600
            let session = URLSession(configuration: config)
            session.uploadTask(with: req, fromFile: gz) { data, response, error in
                // Sent or not, the compressed copy goes now (owner: "gönderildiği an silinir").
                try? FileManager.default.removeItem(at: gz)
                session.finishTasksAndInvalidate()
                if let error { return finish(.failure(error)) }
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                let body = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]) ?? [:]
                guard status == 200 else { return finish(.failure(UploadError.refused(status, body["error"] as? String ?? ""))) }
                guard let code = body["code"] as? String, !code.isEmpty else { return finish(.failure(UploadError.noCode)) }
                finish(.success(code))
            }.resume()
        }
    }

    // MARK: Logs on this device

    /// The `listed` newest logs: session logs and the app's own two, newest first.
    static func recentLogs() -> [URL] {
        guard let docs = MadeiraConfig.documents else { return [] }
        let fm = FileManager.default
        let dir = docs.appendingPathComponent("logs")
        let runs = ((try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey])) ?? [])
            .filter { $0.pathExtension == "txt" }
        let own = ["bcd-log.txt", "bcd-log.prev.txt"].map { docs.appendingPathComponent($0) }.filter { fm.fileExists(atPath: $0.path) }
        return Array((runs + own).sorted { modified($0) > modified($1) }.prefix(listed))
    }

    static func modified(_ url: URL) -> Date {
        (try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
    }

    static func size(_ url: URL) -> Int {
        (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.intValue ?? 0
    }

    /// A log named in auto-tune's history ("bcd-log.prev.txt", or a logs/ name).
    static func log(named name: String) -> URL? {
        guard let docs = MadeiraConfig.documents else { return nil }
        return [docs.appendingPathComponent(name), docs.appendingPathComponent("logs").appendingPathComponent(name)]
            .first { FileManager.default.fileExists(atPath: $0.path) }
    }

    /// Build 562 had automatic sending: its setting and session marker go.
    static func forgetAutomatic() {
        UserDefaults.standard.removeObject(forKey: "madeira.bcd.logs.autosend")
        if let marker = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("bcd-log-session.json") { try? FileManager.default.removeItem(at: marker) }
    }
}
