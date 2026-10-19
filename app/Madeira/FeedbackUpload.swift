// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  FeedbackUpload.swift
//  Madeira
//
//  madeira-bcd FORK-ONLY: never in a pull request to upstream (owner, 2026-10-19).
//
//  Settings › Feedback: a message (what is broken, what should be better) goes to the fork's
//  own intake, the Cloudflare Worker that Send Log uses (bcd-logs/worker, POST /v1/feedback),
//  and the intake answers with a short code. Same host, same X-BCD-Intake header; the app
//  holds no key. By hand only, one message at a time. The body is FeedbackUploadCore.swift's:
//  the person's words, the BCD build, the iOS version, the device model and, if a log was
//  sent with it, that log's code. Nothing else leaves: no name, no account, no device name,
//  no address.
//

import Foundation

enum FeedbackUpload {
    static let endpoint = URL(string: "https://madeira-log-intake.bcd-logs.workers.dev/v1/feedback")!
    static let intake = "madeira-bcd/2"
    private static let historyKey = "madeira.bcd.feedback.sent"
    private static let kept = 20

    /// One message that was sent: its code, its kind, when, and its first line.
    struct Sent: Codable, Identifiable, Equatable {
        var code: String
        var kind: String
        var date: Date
        var line: String
        var id: String { code }
    }

    /// The last 20 sent, newest first.
    static var history: [Sent] {
        guard let d = UserDefaults.standard.data(forKey: historyKey),
              let list = try? JSONDecoder().decode([Sent].self, from: d) else { return [] }
        return Array(list.prefix(kept))
    }

    private static func remember(_ s: Sent) {
        let list = Array(([s] + history).prefix(kept))
        if let d = try? JSONEncoder().encode(list) { UserDefaults.standard.set(d, forKey: historyKey) }
    }

    /// What went wrong, in plain words.
    enum UploadError: LocalizedError {
        case problem(FeedbackCore.Problem), refused(Int, String), noCode
        var errorDescription: String? {
            switch self {
            case .problem(.empty): return "Write something first."
            case .problem(.tooLong): return "That message is too long. The most is \(FeedbackCore.maxCharacters) characters."
            case .problem(.bodyTooLarge): return "That message is too long to send. Shorten it a little."
            case .problem(.badKind): return "Choose Bug, Idea or Other."
            case .refused(429, _): return "You have sent a few just now. Wait a minute and try again."
            case .refused(403, _): return "The server did not accept this version of BCD."
            case .refused(let status, _) where status >= 500: return "The server had a problem. Try again in a while."
            case .refused(let status, let why): return "The server refused it (\(status)\(why.isEmpty ? "" : ": " + why))."
            case .noCode: return "The server did not answer with a code."
            }
        }
    }

    /// The device's model identifier ("iPhone14,2"), as the system names it.
    private static var deviceModel: String {
        var u = utsname()
        uname(&u)
        return withUnsafeBytes(of: &u.machine) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
    }

    /// Sends one message; `done` runs on the main queue with the intake's code. `log` is the code
    /// LogUpload.send gave a log sent with it.
    static func send(kind: String, text: String, log: String? = nil, done: @escaping (Result<String, Error>) -> Void) {
        func finish(_ r: Result<String, Error>) {
            switch r {
            case .success(let code): LogStore.shared.log("[bcd-feedback] sent: code \(code)")
            case .failure(let e): LogStore.shared.log("[bcd-feedback] could not send: \(e.localizedDescription)", level: .error)
            }
            DispatchQueue.main.async { done(r) }
        }
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        let os = ProcessInfo.processInfo.operatingSystemVersion
        let body: Data
        do {
            body = try FeedbackCore.body(kind: kind, text: text, build: build,
                                         ios: "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)",
                                         model: deviceModel, log: log)
        } catch let problem as FeedbackCore.Problem {
            return finish(.failure(UploadError.problem(problem)))
        } catch {
            return finish(.failure(error))
        }
        var req = URLRequest(url: endpoint)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(intake, forHTTPHeaderField: "X-BCD-Intake")
        req.httpBody = body
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 90
        let session = URLSession(configuration: config)
        let line = FeedbackCore.firstLine(text)
        session.dataTask(with: req) { data, response, error in
            session.finishTasksAndInvalidate()
            if let error { return finish(.failure(error)) }
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            let answer = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]) ?? [:]
            guard status == 200 else { return finish(.failure(UploadError.refused(status, answer["error"] as? String ?? ""))) }
            guard let code = answer["code"] as? String, !code.isEmpty else { return finish(.failure(UploadError.noCode)) }
            remember(Sent(code: code, kind: kind, date: Date(), line: line))
            finish(.success(code))
        }.resume()
    }
}
