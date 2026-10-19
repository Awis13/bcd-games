// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI

/// Add a game's sample backend for the preview app: a small drive_c, kept in memory. Only the
/// preview app compiles this file; the app's backend is BCDAddGameLive.
@MainActor final class BCDAddGameSample: BCDAddGameBackend {
    private let tree: [String: [String]] = [
        "": ["Games/", "Program Files/", "ProgramData/", "users/", "windows/", "setup.exe"],
        "Games": ["Hollow Knight/", "Warcraft III/"],
        "Games/Warcraft III": ["Maps/", "Movies/", "Frozen Throne.exe", "Warcraft III.exe", "World Editor.exe", "war3.exe", "start.bat"],
        "Games/Hollow Knight": ["hollow_knight_Data/", "hollow_knight.exe"],
    ]

    func list(_ folder: String) throws -> [BCDDriveItem] {
        (tree[folder] ?? []).map { name in
            let isFolder = name.hasSuffix("/")
            let clean = isFolder ? String(name.dropLast()) : name
            return BCDDriveItem(name: clean, path: folder.isEmpty ? clean : folder + "/" + clean, folder: isFolder)
        }
    }

    func inspect(_ path: String) throws -> BCDProgramInfo {
        let name = (path as NSString).lastPathComponent
        if name == "setup.exe" { throw BCDGameError("BCD runs x86 and x64 programs only.") }
        let title = (name as NSString).deletingPathExtension.replacingOccurrences(of: "_", with: " ")
        let windows = "C:\\" + path.replacingOccurrences(of: "/", with: "\\")
        if name.hasSuffix(".bat") { return BCDProgramInfo(title: title, windowsPath: windows, bits: 0) }
        return BCDProgramInfo(title: title, windowsPath: windows, bits: name.contains("hollow") ? 64 : 32,
                              api: name.contains("hollow") ? "D3D11" : "D3D8")
    }

    func add(_ path: String, title: String) throws -> String { path.contains("Warcraft") ? "wc3" : "wc2" }

    func addEveryGame() async -> Int {
        try? await Task.sleep(nanoseconds: 600_000_000)
        return 2
    }
}

/// `-BCDTab add[-<what>]`: Add a game alone: drive_c, `games` (a game's folder) or `pick` (its
/// program chosen, the name to check).
struct BCDAddGamePreviewRoot: View {
    let spec: String

    var body: some View {
        let what = spec.split(separator: "-").dropFirst().first.map(String.init) ?? ""
        let folder = what.isEmpty ? "" : "Games/Warcraft III"
        BCDAddGame(folder: folder, pick: what == "pick" ? "Games/Warcraft III/Warcraft III.exe" : nil,
                   close: { print("[preview] close") }, added: { print("[preview] added \($0)") })
    }
}
