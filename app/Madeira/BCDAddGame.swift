// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI

// Add a game (docs/BCD-UI-ARCHITECTURE.md): the folders of BCD's drive_c, where the Files app
// puts a game's folder; the game's program, chosen there and read before it is added; its name;
// then the game's own page, where Find on Steam and a cover picture give it its art. Also "Add
// every game in drive_c". Full screen over the shell, from the "+" of Home and Library.
//
// The folders and the library come through BCDAddGameBackend: BCDAddGameLive in the app (the
// only file of this screen that touches the engine), BCDAddGameSample in the preview app.

// MARK: - What the screen works with (no views)

/// A folder of drive_c, or a program in it.
struct BCDDriveItem: Identifiable, Equatable {
    var name: String
    /// Inside drive_c, folders joined by "/" ("Games/Warcraft III/war3.exe").
    var path: String
    var folder: Bool
    var id: String { path }
    /// A batch file, which starts its program through cmd.exe.
    var batch: Bool { !folder && ["bat", "cmd"].contains((name as NSString).pathExtension.lowercased()) }
}

/// A program as its header says, before it is added.
struct BCDProgramInfo: Equatable {
    /// The name the library would give it.
    var title: String
    /// "C:\Games\Warcraft III\war3.exe".
    var windowsPath: String
    /// 32 or 64; 0 for a batch file.
    var bits: Int
    /// "D3D9", "D3D11 / D3D12", "OpenGL"…; nil when it names none.
    var api: String?

    /// "32-bit · Direct3D 9", "A batch file".
    var summary: String {
        guard bits != 0 else { return "A batch file, started through cmd.exe" }
        var parts = ["\(bits)-bit"]
        if let api, !api.isEmpty { parts.append(api.replacingOccurrences(of: "D3D", with: "Direct3D ")) }
        return parts.joined(separator: " · ")
    }
}

/// What Add a game needs from the engine.
@MainActor protocol BCDAddGameBackend: AnyObject {
    /// A folder's subfolders and programs (.exe, .bat, .cmd), folders first, by name. "" is drive_c.
    func list(_ folder: String) throws -> [BCDDriveItem]
    /// The program's header, read; throws with the reason it cannot be a game here.
    func inspect(_ path: String) throws -> BCDProgramInfo
    /// Adds the program with this name (or finds it already added); the answer is its id in the store.
    func add(_ path: String, title: String) throws -> String
    /// Adds every game in drive_c the library lacks; the answer is how many.
    func addEveryGame() async -> Int
}

/// Where the screen finds its backend: the app's (BCDAddGameLive, found by its Objective-C name,
/// as this file cannot name it), or the preview app's sample.
@MainActor enum BCDAddGameSource {
    static var backend: BCDAddGameBackend? =
        (NSClassFromString("BCDAddGameLive") as? NSObject.Type)?.init() as? BCDAddGameBackend
}

/// The folder a path is in ("" for drive_c), and how the path reads ("drive_c › Games").
enum BCDDrivePath {
    static func parent(_ path: String) -> String {
        guard let cut = path.range(of: "/", options: .backwards) else { return "" }
        return String(path[..<cut.lowerBound])
    }
    static func trail(_ path: String) -> String {
        (["drive_c"] + path.split(separator: "/").map(String.init)).joined(separator: " › ")
    }
}

// MARK: - The screen

struct BCDAddGame: View {
    @EnvironmentObject private var store: BCDStore
    @EnvironmentObject private var focus: BCDFocus
    @Environment(\.horizontalSizeClass) private var horizontal
    @Environment(\.verticalSizeClass) private var vertical
    let close: () -> Void
    /// The game was added: its id in the store, once the store lists it.
    let added: (String) -> Void
    private let backend: BCDAddGameBackend?

    /// The folder shown ("" is drive_c), what is in it, and why it could not be read.
    @State private var folder: String
    @State private var items: [BCDDriveItem] = []
    @State private var problem: String?
    /// The program chosen, read, and the name it will have.
    @State private var picked: (path: String, info: BCDProgramInfo)?
    @State private var name = ""
    @State private var everyGame: String?
    @State private var busy = false
    /// Added: waiting for the store to list it.
    @State private var waiting: String?

    init(folder: String = "", pick: String? = nil, backend: BCDAddGameBackend? = nil,
         close: @escaping () -> Void, added: @escaping (String) -> Void) {
        self.close = close
        self.added = added
        let backend = backend ?? BCDAddGameSource.backend
        self.backend = backend
        _folder = State(initialValue: folder)
        if let pick, let info = try? backend?.inspect(pick) {
            _picked = State(initialValue: (pick, info))
            _name = State(initialValue: info.title)
        }
    }

    static let layer = "add."

