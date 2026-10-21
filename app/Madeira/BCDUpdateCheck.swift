// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// A newer BCD, said once a day at most (owner, 2026-10-21: people still send logs from builds two or three
// releases old). At most one question online a day, a few seconds after the library first appears: BCD's
// AltStore/SideStore source (published from every GitHub release, tools/update-altsource.py) names the newest
// build. When it is newer than this one, a small alert with OK says so; nothing is blocked, nothing is
// downloaded or installed, and no game session is interrupted (the alert waits for the library).
// env.MADEIRA_UPDATE_CHECK = 0 turns it off. The core is Foundation only (tests/host/check-update-check.py).

enum BCDUpdateCheck {
    static let sourceURL = URL(string: "https://thebcd69.github.io/bcd-games/source.json")!
    /// The source file is about 7 KB; anything far larger is not it.
    static let maxBytes = 256 << 10
    /// The local day of the last answer from the source (yyyy-MM-dd).
    static let lastDayKey = "bcd.update.lastCheckDay"

    struct Release: Equatable {
        var build: Int
        var version: String
    }

    /// The newest release the AltStore source names: its first app's first version (the source lists one).
    static func latest(from data: Data) -> Release? {
        guard data.count <= maxBytes,
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let apps = root["apps"] as? [[String: Any]], let app = apps.first,
              let versions = app["versions"] as? [[String: Any]], let newest = versions.first,
              let buildText = newest["buildVersion"] as? String, let build = Int(buildText), build > 0,
              let version = newest["version"] as? String, !version.isEmpty, version.count <= 32 else { return nil }
        return Release(build: build, version: version)
    }

    /// This app's build (CFBundleVersion) and version, or nil when the build is not a number.
    static func current(_ info: [String: Any]? = Bundle.main.infoDictionary) -> Release? {
        guard let text = info?["CFBundleVersion"] as? String, let build = Int(text), build > 0 else { return nil }
        return Release(build: build, version: (info?["CFBundleShortVersionString"] as? String) ?? text)
    }

    /// The local calendar day, the unit of "once a day".
    static func day(_ date: Date, calendar: Calendar = .current) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    /// A check is due unless one already got an answer today.
    static func due(lastDay: String?, today: String) -> Bool { lastDay != today }

    /// What the alert says, or nil when this build is not older.
    static func notice(latest: Release, current: Release) -> String? {
        guard latest.build > current.build else { return nil }
        return "BCD \(latest.version) is available; this is \(current.version). Update with AltStore or SideStore, "
            + "or from BCD's releases on GitHub. Please update before sending a log: a problem in an older version "
            + "may already be fixed."
    }

    private static let session: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        configuration.urlCache = nil
        configuration.timeoutIntervalForRequest = 15
        configuration.timeoutIntervalForResource = 30
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration)
    }()

    /// The source, or nil: no connection, an error, a redirect to another host, or too large.
    static func fetch() async -> Data? {
        guard let (data, response) = try? await session.data(from: sourceURL),
              let http = response as? HTTPURLResponse, http.statusCode == 200,
              http.url?.scheme == "https", http.url?.host == sourceURL.host, data.count <= maxBytes else { return nil }
        return data
    }
}
