// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

/// The preview app's memory walk (tools/ui-memory.py; docs/BCD-UI-ARCHITECTURE.md, Performance).
/// Arguments:
///   -BCDSampleGames N   N more games, each with the bundled store art under its own file names
///                       (links in Caches), so every cover decodes on its own: a large library.
///   -BCDMeasure YES     walks the interface as a player does and prints the app's footprint
///                       after each step, then waits for the host to read `footprint <pid>`:
///                       Home, Library, the Library scrolled to its end and back to the top,
///                       a game started from there (store.inGame) and ended, Home again, a
///                       game's page open and closed.
/// Only the preview app has this file; the app never builds it.
@MainActor enum BCDPreviewMeasure {
    /// Seconds the walk waits after printing a step, while the host reads the footprint.
    private static let hostWindow: Double = 3

    private static var started = false

    static func start(store: BCDStore, focus: BCDFocus) {
        guard !started else { return }
        started = true
        let defaults = UserDefaults.standard
        let extra = defaults.integer(forKey: "BCDSampleGames")
        if extra > 0 { addSampleGames(extra, to: store) }
        guard defaults.bool(forKey: "BCDMeasure") else { return }
        Task { await walk(store: store, focus: focus) }
    }

    // MARK: A large library

    static func addSampleGames(_ count: Int, to store: BCDStore) {
        let fm = FileManager.default
        let bundled = Bundle.main.resourceURL?.appendingPathComponent("PreviewArt", isDirectory: true)
        let apps = ((bundled.flatMap { try? fm.contentsOfDirectory(at: $0, includingPropertiesForKeys: nil) }) ?? [])
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        let root = fm.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("BCDPreviewMeasure", isDirectory: true)
        try? fm.removeItem(at: root)
        let now = Date()
        var games: [BCDGame] = []
        for i in 0..<count {
            var game = BCDGame(id: "sample.\(i)", title: String(format: "Sample game %03d", i + 1), store: .steam,
                               state: i % 3 == 2 ? .notInstalled : .ready, size: Int64(1 + i % 40) * 1_000_000_000)
            game.lastPlayed = i % 2 == 0 ? now - TimeInterval(600 + i) * 3600 : nil
            if !apps.isEmpty {
                let source = apps[i % apps.count]
                let folder = root.appendingPathComponent(String(i), isDirectory: true)
                try? fm.createDirectory(at: folder, withIntermediateDirectories: true)
                func art(_ name: String) -> [URL] {
                    let from = source.appendingPathComponent(name)
                    guard fm.fileExists(atPath: from.path) else { return [] }
                    let link = folder.appendingPathComponent(name)
                    try? fm.createSymbolicLink(at: link, withDestinationURL: from)
                    return [link]
                }
                game.coverArt = art("library_600x900.jpg")
                game.headerArt = art("header.jpg")
                game.heroArt = art("library_hero.jpg")
                game.logoArt = art("logo.png")
            }
            games.append(game)
        }
        store.games += games
        print("BCDMeasure: \(count) sample games added (\(apps.count) art sets)")
        fflush(stdout)
    }

    // MARK: The walk

    private static func walk(store: BCDStore, focus: BCDFocus) async {
        await pause(6)   // the first screen and its art
        await report("home")
        focus.press(.rightShoulder)   // Home → Library (the shell's RB)
        focus.touched()
        await pause(4)
        await report("library-top")
        await scroll(toEnd: true)
        await pause(4)
        await report("library-end")
        await scroll(toEnd: false)
        await pause(4)
        await report("library-top-again")
        // A game starts from the Library: the shell is gone and the pictures dropped.
        store.inGame = true
        await pause(4)
        await report("in-game")
        store.inGame = false
        await pause(6)
        await report("after-game")
        focus.press(.leftShoulder)   // Library → Home
        focus.touched()
        await pause(4)
        await report("home-again")
        // The first recent game's page, opened as a tap on its cover opens it.
        if let game = store.recent.first(where: { !$0.heroArt.isEmpty }) ?? store.recent.first {
            focus.preview(focused: "home.recent.\(game.id)")
            focus.press(.a)
            focus.touched()
            await pause(4)
            await report("game-page")
            topPresented()?.dismiss(animated: false)
            await pause(4)
            await report("game-page-closed")
        }
        print("BCDMeasure: done")
        fflush(stdout)
    }

    private static func pause(_ seconds: Double) async {
        try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }

    private static func report(_ step: String) async {
        print("BCDMeasure: \(step) footprint=\(footprint())")
        fflush(stdout)
        await pause(hostWindow)
    }

    /// What iOS charges the app (phys_footprint), as jetsam counts it.
    private static func footprint() -> UInt64 {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        return result == KERN_SUCCESS ? info.phys_footprint : 0
    }

    private static func keyWindow() -> UIWindow? {
        UIApplication.shared.connectedScenes.compactMap { ($0 as? UIWindowScene)?.keyWindow }.first
    }

    private static func topPresented() -> UIViewController? {
        keyWindow()?.rootViewController?.presentedViewController
    }

    /// The tallest scrolling area on screen (the library's grid), moved a screen at a time as a
    /// finger would, so the grid builds and drops its cards on the way.
    private static func scroll(toEnd: Bool) async {
        var settled = 0
        for _ in 0..<2000 {
            guard let scroll = tallestScrollView() else {
                print("BCDMeasure: no scrolling area")
                return
            }
            let top = -scroll.adjustedContentInset.top
            let bottom = max(top, scroll.contentSize.height - scroll.bounds.height + scroll.adjustedContentInset.bottom)
            let step = scroll.bounds.height * 0.6
            let y = min(bottom, max(top, scroll.contentOffset.y + (toEnd ? step : -step)))
            let atEdge = toEnd ? y >= bottom - 0.5 : y <= top + 0.5
            scroll.setContentOffset(CGPoint(x: scroll.contentOffset.x, y: y), animated: false)
            await pause(0.2)
            // A lazy grid grows its content as it builds: the end is reached when it stays put.
            settled = atEdge ? settled + 1 : 0
            if settled >= 3 { return }
        }
    }

    private static func tallestScrollView() -> UIScrollView? {
        var best: UIScrollView?
        func walk(_ view: UIView) {
            if let scroll = view as? UIScrollView, scroll.contentSize.height > scroll.bounds.height + 1,
               scroll.contentSize.height > (best?.contentSize.height ?? 0) {
                best = scroll
            }
            view.subviews.forEach(walk)
        }
        if let window = keyWindow() { walk(window) }
        return best
    }
}
