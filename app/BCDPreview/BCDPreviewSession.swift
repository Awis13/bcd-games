// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

/// The in-game layer on sample values, for the preview app's photographs (tools/ui-preview.py):
///   -BCDTab ingame-menu[-display|-controls|-tools|-binds|-quit|-cloud|-desk]
///   -BCDTab starting[-dock|-installs|-failed|-log|-home]
///   -BCDTab overlay[-desk|-keyboard]
/// A picture of a game stands in for the running game. Only the preview app compiles this file.
struct BCDPreviewSessionScreens: ViewModifier {
    let store: BCDStore
    let focus: BCDFocus

    static func handles(_ tab: String) -> Bool {
        tab.hasPrefix("ingame-menu") || tab.hasPrefix("starting") || tab.hasPrefix("overlay")
    }

    func body(content: Content) -> some View {
        let tab = UserDefaults.standard.string(forKey: "BCDTab") ?? "home"
        if Self.handles(tab) {
            // The shell stays (hidden) so the preview app's start-up runs as for every screen.
            content.hidden().overlay { BCDPreviewSessionStage(spec: tab, store: store, focus: focus).ignoresSafeArea() }
        } else {
            content
        }
    }
}

private struct BCDPreviewSessionStage: View {
    let spec: String
    let store: BCDStore
    let focus: BCDFocus
    @StateObject private var hud = BCDSessionHUDModel()
    @State private var actions = BCDPreviewHUDActions()
    @StateObject private var keyboard = BCDKeyboardBarModel()

    private var game: BCDGame {
        store.games.first { $0.id == "367520" } ?? BCDGame(id: "sample", title: "Sample Game", store: .local, state: .ready)
    }

    var body: some View {
        ZStack {
            Color.black
            if !spec.hasPrefix("starting") {
                // The running game.
                BCDGameArt(game: game, kind: .hero, showTitle: false)
            }
            if spec == "overlay-desk" {
                deskOverlays
            } else {
                BCDSessionLayer(hud: hud, actions: actions)
            }
            if spec == "overlay-keyboard" {
                VStack {
                    Spacer()
                    BCDKeyboardBar(model: keyboard, press: { _ in }, done: {})
                    Rectangle().fill(Color(white: 0.17)).frame(height: 180)
                        .overlay(Text(verbatim: "iOS keyboard").font(.caption).foregroundStyle(.secondary))
                }
                .ignoresSafeArea()
            }
        }
        .onAppear(perform: setUp)
    }

    private var deskOverlays: some View {
        GeometryReader { geo in
            let feed = BCDPerformanceFeed(BCDPreviewSessionSamples.deskReading, tapChip: { _ in })
            let insets = BCDSessionInsets.of(geo)
            ZStack(alignment: .topLeading) {
                BCDPerformanceOverlay(feed: feed, maxWidth: 420).padding(.leading, insets.leading + 12)
                    .padding(.top, insets.top + 12)
                BCDPerformanceOverlay(feed: feed, maxWidth: 140, vertical: true)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                    .padding(.trailing, insets.trailing + 12).padding(.top, insets.top + 60)
            }
        }
    }

