// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import CryptoKit
import Foundation

// The account's GOG games and their pictures (gog.md §2.2): the owned list, one call per page of
// `embed.gog.com/account/getFilteredProducts`, Windows games only; the tall cover, the backdrop
// and the wide tile from GOG's public game database (`gamesdb.gog.com`, no sign-in, once per game,
// kept), with the list's own landscape picture as the fallback. Foundation only.

struct BCDGOGProduct: Codable, Hashable, Identifiable {
    /// GOG's product ID, decimal.
    var id: String
    var title: String
    /// The list's picture base ("//images-1.gog-statics.com/<hash>"), sizes added as suffixes.
    var image: String?
    /// From gamesdb, once looked up: the tall cover and the backdrop.
    var cover: String?
    var background: String?
    var artChecked: Date?
}

/// A game's pictures, each a list of addresses tried in order (BCDGame's shapes).
struct BCDGOGArt: Equatable {
    var cover: [URL] = []
    var header: [URL] = []
    var hero: [URL] = []
}

enum BCDGOGCatalog {
    static func pageURL(_ page: Int) -> URL {
        URL(string: "https://embed.gog.com/account/getFilteredProducts?mediaType=1&page=\(max(1, page))")!
    }

    static func gamesDBURL(_ id: String) -> URL {
        URL(string: "https://gamesdb.gog.com/platforms/gog/external_releases/" + id)!
    }

    static func validID(_ id: String) -> Bool { (1...20).contains(id.count) && id.allSatisfy { $0.isASCII && $0.isNumber } }

    /// One page of the owned list: the Windows games on it, and how many pages there are.
    static func parsePage(_ object: [String: Any]) throws -> (products: [BCDGOGProduct], pages: Int) {
        guard let items = object["products"] as? [[String: Any]] else { throw BCDGOGError.badReply("library") }
        let pages = (object["totalPages"] as? NSNumber)?.intValue ?? 1
        var products: [BCDGOGProduct] = []
        for item in items {
            let id = (item["id"] as? NSNumber)?.stringValue ?? (item["id"] as? String) ?? ""
            let title = ((item["title"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            let windows = ((item["worksOn"] as? [String: Any])?["Windows"] as? Bool) ?? false
            guard validID(id), !title.isEmpty, windows else { continue }
            products.append(BCDGOGProduct(id: id, title: String(title.prefix(200)), image: imageBase(item["image"] as? String)))
        }
        return (products, max(1, min(pages, 1000)))
    }

    /// The list's picture base, kept only when it is one of GOG's image hosts.
    static func imageBase(_ text: String?) -> String? {
        guard let text, text.hasPrefix("//"), text.utf8.count < 200,
              BCDGOGAPI.trusted(URL(string: "https:" + text + "_800.jpg")),
              text.dropFirst(2).allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || "-._/".contains($0)) }) else { return nil }
        return text
    }

    /// gamesdb's tall cover and backdrop: `url_format` with its `{formatter}` empty (the original
    /// picture) and `{ext}` jpg.
    static func parseGamesDB(_ object: [String: Any]) -> (cover: String?, background: String?) {
        let game = object["game"] as? [String: Any] ?? [:]
        func picture(_ key: String) -> String? {
            guard let format = (game[key] as? [String: Any])?["url_format"] as? String else { return nil }
            let filled = format.replacingOccurrences(of: "{formatter}", with: "").replacingOccurrences(of: "{ext}", with: "jpg")
            return BCDGOGAPI.trusted(URL(string: filled)) && filled.utf8.count < 400 ? filled : nil
        }
        return (picture("vertical_cover") ?? picture("cover"), picture("background"))
    }

    static func art(_ product: BCDGOGProduct) -> BCDGOGArt {
        func url(_ text: String?) -> URL? { text.flatMap(URL.init(string:)).flatMap { BCDGOGAPI.trusted($0) ? $0 : nil } }
        let tile = url(product.image.map { "https:" + $0 + "_800.jpg" })
        let backdrop = url(product.image.map { "https:" + $0 + "_bg_crop_1920x655.jpg" })
        return BCDGOGArt(cover: [url(product.cover), tile].compactMap { $0 },
                         header: [tile].compactMap { $0 },
                         hero: [url(product.background), backdrop].compactMap { $0 })
    }

    /// The cached list belongs to one account: a SHA-256 of its user ID, so the file holds no ID.
    static func accountKey(_ userID: String) -> String {
        SHA256.hash(data: Data(userID.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    struct Cache: Codable {
        var version = 1
        var account: String
        var updated: Date
        var products: [BCDGOGProduct]
    }
}
