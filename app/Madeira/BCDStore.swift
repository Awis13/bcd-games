// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI

// BCD's interface reads everything it shows from one store and asks for everything it does
// through one set of actions. It never touches the engine (Wine, FEX, the Steam client,
// JIT, Dock) directly: in the app BCDLiveStore fills the store from the engine's models and
// carries the actions out; in the preview app (app/BCDPreview) BCDStore.preview() fills it
// with sample data, so every screen can be built and photographed in the simulator.
// docs/BCD-UI-ARCHITECTURE.md.

// MARK: - What the interface shows

enum BCDStoreFront: String, CaseIterable, Codable, Hashable {
    case steam, epic, gog, local
    var title: String {
        switch self {
        case .steam: return "Steam"
        case .epic: return "Epic Games"
        case .gog: return "GOG"
        case .local: return "Local"
        }
    }
}

enum BCDInstallPlace: String, Codable, Hashable {
    case device, externalDrive
}

enum BCDGameState: Hashable {
    case ready
    case notInstalled
    case queued
    case downloading(progress: Double)
    case installing(progress: Double)
    case updateAvailable(bytes: Int64)
    case broken(reason: String)

    var isInstalled: Bool {
        switch self {
        case .ready, .updateAvailable, .broken: return true
        default: return false
        }
    }
}

struct BCDGame: Identifiable, Hashable {
    var id: String
    var title: String
    var store: BCDStoreFront
    var state: BCDGameState
    /// Bytes on disk when installed, else the download size when known.
    var size: Int64?
    var place: BCDInstallPlace = .device
    var lastPlayed: Date?
    var minutesPlayed: Int = 0
    /// The store's pictures, each a list of addresses tried in order (a store keeps some only
    /// under newer paths): the tall cover (about 600x900), the wide header (about 460x215),
    /// the wide hero banner (about 1920x620) and the title logo (transparent).
    var coverArt: [URL] = []
    var headerArt: [URL] = []
    var heroArt: [URL] = []
    var logoArt: [URL] = []
    /// Two colours for the artless placeholder, stable per game.
    var tint: (Color, Color) { BCDGame.tint(for: id) }
    var autoTuned = false
    var recommended = false
    var thirtyTwoBit = false
    /// Its place in the library's list of games (a later number is newer): what Library › Sort by ›
    /// Recently added orders by. nil for a game the library has no entry for (an account's game
    /// Steam has not installed, the Wine desktop before its first start).
    var added: Int? = nil

    static func == (a: BCDGame, b: BCDGame) -> Bool {
        a.id == b.id && a.title == b.title && a.store == b.store && a.state == b.state && a.size == b.size
            && a.place == b.place && a.lastPlayed == b.lastPlayed && a.minutesPlayed == b.minutesPlayed
            && a.coverArt == b.coverArt && a.headerArt == b.headerArt && a.heroArt == b.heroArt
            && a.logoArt == b.logoArt && a.autoTuned == b.autoTuned
            && a.recommended == b.recommended && a.thirtyTwoBit == b.thirtyTwoBit && a.added == b.added
    }
    func hash(into h: inout Hasher) { h.combine(id) }

    static func tint(for id: String) -> (Color, Color) {
        let palette: [(Color, Color)] = [
            (Color(red: 0.23, green: 0.11, blue: 0.05), Color(red: 0.54, green: 0.23, blue: 0.07)),
            (Color(red: 0.05, green: 0.16, blue: 0.28), Color(red: 0.17, green: 0.42, blue: 0.66)),
            (Color(red: 0.23, green: 0.05, blue: 0.18), Color(red: 0.70, green: 0.23, blue: 0.42)),
            (Color(red: 0.06, green: 0.19, blue: 0.12), Color(red: 0.25, green: 0.56, blue: 0.35)),
            (Color(red: 0.16, green: 0.07, blue: 0.25), Color(red: 0.42, green: 0.25, blue: 0.70)),
            (Color(red: 0.17, green: 0.17, blue: 0.06), Color(red: 0.56, green: 0.50, blue: 0.16)),
        ]
        var hash: UInt64 = 1469598103934665603
        for byte in id.utf8 { hash = (hash ^ UInt64(byte)) &* 1099511628211 }
        return palette[Int(hash % UInt64(palette.count))]
    }
}

struct BCDDownload: Identifiable, Hashable {
    var id: String              // the game's id
    var title: String
    var store: BCDStoreFront
    var doneBytes: Int64
    var totalBytes: Int64
    var bytesPerSecond: Double
    var paused = false
    var failed: String?
    var fraction: Double { totalBytes > 0 ? min(1, Double(doneBytes) / Double(totalBytes)) : 0 }
}

struct BCDFinishedDownload: Identifiable, Hashable {
    var id: String
    var title: String
    var bytes: Int64
    var finished: Date
}

struct BCDAccount: Identifiable, Hashable {
    var id: BCDStoreFront { store }
    var store: BCDStoreFront
    var name: String?           // nil: not signed in
    var available: Bool         // the store is offered in this build
}

enum BCDJITState: Hashable {
    case on, off, waiting
}