    private func setUp() {
        if spec.hasPrefix("starting") {
            var info = BCDStartingInfo(game: game)
            info.tip = "The game menu: Back + Start on a controller, or the menu button on the screen."
            info.seconds = 42
            switch spec {
            case "starting-dock":
                let read = BCDDockStart.read(["probe-start-bits": "1", "session-native-token-submitted": "1",
                                              "session-authenticated-online": "1", "session-requested-app-listed": "1",
                                              "launch-session-wait": "1", "launch-client-error": "35"],
                                             installers: false, installerProgress: nil, installsFinished: false, waited: 40)
                info.step = read.step; info.steps = BCDDockStart.steps; info.notes = read.notes
                info.canShowDesktop = true
            case "starting-installs":
                let read = BCDDockStart.read([:], installers: true, installerProgress: "Installing the Visual C++ runtime (2 of 3)…",
                                             installsFinished: false, waited: 12)
                info.step = read.step; info.steps = BCDDockStart.steps; info.notes = read.notes
                info.notes.append(BCDStartNote(id: "installs-note", text: "These run once for this game. The next start skips them."))
                info.canShowDesktop = true
            case "starting-failed":
                info.step = .stopped; info.steps = BCDDockStart.steps
                info.failure = "Steam couldn't start the game (error 8). Look in the diagnostic log for why."
                info.canClose = true
            case "starting-log":
                info.step = .loading
                info.notes = [BCDStartNote(id: "slow", text: "This is taking longer than usual. A game's first start can take a few minutes.", tone: .warm)]
                info.canShowGame = true
                info.logShown = true
                info.logLines = BCDPreviewSessionSamples.log
            case "starting-home":
                info.canSkip = true
                info.tip = "The game menu: the ≡ button beside the game."
            default:
                info.step = .loading
            }
            hud.starting = BCDStartingFeed(info)
            return
        }
        hud.overlay = BCDPerformanceFeed(BCDPreviewSessionSamples.libraryReading)
        if spec == "overlay-keyboard" { keyboard.latched = ["ctrl"] }
        if spec.hasPrefix("ingame-menu") {
            hud.overlay = nil
            let desk = spec == "ingame-menu-desk"
            let source = BCDPreviewMenuSource(title: game.title, desk: desk)
            let parts = spec.split(separator: "-").map(String.init)
            let menu = BCDSessionMenuModel(source: source, tab: BCDSessionTab(rawValue: parts.count > 2 ? parts[2] : "") ?? .game)
            switch spec {
            case "ingame-menu-binds": menu.initialTab = .controls; menu.initialPage = "binds"
            case "ingame-menu-quit": menu.initialQuestion = "quit"
            case "ingame-menu-cloud": source.cloud = "conflict"
            default: break
            }
            source.menu = menu
            menu.custom["touch"] = { AnyView(BCDPreviewTouchSection()) }
            hud.menu = menu
            hud.message = ""
        }
        actions.hud = hud
    }
}

@MainActor private final class BCDPreviewHUDActions: BCDSessionHUDActions {
    weak var hud: BCDSessionHUDModel?
    func openMenu() {
        guard let hud else { return }
        let source = BCDPreviewMenuSource(title: "Hollow Knight", desk: false)
        let menu = BCDSessionMenuModel(source: source)
        source.menu = menu
        source.onClose = { [weak hud] in hud?.menu = nil }
        menu.custom["touch"] = { AnyView(BCDPreviewTouchSection()) }
        hud.menu = menu
    }
    func closeMenu() { hud?.menu = nil }
    func menuButtonFrame(_ frame: CGRect) {}
    func overlayFrame(_ frame: CGRect) {}
    func starting(_ action: BCDStartingAction) { print("[preview] starting \(action)") }
}

enum BCDPreviewSessionSamples {
    static let libraryReading = BCDPerformanceReading(readings: [
        BCDReading(id: "fps", text: "58 FPS", tone: .good), BCDReading(id: "ms", text: "17.2 ms"),
        BCDReading(id: "cpu", text: "CPU 41% (top 96%)"), BCDReading(id: "gpu", text: "GPU 63% (10.8 ms)"),
        BCDReading(id: "ram", text: "2310 MB"), BCDReading(id: "battery", text: "82%"), BCDReading(id: "heat", text: "Warm", tone: .warm),
    ])
    static let deskReading = BCDPerformanceReading(readings: [
        BCDReading(id: "fps", text: "59.8 FPS", tone: .good), BCDReading(id: "mem", text: "2310/6144 MB", tone: .good),
        BCDReading(id: "present", text: "#14822", tone: .soft), BCDReading(id: "heat", text: "Cool", tone: .good),
    ], chips: [
        BCDOverlayChip(id: "limit", text: "60"), BCDOverlayChip(id: "eco", text: "ECO", tone: .good, on: true),
        BCDOverlayChip(id: "sync", text: "F1"), BCDOverlayChip(id: "capture", text: "CAP", tone: .soft),
    ])
    static let log: [BCDLogLine] = [
        "[bcd] game switches: avx", "wine: starting C:\\Program Files\\Game\\game.exe", "[pool] 1024 MB JIT pool at 0x300000000",
        "[dxmt] device: Apple GPU, feature level 11_1", "[win-pos] 1280x720 at 0,0", "err:module:import_dll Library steam_api64.dll not found",
        "[present] count=0 fps=0.0 mem=1840/6144MB thermal=OK",
    ].enumerated().map { BCDLogLine(id: $0.offset, text: $0.element, error: $0.element.hasPrefix("err")) }
}

