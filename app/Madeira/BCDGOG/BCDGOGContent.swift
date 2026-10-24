// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// What a GOG game's download is, turned into a BCDContent plan (gog.md §2.3; the format as GOG's
// own public manifests show it, docs/GOG.md): the newest public build of content system
// generation 2, its build manifest (zlib JSON), the depots for the game, the language and 64-bit
// (or neutral) builds, each depot's manifest of files made of chunks (zlib, checked by MD5 before
// and after unpacking), and the secure links that serve the chunks. Foundation only.

struct BCDGOGBuild: Equatable {
    var id: String
    var version: String
    var link: URL
}

struct BCDGOGBuildManifest {
    struct Depot: Equatable {
        var manifest: String
        var productID: String
        var languages: [String]
        var bitness: [String]?
        var gogDepot: Bool
    }
    var baseProductID: String
    var buildID: String
    var installDirectory: String
    var depots: [Depot]
    var dependencies: [String]
    /// The game's own Galaxy identity (for BCD Pier, M3): never logged.
    var clientID: String?
}

/// What the plan was made from, kept until the install is recorded.
struct BCDGOGPlanInfo: Codable, Equatable {
    var productID: String
    var title: String
    var buildID: String
    var version: String
    var language: String
    var depots: [String]
    var dependencies: [String]
    var supportFiles: Int
    var links: Int
    var containers: Bool
}

enum BCDGOGContent {
    static func buildsURL(_ id: String) -> URL {
        URL(string: "https://content-system.gog.com/products/\(id)/os/windows/builds?generation=2")!
    }

    static func secureLinkURL(_ id: String) -> URL {
        URL(string: "https://content-system.gog.com/products/\(id)/secure_link?generation=2&_version=2&path=/")!
    }

    static func validHash(_ text: String) -> Bool {
        text.utf8.count == 32 && text.utf8.allSatisfy { ($0 >= 48 && $0 <= 57) || ($0 >= 97 && $0 <= 102) }
    }

    /// A depot manifest's address, on the host the build's own link uses (GOG's CDN).
    static func depotURL(_ hash: String, near link: URL) -> URL {
        let host = BCDGOGAPI.trusted(link) ? link.host! : "cdn.gog.com"
        return URL(string: "https://\(host)/content-system/v2/meta/\(hash.prefix(2))/\(hash.dropFirst(2).prefix(2))/\(hash)")!
    }

    /// A build link on a CDN outside GOG's own hosts, read from GOG's cdn.gog.com instead (nil when it is not a
    /// manifest link). GOG's build list names the CDN it picks for the player, and in some regions that is not a gog.com
    /// host (2026-10-24: gog-cdn.gcdn.co; BioShock 2, Cult of the Lamb and RollerCoaster Tycoon Deluxe stopped with "GOG's
    /// reply wasn't what BCD expects"). A manifest is a file named by the MD5 of its bytes under
    /// /content-system/v2/meta/<c0c1>/<c2c3>/<md5>, and every GOG CDN serves the same file under that path, so only a link
    /// of exactly that form moves to cdn.gog.com (the host depotURL already falls back to). A link on a gog.com host is
    /// used as before.
    static func gogHostLink(_ link: URL) -> URL? {
        guard link.scheme == "https", link.user == nil, link.query == nil, link.fragment == nil else { return nil }
        let parts = link.path.split(separator: "/", omittingEmptySubsequences: false)
        guard parts.count == 7, parts[0].isEmpty, parts[1] == "content-system", parts[2] == "v2", parts[3] == "meta" else { return nil }
        let hash = String(parts[6])
        guard validHash(hash), parts[4] == hash.prefix(2), parts[5] == hash.dropFirst(2).prefix(2) else { return nil }
        return URL(string: "https://cdn.gog.com/content-system/v2/meta/\(parts[4])/\(parts[5])/\(hash)")
    }

