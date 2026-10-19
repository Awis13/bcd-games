import Foundation

/// ml1095: ONE configuration file for every runtime switch: Documents/bcd.cfg
///
///     # comments start with #
///     key = value        (whitespace trimmed; the value runs to the end of the
///                         line, so it may contain '='; the last line wins)
///
/// Keys: swap-mb, vram-mb, pool, totalphys, wx, ... Environment exports are
/// "env.NAME = value". DXMT options are one line, "dxmt = a=b;c=d".
///
/// The native side reads the same file through build/madeira_cfg.h with the
/// same rules; it is the single source of truth. It was madeira.cfg (and before
/// ml1095 one file per switch, madeira-<key>.txt) until BCD took its own name
/// (2026-10-18). Every device installs BCD fresh, so neither old name is read.
enum MadeiraConfig {
    static let fileName = "bcd.cfg"

    static var documents: URL? {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
    }
    static var url: URL? { documents?.appendingPathComponent(fileName) }
    static var present: Bool { url.map { FileManager.default.fileExists(atPath: $0.path) } ?? false }

    /// All key/value pairs of bcd.cfg (empty when the file is absent).
    static func all() -> [String: String] {
        guard let u = url, let text = try? String(contentsOf: u, encoding: .utf8) else { return [:] }
        return parse(text)
    }