/// The menu on sample values: what a library session offers (`desk`: a developer session).
@MainActor private final class BCDPreviewMenuSource: BCDSessionMenuSource {
    weak var menu: BCDSessionMenuModel?
    var onClose: (() -> Void)?
    let title: String
    let desk: Bool
    var cloud: String?
    private var fps = "1", scale = "fit", overlay = true, fields: Set<String> = ["FPS", "Frame time", "RAM", "Battery"]
    private var eco = false, padMode = "keys", padAPI = "xinput"
    private var pointer = "absolute", speed = 2.0, sync = "1"

    init(title: String, desk: Bool) {
        self.title = title
        self.desk = desk
    }

    var subtitle: String { "Playing · 42 min" }
    var tabs: [BCDSessionTab] { BCDSessionTab.allCases }

    func sections(_ tab: BCDSessionTab) -> [BCDMenuSection] {
        switch tab {
        case .game:
            var out = [BCDMenuSection(id: "resume", rows: [BCDMenuRow(id: "resume", title: "Resume", symbol: "play.fill", kind: .action(primary: true))]),
                       BCDMenuSection(id: "quick", rows: [
                        BCDMenuRow(id: "keyboard", title: "Keyboard", detail: "Type into the game; Esc, Ctrl, Alt and the arrows are above the keys.", symbol: "keyboard", kind: .action()),
                       ])]
            if !desk {
                var rows = [BCDMenuRow(id: "cloud.upload", title: "Upload saves and close BCD",
                                       detail: "Save in the game first. BCD sends this game's saves to Steam Cloud, then closes. Steam in this session signs out as the upload starts.",
                                       symbol: "icloud.and.arrow.up", kind: .action())]
                if cloud == "conflict" {
                    rows.append(BCDMenuRow(id: "cloud.status", title: "Nothing was uploaded: 2 saves also changed in Steam Cloud, on another device or without a sync record here. Uploading would replace the cloud's copies.",
                                           symbol: "exclamationmark.triangle.fill", kind: .note(.warm)))
                    rows.append(BCDMenuRow(id: "cloud.replace", title: "Replace the cloud's saves and close", symbol: "icloud.and.arrow.up",
                                           kind: .action(destructive: true),
                                           confirm: BCDConfirm(title: "Replace Steam Cloud's saves with this device's?", message: "",
                                                               answers: [.init(id: "replace", title: "Replace and close", destructive: true)])))
                }
                out.append(BCDMenuSection(id: "cloud", title: "Steam Cloud", rows: rows))
                out.append(BCDMenuSection(id: "quit", rows: [
                    BCDMenuRow(id: "quit", title: "Quit game", detail: "Progress you haven't saved is lost.", symbol: "stop.circle",
                               kind: .action(destructive: true),
                               confirm: BCDConfirm(title: "Quit the game?",
                                                   message: "BCD asks the game to close, as Alt+F4 does. What you haven't saved is lost, and the game may ask you to confirm.",
                                                   answers: [.init(id: "quit", title: "Quit game", destructive: true)])),
                ]))
            }
            return out
        case .display:
            var out = [BCDMenuSection(id: "fps", title: "Frame rate", rows: [
                BCDMenuRow(id: "fps", title: "Frame-rate limit", detail: "60 frames a second.",
                           kind: .choice([.init(id: "3", title: "30"), .init(id: "4", title: "40"), .init(id: "1", title: "60"),
                                          .init(id: "0", title: "Screen max"), .init(id: "2", title: "No limit")], selected: fps)),
            ])]
            out.append(BCDMenuSection(id: "scale", title: "Picture", rows: [
                BCDMenuRow(id: "scale", title: "Aspect & scaling", detail: "The whole picture, as large as fits, with bars where the shapes differ.",
                           kind: .choice([.init(id: "fit", title: "Fit"), .init(id: "fill", title: "Fill"), .init(id: "stretch", title: "Stretch"),
                                          .init(id: "aspect", title: "Game's shape")], selected: scale)),
            ]))
            var rows = [BCDMenuRow(id: "overlay", title: "Performance overlay", detail: "Drag it where you want it.", kind: .toggle(overlay))]
            if overlay && !desk {
                rows.append(BCDMenuRow(id: "overlay.fields", title: "Shows", kind: .multi(
                    [("FPS", "FPS"), ("Frame time", "Frame time"), ("CPU", "CPU"), ("GPU", "GPU"), ("RAM", "Memory"), ("Battery", "Battery"),
                     ("Thermal", "Heat")].map { BCDChoice(id: $0.0, title: $0.1) }, selected: fields)))
            }
            out.append(BCDMenuSection(id: "overlay", title: "Overlay", rows: rows))
            out.append(BCDMenuSection(id: "eco", title: "Battery & heat", rows: [
                BCDMenuRow(id: "eco", title: "Eco mode",
                           detail: "The game's threads run on the efficiency cores: the phone stays cooler, the game runs slower. Handy while a game loads; off again for play.",
                           kind: .toggle(eco)),
            ]))
            return out
        case .controls:
            var out = [BCDMenuSection(id: "touch", rows: [
                BCDMenuRow(id: "touch", title: "Touch controls", kind: .custom("touch")),
            ])]
            if !desk {
                out.append(BCDMenuSection(id: "controller", title: "Controller", rows: [
                    BCDMenuRow(id: "pad.mode", title: "The controller reaches the game as", kind: .choice([
                        .init(id: "", title: "Its own support"), .init(id: "dinput", title: "XInput + DirectInput"), .init(id: "keys", title: "Keyboard & mouse"),
                    ], selected: padMode)),
                    BCDMenuRow(id: "binds", title: "Controller binds", symbol: "gamecontroller", kind: .page("binds", value: "2 changed")),
                    BCDMenuRow(id: "pad.api", title: "Controller API", kind: .choice([.init(id: "xinput", title: "XInput"), .init(id: "hid", title: "DirectInput / HID")], selected: padAPI)),
                    BCDMenuRow(id: "pad.api.status", title: padAPI == "xinput" ? "Live in this session" : "Needs a restart: quit BCD, then start this game again",
                               symbol: padAPI == "xinput" ? "checkmark.circle" : "arrow.clockwise.circle", kind: .note(padAPI == "xinput" ? .good : .warm)),
                ]))
            }
            out.append(BCDMenuSection(id: "pointer", title: "Mouse & pointer", rows: [
                BCDMenuRow(id: "pointer", title: "A finger on the game", detail: "Drag to move the pointer, as on a trackpad. A tap clicks.",
                           kind: .choice([.init(id: "absolute", title: "Trackpad"), .init(id: "touch", title: "Tap to click"),
                                          .init(id: "relative", title: "Mouse-look")], selected: pointer)),
                BCDMenuRow(id: "speed", title: "Touch speed", kind: .slider(speed, 0.1...8, step: 0.1, text: String(format: "%.1f×", speed))),
            ]))
            return out
        case .tools:
            return [
                BCDMenuSection(id: "diagnostics", title: "Diagnostics", rows: [
                    BCDMenuRow(id: "capture", title: "Capture the next frame", detail: "Each render pass of the next frame goes to Documents/capture, its draw list to the log.",
                               symbol: "camera.viewfinder", kind: .action()),
                    BCDMenuRow(id: "gpu.sync", title: "GPU sync", detail: "F1, the default: each pass waits for the one before.",
                               kind: .choice([.init(id: "1", title: "F1"), .init(id: "6", title: "F6"), .init(id: "5", title: "F5"), .init(id: "0", title: "F0")], selected: sync)),
                ]),
                BCDMenuSection(id: "log", title: "Log", rows: [
                    BCDMenuRow(id: "log", title: "Live log", symbol: "text.alignleft", kind: .page("log", value: nil)),
                    BCDMenuRow(id: "share", title: "Share logs", kind: .share([])),
                ]),
            ]
        }
    }

