//
//  GameFiles.swift
//  Madeira
//
//  What the games in drive_c look like from outside: a title and its executables
//  (LibraryGame, for BCDLibraryImport), a program's CPU architecture (PEInfo, for
//  DeviceCapability) and a game's chosen cover picture (CoverStore, for BCDSessionLive,
//  GameProfiles and GameRecommendationsApply). Wave 3 (2026-10-19): this fork's earlier home
//  screen, which these types came from, is gone; BCD's interface is the only one.
//

import SwiftUI
import UIKit

// MARK: - Library model

/// One title as a person thinks of it: a folder under Program Files (or C:\)
/// with every launchable exe inside it, biggest first.
struct LibraryGame: Identifiable {
    let title: String
    let executables: [GuestExecutable]
    /// Set when one title is shown as several cards (one per exe).
    var key: String? = nil

    var id: String { key ?? title }

    /// The exe the card launches: the one chosen in settings, else the biggest.
    /// The biggest is a good default because a game's shipping binary dwarfs
    /// the launchers and crash reporters next to it. 32-bit exes sort after
    /// every 64-bit one (see group), so a folder holding both a 32-bit setup
    /// or launcher and the 64-bit game defaults to the one that can run.
    var primary: GuestExecutable {
        if let chosen = LibraryPrefs.primaryPath(for: title),
           let exe = executables.first(where: { $0.windowsPath == chosen }) {
            return exe
        }
        return executables[0]
    }

    static func group(_ found: [GuestExecutable]) -> [LibraryGame] {
        var order: [String] = []
        var byTitle: [String: [GuestExecutable]] = [:]
        for exe in found {
            if byTitle[exe.title] == nil { order.append(exe.title) }
            byTitle[exe.title, default: []].append(exe)
        }
        return order.map { title in
            // Called off the main thread (rescan), so the PE header reads are fine here.
            let is32 = Dictionary((byTitle[title] ?? []).map { ($0.windowsPath, PEInfo.archLabel($0.url) == "x86") },
                                  uniquingKeysWith: { a, _ in a })
            let exes = (byTitle[title] ?? []).sorted { a, b in
                let a32 = is32[a.windowsPath] ?? false, b32 = is32[b.windowsPath] ?? false
                return a32 != b32 ? !a32 : a.sizeBytes > b.sizeBytes
            }
            return LibraryGame(title: title, executables: exes)
        }
    }
}

/// Reads the PE header's Machine field. Two small reads per file, off the main
/// thread, and it tells a person whether a card is x86-64 (emulated) or native.
enum PEInfo {
    static func archLabel(_ url: URL) -> String? {
        guard let machine = machine(url) else { return nil }
        switch machine {
        case 0x8664: return "x64"
        case 0xAA64: return "ARM64"
        case 0xA641: return "ARM64EC"
        case 0x014C: return "x86"
        default: return nil
        }
    }

    private static func machine(_ url: URL) -> UInt16? {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        guard let dos = try? handle.read(upToCount: 64), dos.count == 64 else { return nil }
        let bytes = [UInt8](dos)
        guard bytes[0] == 0x4D, bytes[1] == 0x5A else { return nil }
        var lfanew: UInt64 = 0
        for i in 0..<4 { lfanew |= UInt64(bytes[0x3C + i]) << (8 * UInt64(i)) }
        guard (try? handle.seek(toOffset: lfanew)) != nil,
              let header = try? handle.read(upToCount: 6), header.count == 6 else { return nil }
        let h = [UInt8](header)
        guard h[0] == 0x50, h[1] == 0x45, h[2] == 0, h[3] == 0 else { return nil }
        return UInt16(h[4]) | (UInt16(h[5]) << 8)
    }
}

/// User-chosen cover art. Kept in Application Support, not Documents, so it
/// does not clutter the folder the Files app shows as the Windows drive.
enum CoverStore {
    private static var directory: URL? {
        guard let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            return nil
        }
        let dir = base.appendingPathComponent("Covers", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// FNV-1a over the title: stable across launches, unlike String.hashValue.
    static func stableHash(_ s: String) -> UInt64 {
        var h: UInt64 = 0xcbf29ce484222325
        for b in s.utf8 { h = (h ^ UInt64(b)) &* 0x100000001b3 }
        return h
    }

    private static func url(for title: String) -> URL? {
        directory?.appendingPathComponent(String(format: "%016llx.jpg", stableHash(title)))
    }

    static func image(for title: String) -> UIImage? {
        guard let u = url(for: title) else { return nil }
        return UIImage(contentsOfFile: u.path)
    }

    static func hasCover(_ title: String) -> Bool {
        guard let u = url(for: title) else { return false }
        return FileManager.default.fileExists(atPath: u.path)
    }

    /// Downscaled on save: a 12-megapixel photo is 30 MB decoded, and a grid
    /// of them would cost more memory than the game it is a picture of.
    static func save(_ data: Data, for title: String) {
        guard let u = url(for: title), let img = UIImage(data: data) else { return }
        let maxSide: CGFloat = 900
        let scale = min(1, maxSide / max(img.size.width, img.size.height))
        let size = CGSize(width: img.size.width * scale, height: img.size.height * scale)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let scaled = UIGraphicsImageRenderer(size: size, format: format).image { _ in
            img.draw(in: CGRect(origin: .zero, size: size))
        }
        try? scaled.jpegData(compressionQuality: 0.85)?.write(to: u, options: .atomic)
    }

    static func remove(_ title: String) {
        guard let u = url(for: title) else { return }
        try? FileManager.default.removeItem(at: u)
    }
}