    /// The key/value pairs of text in bcd.cfg's syntax, the last line winning.
    static func parse(_ text: String) -> [String: String] {
        var out: [String: String] = [:]
        // A file saved by some editors starts with a UTF-8 byte order mark, which Foundation keeps:
        // without this the first key reads as "\u{FEFF}key" and is lost (the C reader skips it).
        let text = text.hasPrefix("\u{FEFF}") ? String(text.dropFirst()) : text
        for raw in text.split(omittingEmptySubsequences: false, whereSeparator: { $0 == "\n" || $0 == "\r\n" }) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") { continue }
            guard let eq = line.firstIndex(of: "=") else { continue }
            let k = line[..<eq].trimmingCharacters(in: .whitespaces)
            let v = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces)
            if !k.isEmpty { out[k] = v }
        }
        return out
    }

    /// The value for `key`, trimmed, or nil when unset (also when bcd.cfg is absent).
    static func get(_ key: String) -> String? { all()[key] }

    /// A library game's own lines (Game details › This game's config): written to
    /// Application Support for each launch and named by MADEIRA_CFG_GAME, which
    /// build/madeira_cfg.h reads after bcd.cfg so a key there wins, and whose
    /// env.NAME lines WineProcessBridge.m exports after bcd.cfg's.
    static var gameURL: URL? {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("bcd-game.cfg")
    }

    /// `key` from the running game's own lines, or nil (also when empty). The
    /// native reader lets it win on its own; Swift readers that merge (dxmt) ask.
    /// madeira-bcd: whichever file MADEIRA_CFG_GAME names -- a game's own
    /// GameProfiles.swift file, or the merged bcd-game.cfg (applyGame) --
    /// with the same parser GameProfile uses.
    static func gameValue(_ key: String) -> String? {
        guard let p = getenv("MADEIRA_CFG_GAME"), case let path = String(cString: p), !path.isEmpty,
              let text = try? String(contentsOfFile: path, encoding: .utf8) else { return nil }
        return parse(text)[key].flatMap { $0.isEmpty ? nil : $0 }
    }

    /// Makes `text` the running game's lines: written and exported as
    /// MADEIRA_CFG_GAME when it sets anything, otherwise the variable is unset
    /// and the file removed, so a previous game's lines never apply. Returns the
    /// pairs it applied; throws (with the variable unset) when the file cannot
    /// be written.
    ///
    /// madeira-bcd: a game's own GameProfiles.swift file (Application
    /// Support/GameConfigs/<hash>.cfg) is exported as MADEIRA_CFG_GAME by
    /// BCDLaunch.applyExtras on the main thread before this runs on the launch
    /// worker. That file stays the game's file: with no lines of the entry's own
    /// it is left exported exactly as it was (the owner's per-game profiles run
    /// unchanged); with some, both are written to bcd-game.cfg, the entry's
    /// lines first and the game's file after them, so a key the game's file sets
    /// wins. A launch without a game file (applyExtras unset the variable) is
    /// upstream's behaviour unchanged.
    @discardableResult
    static func applyGame(_ text: String?) throws -> [String: String] {
        let profileURL = bcdProfileURL()
        unsetenv("MADEIRA_CFG_GAME")
        let pairs = parse(text ?? "")
        guard let u = gameURL else { return [:] }
        if let profileURL {
            let profileText = (try? String(contentsOf: profileURL, encoding: .utf8)) ?? ""
            let profilePairs = parse(profileText)
            if pairs.isEmpty || text == nil {
                try? FileManager.default.removeItem(at: u)
                if !profilePairs.isEmpty { setenv("MADEIRA_CFG_GAME", profileURL.path, 1) }
                return profilePairs
            }
            if let text {
                let merged = (text.hasSuffix("\n") ? text : text + "\n")
                    + "# BCD: the game's own file (\(profileURL.lastPathComponent)), after the lines above so it wins\n"
                    + profileText
                try FileManager.default.createDirectory(at: u.deletingLastPathComponent(), withIntermediateDirectories: true)
                try (merged.hasSuffix("\n") ? merged : merged + "\n").write(to: u, atomically: true, encoding: .utf8)
                setenv("MADEIRA_CFG_GAME", u.path, 1)
                return pairs.merging(profilePairs) { $1 }
            }
        }
        guard !pairs.isEmpty, let text else {
            try? FileManager.default.removeItem(at: u)
            return [:]
        }
        try FileManager.default.createDirectory(at: u.deletingLastPathComponent(), withIntermediateDirectories: true)
        try (text.hasSuffix("\n") ? text : text + "\n").write(to: u, atomically: true, encoding: .utf8)
        setenv("MADEIRA_CFG_GAME", u.path, 1)
        return pairs
    }

    /// madeira-bcd: the GameProfiles.swift file MADEIRA_CFG_GAME names right now
    /// (a file directly inside Application Support/GameConfigs), else nil --
    /// also for the merged bcd-game.cfg.
    private static func bcdProfileURL() -> URL? {
        // GameProfile.directory's folder, named here so this file compiles on its own
        // (the onboarding, runtime-settings and early-config host checks build it alone).
        guard let p = getenv("MADEIRA_CFG_GAME"), case let path = String(cString: p), !path.isEmpty,
              let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
        else { return nil }
        let dir = base.appendingPathComponent("GameConfigs", isDirectory: true)
        let url = URL(fileURLWithPath: path)
        guard url.deletingLastPathComponent().standardizedFileURL.path == dir.standardizedFileURL.path,
              FileManager.default.fileExists(atPath: url.path) else { return nil }
        return url
    }

    static func bool(_ key: String, default dflt: Bool = false) -> Bool {
        guard let v = get(key) else { return dflt }
        return ["1", "on", "true", "yes"].contains(v)
    }

    /// Set or remove one key in bcd.cfg (Settings). Comments and every
    /// other line are kept; earlier lines for the key are dropped and the new
    /// value is appended; a nil value removes the key. Without bcd.cfg the
    /// file is created with that one line.
    @discardableResult
    static func set(_ key: String, _ value: String?) -> Bool {
        guard let u = url else { return false }
        let text = (try? String(contentsOf: u, encoding: .utf8)) ?? ""
        var lines = text.split(omittingEmptySubsequences: false, whereSeparator: { $0 == "\n" || $0 == "\r\n" }).map(String.init)
        if lines.last == "" { lines.removeLast() }
        lines.removeAll { line in
            let t = line.trimmingCharacters(in: .whitespaces)
            guard !t.hasPrefix("#"), let eq = t.firstIndex(of: "=") else { return false }
            return t[..<eq].trimmingCharacters(in: .whitespaces) == key
        }
        if let value { lines.append("\(key) = \(value)") }
        let out = lines.joined(separator: "\n") + (lines.isEmpty ? "" : "\n")
        do { try out.write(to: u, atomically: true, encoding: .utf8); return true } catch { return false }
    }

    /// An app-side switch spelled like the native ones: `env.NAME = value` in
    /// bcd.cfg, else the process environment, else `fallback`. Any value
    /// other than "0" means on.
    static func flag(_ name: String, fallback: Bool = true) -> Bool {
        if present, let v = all()["env." + name] { return v != "0" }
        return getenv(name).map { String(cString: $0) != "0" } ?? fallback
    }
}