    func page(_ id: String) -> BCDMenuPage? {
        switch id {
        case "binds":
            let groups: [(String, [(String, String)])] = [("Sticks", [("Left stick", "WASD"), ("Right stick", "The mouse")]),
                                                          ("Face buttons", [("A", "Space"), ("B", "Ctrl"), ("X", "E"), ("Y", "R")]),
                                                          ("Bumpers & triggers", [("LB", "Q"), ("RB", "F"), ("LT", "Right click"), ("RT", "Left click")])]
            var sections = groups.map { group in
                BCDMenuSection(id: group.0, title: group.0, rows: group.1.map {
                    BCDMenuRow(id: "bind." + $0.0, title: $0.0, detail: $0.0 == "X" ? "Changed" : nil, kind: .page("bind." + $0.0, value: $0.1))
                })
            }
            sections.append(BCDMenuSection(id: "mouse", title: "Right stick as mouse", rows: [
                BCDMenuRow(id: "bind.vertical", title: "Up-and-down speed", kind: .slider(0.8, 0.25...1.5, step: 0.05, text: "80%")),
            ]))
            return BCDMenuPage(id: "binds", title: "Controller binds", sections: sections)
        case "log":
            return BCDMenuPage(id: "log", title: "Live log", kind: .log)
        default:
            return BCDMenuPage(id: id, title: String(id.dropFirst(5)), sections: [
                BCDMenuSection(id: "mouse", title: "Mouse", rows: [BCDMenuRow(id: "left", title: "Left click", kind: .pick(selected: false)),
                                                                  BCDMenuRow(id: "right", title: "Right click", kind: .pick(selected: false))]),
                BCDMenuSection(id: "letters", title: "Letters", rows: (0x41...0x46).map {
                    BCDMenuRow(id: "key.\($0)", title: String(UnicodeScalar(UInt8($0))), kind: .pick(selected: $0 == 0x45))
                }),
            ])
        }
    }

