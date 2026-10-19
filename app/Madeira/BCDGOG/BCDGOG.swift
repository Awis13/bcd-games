// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

/// GOG in BCD, milestone 1 (docs/GOG.md; docs/research/open-fork-2026-10-18/gog.md §5 M1): sign-in
/// in GOG's own page, the owned Windows games with their pictures, the newest build downloaded
/// and installed into C:\GOG Games\<game> by the shared content engine (BCDContentQueue), the
/// build's play task as an ordinary library entry (Play is the library's start of a game added
/// from drive_c: no client in Wine), and uninstall. Engine-bound: BCD's interface reaches it only
/// through BCDLiveStore (BCDLiveStoreGOG.swift).
///
/// - `env.BCD_GOG = 0` hides everything GOG (installed GOG games stay as ordinary library games).
/// - `env.BCD_GOG_SFC = 1` cuts small files from their depot's small-files container (one fetch
///   instead of one per file); off until a device run shows the container downloads.
/// - Not in M1: updates, verify and repair, DLC, a language picker, redistributables and GOG's
///   support setups (a build's redistributables are listed on its record), the Galaxy stand-in.
/// Log tags: [gog-signin], [gog-library], [gog-content], [gog-install] (product IDs, counts,
/// status: never a token, code, secret, account name or path).
@MainActor final class BCDGOG: ObservableObject, BCDContentDriver {
    static let shared = BCDGOG()