struct BCDSystemStatus: Hashable {
    var jit: BCDJITState = .off
    /// What iOS lets the app use, measured; nil before the first measurement.
    var memoryLimitBytes: Int64?
    var increasedMemoryLimit = false
    /// The extended virtual addressing entitlement (a ~63 GB address map without it). True until measured, so nothing shows early.
    var extendedAddressing = true
    var controllerName: String?
    var controllerBattery: Int?     // percent
    var freeDeviceBytes: Int64?
    var totalDeviceBytes: Int64?
    var freeExternalBytes: Int64?
}

// MARK: - What the interface asks for

@MainActor protocol BCDActions: AnyObject {
    func play(_ game: BCDGame)
    func install(_ game: BCDGame, place: BCDInstallPlace)
    func update(_ game: BCDGame)
    func uninstall(_ game: BCDGame)
    func verify(_ game: BCDGame)
    func pauseDownload(_ id: String)
    func resumeDownload(_ id: String)
    func cancelDownload(_ id: String)
    func enableJIT()
    func signIn(_ store: BCDStoreFront)
    func signOut(_ store: BCDStoreFront)
    func addGameFromFiles()
    func refreshLibrary()
}

// MARK: - The store

@MainActor final class BCDStore: ObservableObject {
    @Published var games: [BCDGame] = []
    @Published var downloads: [BCDDownload] = []
    @Published var finishedToday: [BCDFinishedDownload] = []
    @Published var accounts: [BCDAccount] = []
    @Published var system = BCDSystemStatus()
    /// A game is running: the interface is torn down and draws nothing until it ends.
    @Published var inGame = false
    /// The game whose Play is turning JIT on before it starts (its Play reads so); nil otherwise.
    @Published var startingJIT: String?
    /// The games whose Steam Cloud saves differ from this device's: they wait for a choice on each
    /// game's page (Options › Cloud saves), and the Library says so. Empty without Steam Cloud.
    @Published var savesDiffer: [String] = []
    weak var actions: BCDActions?

    init(actions: BCDActions? = nil) { self.actions = actions }

    var recent: [BCDGame] {
        games.filter { $0.lastPlayed != nil }.sorted { ($0.lastPlayed ?? .distantPast) > ($1.lastPlayed ?? .distantPast) }
    }
    var continuePlaying: BCDGame? { recent.first { $0.state.isInstalled } }
    func count(_ store: BCDStoreFront?) -> Int { store.map { s in games.filter { $0.store == s }.count } ?? games.count }
    var installedCount: Int { games.filter { $0.state.isInstalled }.count }
}

// MARK: - Formatting shared by the screens

enum BCDFormat {
    static func bytes(_ value: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: value, countStyle: .file)
    }
    static func ago(_ date: Date, now: Date = Date()) -> String {
        let seconds = max(0, now.timeIntervalSince(date))
        switch seconds {
        case ..<60: return "just now"
        case ..<3600: return "\(Int(seconds / 60)) min ago"
        case ..<86400: return "\(Int(seconds / 3600)) h ago"
        case ..<(86400 * 7): return "\(Int(seconds / 86400)) d ago"
        default: return shortDate(date, now: now)
        }
    }
    // shortDate-begin
    /// "12 Oct" (this year) or "12 Oct 2025", from numbers only. `Date.formatted` builds an ICU date formatter on
    /// every call and the Home and Library cards call this on every draw: one of those calls crashed the interface
    /// the moment a game started (a tester's log, KDY8E6, 2026-10-19). The screens are English, so a fixed table does.
    static func shortDate(_ date: Date, now: Date = Date()) -> String {
        func civil(_ d: Date) -> (year: Int, month: Int, day: Int) {
            let local = d.timeIntervalSince1970 + Double(TimeZone.current.secondsFromGMT(for: d))
            let z = Int((local / 86400).rounded(.down)) + 719_468
            let era = (z >= 0 ? z : z - 146_096) / 146_097
            let doe = z - era * 146_097
            let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365
            let doy = doe - (365 * yoe + yoe / 4 - yoe / 100)
            let mp = (5 * doy + 2) / 153
            let month = mp < 10 ? mp + 3 : mp - 9
            return (yoe + era * 400 + (month <= 2 ? 1 : 0), month, doy - (153 * mp + 2) / 5 + 1)
        }
        let months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        let then = civil(date), today = civil(now)
        let text = "\(then.day) \(months[then.month - 1])"
        return then.year == today.year ? text : text + " \(then.year)"
    }
    // shortDate-end
    static func playtime(_ minutes: Int) -> String {
        minutes < 60 ? "\(minutes) min" : String(format: "%.1f h", Double(minutes) / 60)
    }
    static func eta(_ download: BCDDownload) -> String? {
        guard download.bytesPerSecond > 1, download.totalBytes > download.doneBytes else { return nil }
        let seconds = Double(download.totalBytes - download.doneBytes) / download.bytesPerSecond
        if seconds < 90 { return "1 min left" }
        let minutes = Int((seconds / 60).rounded())
        return minutes < 60 ? "\(minutes) min left" : "\(minutes / 60) h \(minutes % 60) min left"
    }
}