    private var layout: BCDLayout { BCDLayout.of(horizontal: horizontal, vertical: vertical) }

    var body: some View {
        VStack(spacing: 0) {
            header
            ScrollViewReader { scroller in
                ScrollView(.vertical, showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 8) {
                        if let picked { confirm(picked.path, picked.info) } else { listing }
                    }
                    .frame(maxWidth: 640, alignment: .leading)
                    .frame(maxWidth: .infinity)
                    .padding(.horizontal, layout.gutter).padding(.top, 4).padding(.bottom, 60)
                }
                .scrollDismissesKeyboard(.interactively)
                .onChange(of: focus.focused) { _, id in
                    guard let id, id.hasPrefix(Self.layer) else { return }
                    withAnimation(.easeOut(duration: 0.2)) { scroller.scrollTo(id, anchor: .center) }
                }
            }
            if focus.controllerActive && !focus.hints.isEmpty {
                HStack(spacing: 22) { Spacer(); ForEach(focus.hints) { BCDHintView(hint: $0) } }
                    .frame(height: 40).padding(.horizontal, layout.gutter)
                    .allowsHitTesting(false)
            }
        }
        .background { BCDBackdrop().ignoresSafeArea() }
        .coordinateSpace(name: BCDFocus.space)
        .environment(\.bcdLayout, layout)
        .foregroundStyle(BCDStyle.text)
        .preferredColorScheme(.dark)
        .onAppear {
            focus.openLayer(Self.layer)
            load(folder)
            installButtons()
        }
        .onDisappear { focus.closeLayer(Self.layer) }
        .onChange(of: picked?.path) { _, _ in installButtons() }
        .onChange(of: folder) { _, _ in installButtons() }
        // Added: the game's page opens once the store lists the game.
        .onChange(of: store.games.map(\.id)) { _, ids in
            if let waiting, ids.contains(waiting) { finish(waiting) }
        }
    }

    // MARK: Header

    private var header: some View {
        HStack(spacing: 12) {
            if picked != nil || !folder.isEmpty {
                Button(action: back) {
                    Image(systemName: "chevron.left").font(.system(size: 16, weight: .bold))
                        .frame(width: 40, height: 40).background(BCDStyle.surfaceRaised, in: Circle())
                }
                .buttonStyle(.plain)
                .bcdFocusable("add.back", cornerRadius: 20, action: back)
                .accessibilityLabel("Back")
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(picked == nil ? "Add a game" : "Add this game").font(BCDStyle.heading(22)).lineLimit(1)
                Text(picked == nil ? BCDDrivePath.trail(folder) : "Check its name, then add it")
                    .font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft).lineLimit(1).truncationMode(.head)
            }
            Spacer(minLength: 8)
            Button(action: close) {
                Image(systemName: "xmark").font(.system(size: 14, weight: .bold))
                    .frame(width: 40, height: 40).background(BCDStyle.surfaceRaised, in: Circle())
            }
            .buttonStyle(.plain)
            .bcdFocusable("add.close", cornerRadius: 20, action: close)
            .accessibilityLabel("Close")
        }
        .padding(.horizontal, layout.gutter).padding(.top, 12).padding(.bottom, 10)
        .frame(maxWidth: 640 + 2 * layout.gutter)
        .frame(maxWidth: .infinity)
    }

    // MARK: The folder

    @ViewBuilder private var listing: some View {
        if folder.isEmpty {
            Text("Copy a game's folder into BCD › wine › drive_c with the Files app, then choose its program here: the .exe that starts the game.")
                .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4).padding(.bottom, 4)
            row(id: "add.every", symbol: "square.stack.3d.down.right", title: busy ? "Adding…" : "Add every game in drive_c",
                detail: everyGame ?? "Finds the games in drive_c and adds those your library lacks, each with its main program.",
                enabled: !busy, run: addEveryGame)
            BCDEyebrow(text: "drive_c").padding(.top, 10).padding(.leading, 4)
        }
        if let problem {
            Label(problem, systemImage: "exclamationmark.triangle.fill")
                .font(BCDStyle.body(14)).foregroundStyle(BCDGameColors.amber)
                .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
        } else if items.isEmpty {
            Text("No folders or programs here. Use the Files app to copy games into BCD › wine › drive_c.")
                .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4).padding(.top, 4)
        }
        ForEach(items) { item in
            row(id: "add.item." + item.path, symbol: item.folder ? "folder.fill" : (item.batch ? "terminal" : "app.dashed"),
                title: item.name, detail: nil, chevron: item.folder) { open(item) }
        }
    }

    private func row(id: String, symbol: String, title: String, detail: String?, chevron: Bool = false,
                     enabled: Bool = true, run: @escaping () -> Void) -> some View {
        Button(action: run) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 12) {
                    Image(systemName: symbol).font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(BCDStyle.lavender).frame(width: 26)
                    Text(title).font(BCDStyle.label(15)).lineLimit(2)
                    Spacer(minLength: 8)
                    if chevron {
                        Image(systemName: "chevron.right").font(.system(size: 12, weight: .bold))
                            .foregroundStyle(BCDStyle.textSoft.opacity(0.7))
                    }
                }
                if let detail {
                    Text(detail).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                        .fixedSize(horizontal: false, vertical: true).padding(.leading, 38)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 11)
            .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
            .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.55)
        .bcdFocusable(id, cornerRadius: 14) { if enabled { run() } }
        .id(id)
    }

    // MARK: The program chosen

    private func confirm(_ path: String, _ info: BCDProgramInfo) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                Text(info.windowsPath).font(.system(size: 14, design: .monospaced)).foregroundStyle(BCDStyle.text)
                    .fixedSize(horizontal: false, vertical: true)
                Text(info.summary).font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            VStack(alignment: .leading, spacing: 6) {
                BCDEyebrow(text: "Name").padding(.leading, 4)
                TextField(info.title, text: $name)
                    .font(BCDStyle.body(16))
                    .submitLabel(.done)
                    .onSubmit(add)
                    .padding(.horizontal, 14).frame(height: 48)
                    .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
            }
            if let problem {
                Label(problem, systemImage: "exclamationmark.triangle.fill")
                    .font(BCDStyle.body(14)).foregroundStyle(BCDGameColors.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("Its page opens next: there, Find on Steam or a cover picture gives it its art, and Options holds its settings.")
                .font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 12) {
                Button("Back", action: back)
                    .buttonStyle(BCDOutlineButtonStyle())
                    .bcdFocusable("add.cancel", cornerRadius: 25, action: back)
                Button(action: add) { Label(waiting == nil ? "Add to library" : "Adding…", systemImage: "plus") }
                    .buttonStyle(BCDPrimaryButtonStyle())
                    .disabled(waiting != nil)
                    .bcdFocusable("add.confirm", cornerRadius: 27, action: add)
                    .id("add.confirm")
            }
        }
    }

    // MARK: What it does

    private func installButtons() {
        focus.setHandlers([
            (BCDHint(button: .a, title: "Select"), {}),
            (BCDHint(button: .b, title: picked != nil || !folder.isEmpty ? "Back" : "Close"), { back() }),
        ])
    }

    private func load(_ path: String) {
        guard let backend else { problem = "Adding a game isn't available here."; return }
        do {
            items = try backend.list(path)
            problem = nil
        } catch {
            items = []
            problem = error.localizedDescription
        }
        folder = path
        focus.focused = items.first.map { "add.item." + $0.path } ?? (path.isEmpty ? "add.every" : "add.back")
    }

    private func open(_ item: BCDDriveItem) {
        if item.folder {
            withAnimation(.easeOut(duration: 0.18)) { load(item.path) }
            return
        }
        guard let backend else { return }
        do {
            let info = try backend.inspect(item.path)
            problem = nil
            name = info.title
            withAnimation(.easeOut(duration: 0.18)) { picked = (item.path, info) }
            focus.focused = "add.confirm"
        } catch {
            problem = error.localizedDescription
        }
    }

    /// B and the back arrow: from the program to its folder, from a folder to the one above it,
    /// from drive_c out.
    private func back() {
        if let picked {
            withAnimation(.easeOut(duration: 0.18)) { self.picked = nil }
            problem = nil
            focus.focused = "add.item." + picked.path
        } else if !folder.isEmpty {
            let left = folder
            withAnimation(.easeOut(duration: 0.18)) { load(BCDDrivePath.parent(folder)) }
            focus.focused = "add.item." + left
        } else {
            close()
        }
    }

    private func add() {
        guard let picked, let backend, waiting == nil else { return }
        let title = name.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            let id = try backend.add(picked.path, title: title.isEmpty ? picked.info.title : title)
            waiting = id
            if store.games.contains(where: { $0.id == id }) { finish(id) }
        } catch {
            problem = error.localizedDescription
        }
    }

    private func finish(_ id: String) {
        guard waiting == id else { return }
        waiting = nil
        added(id)
    }

    private func addEveryGame() {
        guard let backend, !busy else { return }
        busy = true
        everyGame = "Looking through drive_c…"
        Task {
            let count = await backend.addEveryGame()
            busy = false
            everyGame = count == 0 ? "Nothing new: every game found in drive_c is in your library."
                : "Added \(count) game\(count == 1 ? "" : "s") to your library."
            load(folder)
        }
    }
}