    /// GOG in BCD (on by default; nothing is fetched before a sign-in). 0 hides it everywhere.
    static var enabled: Bool { MadeiraConfig.flag("BCD_GOG", fallback: true) }
    /// Small files from their depot's container (one download for hundreds of files). Off by default.
    static var containers: Bool { MadeiraConfig.flag("BCD_GOG_SFC", fallback: false) }
    static let root = BCDInstallRoot(drive: "C", folder: "GOG Games")
    static var folder: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("BCD/GOG", isDirectory: true)
    }

    @Published private(set) var signedIn = false
    @Published private(set) var userName: String?
    @Published private(set) var signingIn = false
    @Published private(set) var products: [BCDGOGProduct] = []
    @Published private(set) var installs: [String: BCDGOGInstallRecord] = [:]
    @Published private(set) var refreshing = false
    @Published private(set) var updated: Date?
    @Published var error: String?

    let store = "gog"
    let storeTitle = "GOG"
    private let http = BCDHTTPStream()
    private lazy var session = BCDGOGSession(secrets: BCDKeychain.shared, http: http)
    private lazy var api = BCDGOGAPI(session: session, http: http)
    private var started = false
    private var watchers = Set<AnyCancellable>()
    private var artTask: Task<Void, Never>?
    private var queue: BCDContentQueue { BCDContentQueue.shared }

    private init() {}

    func key(_ id: String) -> BCDContentKey { BCDContentKey(store: store, id: id) }

    /// What BCD's store watches to show GOG's changes.
    var changes: AnyPublisher<Void, Never> {
        Publishers.Merge(objectWillChange.map { _ in () }, queue.$jobs.map { _ in () }).eraseToAnyPublisher()
    }

    /// Once per run, at the first look: the records, the cached list, the queue's wiring. No network
    /// until the list is stale (6 h) and someone is signed in.
    func start() {
        guard Self.enabled, !started else { return }
        started = true
        BCDContentLog.sink = { LogStore.shared.log($0) }
        installs = BCDGOGInstall.loadRecords(Self.folder)
        let queue = self.queue
        queue.register(self)
        queue.sessionRunning = { LibraryModel.shared.current != nil || wine_process_is_running() != 0 || wineserver_is_running() != 0 }
        queue.otherDownloadActive = { SteamOwnedLibrary.shared.hasActiveDownload }
        BCDContentBackground.shared.attach(queue)
        // @Published sends before the value is stored: look on the next turn of the main queue.
        LibraryModel.shared.$current.receive(on: DispatchQueue.main)
            .sink { [weak queue] _ in queue?.reconcile() }.store(in: &watchers)
        SteamOwnedLibrary.shared.$downloads.receive(on: DispatchQueue.main)
            .sink { [weak queue] _ in queue?.otherDownloadChanged() }.store(in: &watchers)
        if let trash = Self.root.stagingURL?.appendingPathComponent("trash", isDirectory: true),
           FileManager.default.fileExists(atPath: trash.path) {
            Task.detached(priority: .utility) { try? FileManager.default.removeItem(at: trash) }
        }
        Task {
            let tokens = await session.current()
            signedIn = tokens != nil
            userName = tokens?.userName
            if let tokens { loadCache(account: BCDGOGCatalog.accountKey(tokens.userID)) }
            BCDContentLog.write("[gog-library] start signed-in=\(signedIn ? 1 : 0) cached=\(products.count) installed=\(installs.count)")
            await refreshLibrary(force: false)
        }
    }

    // MARK: Account

    func signIn() {
        guard Self.enabled, !signingIn else { return }
        start()
        let page = BCDWebSignIn.Page(
            title: "Sign in to GOG", start: BCDGOGAuth.loginURL, match: BCDGOGAuth.code(from:),
            pasteHint: "Sign in to GOG in Safari. When it's done, the page shows nothing or an error: copy the whole address from Safari's address bar and paste it here.")
        BCDWebSignIn.present(page) { [weak self] code in
            guard let self, let code else { return }
            Task { await self.finishSignIn(code) }
        }
    }

    private func finishSignIn(_ code: String) async {
        signingIn = true
        defer { signingIn = false }
        do {
            let tokens = try await session.signIn(code: code)
            signedIn = true
            userName = tokens.userName
            error = nil
            await refreshLibrary(force: true)
        } catch {
            tell("GOG didn't accept the sign-in. Try again, or use Safari from the sign-in page.")
            BCDContentLog.write("[gog-signin] failed reason=\(Self.reason(error))")
        }
    }

    /// GOG has no way to end a sign-in from outside: BCD forgets its tokens (they lapse on their own)
    /// and the list of games. Installed games stay and still start; their downloads pause.
    func signOut() {
        Task { await session.signOut() }
        for job in queue.jobs.values where job.key.store == store { queue.pause(job.key) }
        artTask?.cancel()
        artTask = nil
        signedIn = false
        userName = nil
        products = []
        updated = nil
        try? FileManager.default.removeItem(at: Self.folder.appendingPathComponent("library.json"))
        BCDContentLog.write("[gog-signin] signed out")
    }

    // MARK: Library

    func refreshLibrary(force: Bool) async {
        guard Self.enabled, signedIn, !refreshing, LibraryModel.shared.current == nil else { return }
        if !force, let updated, Date().timeIntervalSince(updated) < 6 * 3600 { return }
        refreshing = true
        defer { refreshing = false }
        do {
            var found: [BCDGOGProduct] = []
            var page = 1, pages = 1
            repeat {
                let reply = try BCDGOGCatalog.parsePage(await api.json(BCDGOGCatalog.pageURL(page), signedIn: true))
                found += reply.products
                pages = reply.pages
                page += 1
            } while page <= pages && page <= 200
            let known = Dictionary(products.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
            var seen = Set<String>()
            products = found.filter { seen.insert($0.id).inserted }.map { product in
                var product = product
                if let old = known[product.id] { product.cover = old.cover; product.background = old.background; product.artChecked = old.artChecked }
                return product
            }.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
            updated = Date()
            error = nil
            await saveCache()
            BCDContentLog.write("[gog-library] refreshed games=\(products.count) pages=\(pages)")
            resolveArt()
        } catch {
            if case BCDGOGError.signInRefused = error { signedIn = false; userName = nil }
            let message = (error as? LocalizedError)?.errorDescription ?? "BCD couldn't read your GOG library. Try again later."
            self.error = message
            if force { tell(message) }   // asked for: say so; a refresh in the background stays quiet
            BCDContentLog.write("[gog-library] refresh failed reason=\(Self.reason(error))")
        }
    }

    /// The tall covers and backdrops from GOG's public game database, one game at a time in the
    /// background, once per game (kept with the list). Stops while a game runs.
    private func resolveArt() {
        guard artTask == nil else { return }
        let missing = products.filter { $0.artChecked == nil }.map(\.id)
        guard !missing.isEmpty else { return }
        artTask = Task { [weak self] in
            var resolved = 0
            for id in missing {
                guard let self, !Task.isCancelled, LibraryModel.shared.current == nil else { break }
                guard let reply = try? await self.api.json(BCDGOGCatalog.gamesDBURL(id), signedIn: false, limit: 4 << 20) else { continue }
                let art = BCDGOGCatalog.parseGamesDB(reply)
                if let i = self.products.firstIndex(where: { $0.id == id }) {
                    self.products[i].cover = art.cover
                    self.products[i].background = art.background
                    self.products[i].artChecked = Date()
                    resolved += 1
                }
            }
            await self?.saveCache()
            self?.artTask = nil
            BCDContentLog.write("[gog-library] art resolved=\(resolved) of=\(missing.count)")
        }
    }

    private func loadCache(account: String) {
        guard let data = try? Data(contentsOf: Self.folder.appendingPathComponent("library.json")),
              let cache = try? JSONDecoder().decode(BCDGOGCatalog.Cache.self, from: data),
              cache.version == 1, cache.account == account else { return }
        products = cache.products
        updated = cache.updated
    }

    private func saveCache() async {
        guard let tokens = await session.current() else { return }
        let cache = BCDGOGCatalog.Cache(account: BCDGOGCatalog.accountKey(tokens.userID), updated: updated ?? Date(), products: products)
        try? FileManager.default.createDirectory(at: Self.folder, withIntermediateDirectories: true)
        try? JSONEncoder().encode(cache).write(to: Self.folder.appendingPathComponent("library.json"), options: .atomic)
    }

    func product(_ id: String) -> BCDGOGProduct? { products.first { $0.id == id } }

    // MARK: Install, play, uninstall

    func install(_ id: String) {
        guard Self.enabled, BCDGOGCatalog.validID(id), installs[id] == nil else { return }
        guard signedIn else { tell(BCDGOGError.signedOut.errorDescription!); return }
        start()
        queue.enqueue(key(id), title: product(id)?.title ?? queue.job(key(id))?.title ?? "GOG game")
    }

    /// The library entry Play starts, made again if the library lost it.
    func playEntry(_ id: String) -> Result<LibraryEntry, BCDGOGPlayRefusal> {
        guard var record = installs[id] else { return .failure(.init(message: "This GOG game isn't installed.")) }
        guard record.play != nil else { return .failure(.init(message: BCDGOGError.noPlayTask.errorDescription!)) }
        if record.entryID.flatMap({ id in LibraryModel.shared.entries.first { $0.id == id } }) == nil {
            makeEntry(&record)
            try? BCDGOGInstall.save(record, base: Self.folder)
            installs[id] = record
        }
        guard let entry = record.entryID.flatMap({ id in LibraryModel.shared.entries.first { $0.id == id } }) else {
            return .failure(.init(message: "BCD couldn't add this game to the library. Its folder is \(record.windowsFolder)."))
        }
        BCDContentLog.write("[gog-install] play product=\(id)")
        return .success(entry)
    }

    /// The game's files go (renamed out of sight at once, deleted in the background), with its
    /// record and library entry. Its saves in the Windows user profile stay.
    func uninstall(_ id: String) {
        if queue.job(key(id)) != nil { queue.cancel(key(id)) }
        guard let record = installs[id] else { return }
        guard LibraryModel.shared.current == nil, wine_process_is_running() == 0 else {
            tell("Close the game before uninstalling it.")
            return
        }
        if let entryID = record.entryID { LibraryModel.shared.remove(entryID) }
        BCDGOGInstall.delete(id, base: Self.folder)
        installs[id] = nil
        if let folder = record.hostFolder, let trash = record.root.stagingURL?.appendingPathComponent("trash", isDirectory: true) {
            Task { await BCDContentEngine.remove(folder, trash: trash) }
        }
        BCDContentLog.write("[gog-install] uninstalled product=\(id)")
    }

    /// The record's play task as a library entry: an existing entry keeps its settings.
    private func makeEntry(_ record: inout BCDGOGInstallRecord) {
        guard let play = record.play, let folder = record.hostFolder else { return }
        let launch = BCDGOGInstall.launch(record, play: play, program: play.path, working: play.workingDirectory)
        var entry = record.entryID.flatMap { id in LibraryModel.shared.entries.first { $0.id == id } }
            ?? (try? LibraryModel.inspect(folder.appendingPathComponent(play.path)))
            ?? LibraryEntry(title: record.title, relativePath: launch.relativePath, bits: 0)
        entry.title = record.title
        entry.relativePath = launch.relativePath
        entry.arguments = launch.arguments
        entry.workingDirectory = launch.workingDirectory
        if entry.launchMode == nil { entry.launchMode = launch.launchMode }
        entry.folderBytes = record.bytes
        LibraryModel.shared.save(entry)
        record.entryID = entry.id
    }

    /// Something the player asked for went wrong: BCD's interface shows the library's message.
    private func tell(_ message: String) { LibraryModel.shared.error = message }

    /// What an installed game's build names that BCD doesn't install yet (M2): its
    /// redistributables, in words, for the game's page.
    func note(_ id: String) -> String? {
        guard let record = installs[id], !record.dependencies.isEmpty else { return nil }
        let names = record.dependencies.prefix(6).map(Self.readable)
        return "May need: " + names.joined(separator: ", ") + ". BCD doesn't install these from GOG yet."
    }

    static func readable(_ dependency: String) -> String {
        var name = dependency
        let wide = name.hasSuffix("_x64")
        if wide { name.removeLast(4) }
        if name.hasPrefix("MSVC"), let year = Int(name.dropFirst(4)) { name = "Visual C++ \(year)" }
        else if name == "DirectX" { name = "DirectX (June 2010)" }
        else if name.lowercased().hasPrefix("dotnet") { name = ".NET " + String(name.dropFirst(6)) }
        return wide ? name + " (64-bit)" : name
    }

    static func reason(_ error: Error) -> String {
        if let error = error as? BCDGOGError { return error.logReason }
        return BCDContentQueue.reason(error)
    }

    // MARK: BCDContentDriver

    func blocker(_ key: BCDContentKey) -> String? {
        if !signedIn { return BCDGOGError.signedOut.errorDescription }
        if installs[key.id] != nil { return "This game is already installed." }
        return nil
    }

    func plan(for key: BCDContentKey, stored: BCDContentPlan?) async throws -> BCDContentPlan {
        let api = self.api
        let fetch: BCDGOGContent.Fetch = { url, signedIn in try await api.json(url, signedIn: signedIn, limit: 64 << 20) }
        let build = try await BCDGOGContent.newest(key.id, fetch: fetch)
        if let stored, stored.version == build.id, pending(key.id) != nil { return stored }
        let title = product(key.id)?.title ?? queue.job(key)?.title ?? "GOG game"
        let made = try await BCDGOGContent.plan(key, title: title, build: build, preferred: Locale.preferredLanguages,
                                                containers: Self.containers, fetch: fetch)
        try savePending(made.info)
        return made.plan
    }

    func source(for plan: BCDContentPlan) async throws -> BCDChunkSource {
        let api = self.api
        return BCDGOGChunkSource(fetch: { product in
            try await api.json(BCDGOGContent.secureLinkURL(product), signedIn: true, limit: 1 << 20)
        })
    }

    func root(for key: BCDContentKey) -> BCDInstallRoot { Self.root }

    func options(for plan: BCDContentPlan) -> BCDContentOptions { BCDContentOptions() }

    func finish(_ plan: BCDContentPlan, stage: URL) async throws {
        guard let info = pending(plan.key.id) else { throw BCDGOGError.badReply("pending") }
        let root = Self.root
        var record = try await Task.detached(priority: .utility) {
            try BCDGOGInstall.finish(plan, info: info, stage: stage, root: root)
        }.value
        makeEntry(&record)
        try BCDGOGInstall.save(record, base: Self.folder)
        installs[record.productID] = record
        try? FileManager.default.removeItem(at: pendingURL(plan.key.id))
    }

    func cancelled(_ key: BCDContentKey) {
        try? FileManager.default.removeItem(at: pendingURL(key.id))
    }

    private func pendingURL(_ id: String) -> URL { Self.folder.appendingPathComponent("pending/\(id).json") }

    private func pending(_ id: String) -> BCDGOGPlanInfo? {
        (try? Data(contentsOf: pendingURL(id))).flatMap { try? JSONDecoder().decode(BCDGOGPlanInfo.self, from: $0) }
    }

    private func savePending(_ info: BCDGOGPlanInfo) throws {
        let url = pendingURL(info.productID)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(info).write(to: url, options: .atomic)
    }
}

/// Why Play cannot start a GOG game, in words for the player.
struct BCDGOGPlayRefusal: Error {
    var message: String
}