    /// The newest public generation-2 build (the list is newest first).
    static func newestBuild(_ object: [String: Any]) throws -> BCDGOGBuild {
        guard let items = object["items"] as? [[String: Any]] else {
            BCDContentLog.write("[gog-content] builds reply: " + BCDGOGAPI.shape(object, lists: ["items"]))
            throw BCDGOGError.badReply("builds")
        }
        var older = false
        for item in items {
            let generation = (item["generation"] as? NSNumber)?.intValue ?? 0
            guard item["branch"] == nil || item["branch"] is NSNull else { continue }
            if generation == 1 { older = true; continue }
            guard generation == 2 else { continue }
            let id = (item["build_id"] as? String) ?? (item["build_id"] as? NSNumber)?.stringValue ?? ""
            let given = (item["link"] as? String).flatMap(URL.init(string:))
            let moved = given.flatMap { BCDGOGAPI.trusted($0) ? nil : gogHostLink($0) }
            guard BCDGOGCatalog.validID(id), let given, let link = BCDGOGAPI.trusted(given) ? given : moved else {
                // The host of the link is GOG's own to say, so it is the one thing named: BCD follows only gog.com
                // and gog-statics.com hosts. The build's id is a number of GOG's, not the player's.
                let where_: String
                if let text = item["link"] as? String, let target = URL(string: text) {
                    where_ = BCDGOGAPI.trusted(target) ? "gog" : (target.scheme == "https" ? "other-host" : "not-secure")
                } else {
                    where_ = "none"
                }
                BCDContentLog.write("[gog-content] build entry: id=\(BCDGOGCatalog.validID(id) ? "ok" : "bad") target=\(where_) "
                                    + BCDGOGAPI.shape(item, lists: []))
                throw BCDGOGError.badReply("build")
            }
            if moved != nil {
                BCDContentLog.write("[gog-content] build manifest read from cdn.gog.com (the list named a CDN outside gog.com)")
            }
            let version = ((item["version_name"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            return BCDGOGBuild(id: id, version: String(version.prefix(80)), link: link)
        }
        throw older ? BCDGOGError.olderBuildsOnly : BCDGOGError.noWindowsBuild
    }

    static func parseBuildManifest(_ object: [String: Any]) throws -> BCDGOGBuildManifest {
        func text(_ value: Any?) -> String? { (value as? String) ?? (value as? NSNumber)?.stringValue }
        guard (object["version"] as? NSNumber)?.intValue == 2, let base = text(object["baseProductId"]), BCDGOGCatalog.validID(base),
              let depots = object["depots"] as? [[String: Any]] else {
            BCDContentLog.write("[gog-content] build manifest: " + BCDGOGAPI.shape(object, lists: ["depots"]))
            throw BCDGOGError.badReply("manifest")
        }
        var list: [BCDGOGBuildManifest.Depot] = []
        for depot in depots {
            guard let hash = depot["manifest"] as? String, validHash(hash), let product = text(depot["productId"]),
                  BCDGOGCatalog.validID(product) else {
                BCDContentLog.write("[gog-content] build manifest depot: " + BCDGOGAPI.shape(depot, lists: []) +
                                    " hash-bytes=\((depot["manifest"] as? String)?.utf8.count ?? -1)")
                throw BCDGOGError.badReply("depot")
            }
            let languages = (depot["languages"] as? [String] ?? []).map { $0.trimmingCharacters(in: .whitespaces) }
            list.append(.init(manifest: hash, productID: product, languages: languages,
                              bitness: depot["osBitness"] as? [String], gogDepot: depot["isGogDepot"] as? Bool ?? false))
        }
        let client = text(object["clientId"]).flatMap { BCDGOGCatalog.validID($0) ? $0 : nil }
        return BCDGOGBuildManifest(baseProductID: base, buildID: text(object["buildId"]) ?? "",
                                   installDirectory: (object["installDirectory"] as? String) ?? "",
                                   depots: list, dependencies: (object["dependencies"] as? [String] ?? []).filter { $0.count <= 64 },
                                   clientID: client)
    }

    /// The language to install: the device's first preferred language GOG offers (exactly, then by
    /// language and script, then by language alone), else US English, else any English, else the
    /// first one offered. GOG writes "en-US", "zh-Hans" and sometimes "en".
    static func language(offered: [String], preferred: [String]) -> String {
        let offered = offered.filter { $0 != "*" && !$0.isEmpty }
        guard let first = offered.first else { return "*" }
        func pieces(_ code: String) -> [String] { code.replacingOccurrences(of: "_", with: "-").lowercased().split(separator: "-").map(String.init) }
        for wanted in preferred {
            let w = pieces(wanted)
            guard let primary = w.first else { continue }
            if let exact = offered.first(where: { pieces($0) == w }) { return exact }
            if w.count >= 2, let script = offered.first(where: { Array(pieces($0).prefix(2)) == Array(w.prefix(2)) }) { return script }
            if let bare = offered.first(where: { pieces($0) == [primary] }) { return bare }
            if let same = offered.first(where: { pieces($0).first == primary }) { return same }
        }
        return offered.first { pieces($0) == ["en", "us"] } ?? offered.first { pieces($0).first == "en" } ?? first
    }

    /// The depots to install for `products`: the language's and the neutral ones, 64-bit when the
    /// build has 64-bit depots (else 32-bit), each manifest once.
    static func depots(_ manifest: BCDGOGBuildManifest, products: Set<String>, language: String) -> [BCDGOGBuildManifest.Depot] {
        let mine = manifest.depots.filter { products.contains($0.productID) }
        let bits = mine.contains { $0.bitness?.contains("64") == true } ? "64" : "32"
        var seen = Set<String>()
        return mine.filter { depot in
            let languageOK = depot.languages.contains("*") || depot.languages.contains { $0.caseInsensitiveCompare(language) == .orderedSame }
                || (language == "*" && depot.languages.isEmpty)
            let bitsOK = depot.bitness == nil || depot.bitness!.isEmpty || depot.bitness!.contains(bits)
            return languageOK && bitsOK && seen.insert(depot.manifest).inserted
        }
    }

    struct DepotResult: Equatable {
        var files = 0
        var support = 0
        var links = 0
        var containerFiles = 0
    }

    private static let knownFileKeys: Set<String> = ["path", "type", "chunks", "md5", "sha256", "flags", "sfcRef"]

    /// One depot manifest into the plan. Files flagged `support` (GOG's own setup and its helpers)
    /// are left out until BCD runs them (M2). Links are macOS-only and skipped. With `containers`,
    /// small files GOG also packs into the depot's small-files container are cut from it (one
    /// fetch instead of one per file) when every reference is consistent; otherwise, and by
    /// default, each file comes from its own chunks.
    static func add(depot object: [String: Any], product: String, to builder: inout BCDContentPlanBuilder,
                    containers: Bool) throws -> DepotResult {
        guard let depot = object["depot"] as? [String: Any], let items = depot["items"] as? [[String: Any]] else {
            throw BCDGOGError.badReply("depot-manifest")
        }
        func number(_ value: Any?) -> Int64? { (value as? NSNumber)?.int64Value }
        func chunk(_ c: [String: Any]) throws -> BCDContentChunk {
            guard let md5 = c["md5"] as? String, let packed = c["compressedMd5"] as? String,
                  let size = number(c["size"]), let packedSize = number(c["compressedSize"]), size > 0, packedSize > 0,
                  let digest = BCDDigest(.md5, md5), let fetchDigest = BCDDigest(.md5, packed) else {
                throw BCDGOGError.badReply("chunk")
            }
            return BCDContentChunk(locator: fetchDigest.hex, group: product, fetchSize: packedSize, size: size,
                                   fetchDigest: fetchDigest, digest: digest, codec: .zlib)
        }

        // The small-files container: usable when every reference lies inside it and matches its
        // file. A file listed without chunks of its own can only come from it.
        var container: [(index: Int, start: Int64)] = []
        var containerSize: Int64 = 0
        if let sfc = depot["smallFilesContainer"] as? [String: Any], let chunks = sfc["chunks"] as? [[String: Any]], !chunks.isEmpty {
            for c in chunks {
                let made = try chunk(c)
                container.append((try builder.addChunk(made), containerSize))
                containerSize += made.size
            }
            for item in items {
                guard let ref = item["sfcRef"] as? [String: Any] else { continue }
                let own = item["chunks"] as? [[String: Any]] ?? []
                let size = own.reduce(Int64(0)) { $0 + (number($1["size"]) ?? -1) }
                guard let offset = number(ref["offset"]), let length = number(ref["size"]), offset >= 0, length >= 0,
                      own.isEmpty || length == size, offset + length <= containerSize else { container = []; break }
            }
        }

        var result = DepotResult()
        for item in items {
            let type = item["type"] as? String ?? ""
            guard let path = item["path"] as? String else { throw BCDGOGError.badReply("item") }
            switch type {
            case "DepotDirectory":
                try builder.addDirectory(path)
                continue
            case "DepotLink":
                result.links += 1
                continue
            case "DepotFile":
                break
            default:
                throw BCDGOGError.unknownFileSource
            }
            if (item["flags"] as? [String] ?? []).contains("support") { result.support += 1; continue }
            guard Set(item.keys).isSubset(of: knownFileKeys) || !(item["chunks"] as? [[String: Any]] ?? []).isEmpty else {
                throw BCDGOGError.unknownFileSource
            }
            let chunks = try (item["chunks"] as? [[String: Any]] ?? []).map(chunk)
            let ref = item["sfcRef"] as? [String: Any]
            let sliced = !container.isEmpty && ref != nil && (containers || chunks.isEmpty)
            if chunks.isEmpty, ref != nil, !sliced {
                throw BCDGOGError.unknownFileSource   // its bytes are only in a container BCD can't use
            }
            let size = sliced ? (number(ref?["size"]) ?? 0) : chunks.reduce(Int64(0)) { $0 + $1.size }
            let digest = (item["md5"] as? String).flatMap { BCDDigest(.md5, $0) }
                ?? (item["sha256"] as? String).flatMap { BCDDigest(.sha256, $0) }
            let file = try builder.addFile(path, size: size, digest: digest)
            result.files += 1
            if sliced {
                // The file's bytes are [offset, offset + size) of the container: a slice of each
                // container chunk that range crosses.
                let offset = number(ref?["offset"]) ?? 0
                for (index, (chunkIndex, start)) in container.enumerated() {
                    let end = index + 1 < container.count ? container[index + 1].start : containerSize
                    let from = max(offset, start), to = min(offset + size, end)
                    if from < to {
                        try builder.addPart(chunk: chunkIndex, chunkOffset: from - start, length: to - from, file: file, fileOffset: from - offset)
                    }
                }
                result.containerFiles += 1
                continue
            }
            var offset: Int64 = 0
            for c in chunks {
                try builder.addPart(chunk: builder.addChunk(c), chunkOffset: 0, length: c.size, file: file, fileOffset: offset)
                offset += c.size
            }
        }
        return result
    }

    // MARK: The plan

    /// GOG's reply for an address, signed in or not (BCDGOGAPI.json in the app, a fixture in the
    /// host checks).
    typealias Fetch = (URL, Bool) async throws -> [String: Any]

    /// The newest public build of a product.
    static func newest(_ id: String, fetch: Fetch) async throws -> BCDGOGBuild {
        try newestBuild(await fetch(buildsURL(id), true))
    }

    /// The plan to install `build` of the game `key.id`: its depots for the device's language,
    /// files from the depot manifests, a later depot winning a path. Off the main thread: big
    /// manifests take a moment to read.
    static func plan(_ key: BCDContentKey, title: String, build: BCDGOGBuild, preferred: [String], containers: Bool,
                     fetch: Fetch) async throws -> (plan: BCDContentPlan, info: BCDGOGPlanInfo) {
        let manifest = try parseBuildManifest(await fetch(build.link, false))
        let products: Set<String> = [key.id]   // the game itself; owned DLC is M2
        let offered = manifest.depots.filter { products.contains($0.productID) }.flatMap(\.languages)
        var seen = Set<String>()
        let language = Self.language(offered: offered.filter { seen.insert($0).inserted }, preferred: preferred)
        let chosen = depots(manifest, products: products, language: language)
        guard !chosen.isEmpty else { throw BCDGOGError.noWindowsBuild }
        let folder = BCDContentPaths.folderName(manifest.installDirectory) ?? BCDContentPaths.folderName(title) ?? "GOG \(key.id)"
        var builder = try BCDContentPlanBuilder(key: key, title: title, version: build.id, folder: folder)
        var total = DepotResult()
        for depot in chosen {
            try Task.checkCancellation()
            let object = try await fetch(depotURL(depot.manifest, near: build.link), false)
            let added = try add(depot: object, product: depot.productID, to: &builder, containers: containers)
            total.files += added.files
            total.support += added.support
            total.links += added.links
            total.containerFiles += added.containerFiles
        }
        let plan = try builder.build()
        let info = BCDGOGPlanInfo(productID: key.id, title: title, buildID: build.id, version: build.version, language: language,
                                  depots: chosen.map(\.manifest), dependencies: manifest.dependencies,
                                  supportFiles: total.support, links: total.links, containers: total.containerFiles > 0)
        BCDContentLog.write("[gog-content] plan product=\(key.id) depots=\(chosen.count) files=\(plan.files.count) " +
                            "support-skipped=\(total.support) links-skipped=\(total.links) container-files=\(total.containerFiles) " +
                            "redist=\(manifest.dependencies.count)")
        return (plan, info)
    }

    // MARK: Secure links

    struct Endpoint: Equatable {
        var format: String
        var parameters: [String: String]
        var priority: Int
        var fallbackOnly: Bool
    }

    /// A product's secure link: its endpoints, preferred first (priority, fallback-only last), and
    /// when the link lapses.
    static func parseSecureLink(_ object: [String: Any], now: Date) throws -> (endpoints: [Endpoint], expires: Date) {
        guard let urls = object["urls"] as? [[String: Any]] else { throw BCDGOGError.badReply("secure-link") }
        var endpoints: [Endpoint] = []
        var expires = now.addingTimeInterval(3600)
        for item in urls {
            guard let format = item["url_format"] as? String, let raw = item["parameters"] as? [String: Any] else { continue }
            if let generations = item["supports_generation"] as? [NSNumber], !generations.contains(2) { continue }
            var parameters: [String: String] = [:]
            for (key, value) in raw {
                if let s = value as? String { parameters[key] = s } else if let n = value as? NSNumber { parameters[key] = n.stringValue }
            }
            if let at = parameters["expires_at"].flatMap(Double.init) { expires = min(expires, Date(timeIntervalSince1970: at)) }
            endpoints.append(Endpoint(format: format, parameters: parameters, priority: (item["priority"] as? NSNumber)?.intValue ?? 0,
                                      fallbackOnly: item["fallback_only"] as? Bool ?? false))
        }
        guard !endpoints.isEmpty else { throw BCDGOGError.badReply("secure-link") }
        let ordered = endpoints.enumerated().sorted { a, b in
            if a.element.fallbackOnly != b.element.fallbackOnly { return !a.element.fallbackOnly }
            if a.element.priority != b.element.priority { return a.element.priority > b.element.priority }
            return a.offset < b.offset
        }.map(\.element)
        return (ordered, expires)
    }

    /// A chunk's address on one endpoint: its `path` parameter gains /<c0c1>/<c2c3>/<compressed MD5>,
    /// then every {parameter} of the format is filled. https only.
    static func chunkURL(_ endpoint: Endpoint, locator: String) -> URL? {
        guard validHash(locator) else { return nil }
        var parameters = endpoint.parameters
        parameters["path"] = (parameters["path"] ?? "") + "/\(locator.prefix(2))/\(locator.dropFirst(2).prefix(2))/\(locator)"
        var text = endpoint.format
        for (key, value) in parameters { text = text.replacingOccurrences(of: "{\(key)}", with: value) }
        guard !text.contains("{"), let url = URL(string: text), url.scheme == "https", url.host != nil else { return nil }
        return url
    }
}

/// GOG's chunks for one job: a secure link per product (a DLC's chunks under its own), fetched
/// when first needed and again when under two minutes are left or a host refuses it.
actor BCDGOGChunkSource: BCDChunkSource {
    private let fetch: @Sendable (String) async throws -> [String: Any]
    private let now: @Sendable () -> Date
    private var links: [String: (endpoints: [BCDGOGContent.Endpoint], expires: Date)] = [:]

    /// `fetch` reads a product's secure link (signed in).
    init(fetch: @escaping @Sendable (String) async throws -> [String: Any], now: @escaping @Sendable () -> Date = { Date() }) {
        self.fetch = fetch
        self.now = now
    }

    func urls(for chunk: BCDContentChunk) async throws -> [URL] {
        try await link(chunk.group).compactMap { BCDGOGContent.chunkURL($0, locator: chunk.locator) }
    }

    func refresh(group: String) async throws {
        links[group] = nil
        _ = try await link(group)
    }

    private func link(_ product: String) async throws -> [BCDGOGContent.Endpoint] {
        if let held = links[product], held.expires.timeIntervalSince(now()) > 120 { return held.endpoints }
        let made = try BCDGOGContent.parseSecureLink(await fetch(product), now: now())
        links[product] = made
        BCDContentLog.write("[gog-content] link product=\(product) endpoints=\(made.endpoints.count)")
        return made.endpoints
    }
}