    func logLines() -> [BCDLogLine] { BCDPreviewSessionSamples.log }
    func act(_ row: String) { if row == "resume" { close() } }
    func toggle(_ row: String, _ on: Bool) {
        switch row {
        case "overlay": overlay = on
        case "eco": eco = on
        default: break
        }
    }
    func choose(_ row: String, _ value: String) {
        switch row {
        case "fps": fps = value
        case "scale": scale = value
        case "overlay.fields": if fields.contains(value) { fields.remove(value) } else { fields.insert(value) }
        case "pad.mode": padMode = value
        case "pad.api": padAPI = value
        case "pointer": pointer = value
        case "gpu.sync": sync = value
        default: break
        }
    }
    func slide(_ row: String, _ value: Double) {
        switch row {
        case "speed": speed = value
        default: break
        }
    }
    func answer(_ row: String, _ answer: String) { print("[preview] \(row): \(answer)") }
    func pageShown(_ id: String?) {}
    func close() { onClose?() }
}

/// The touch controls' part of the menu as ui-touch draws it (BCDTouchMenuSection), on its sample.
private struct BCDPreviewTouchSection: View {
    @StateObject private var touch = BCDTouchPreviewModel()
    var body: some View {
        BCDTouchMenuSection(state: touch.menuState, actions: touch, opened: {})
    }
}
