import Foundation

/// madeira-bcd (2026-10-23): Steam games on an external drive.
///
/// The player picks a folder on a USB drive once (Settings › Storage › External drive). Its
/// security-scoped bookmark is kept and opened on every start and every return to BCD, so the
/// downloader and Wine, which both run in this process, reach it by path. A game installed there
/// keeps its record (`appmanifest_<id>.acf`) in drive C:; only `steamapps/common/<folder>` is a
/// symbolic link to `<drive folder>/steamapps/common/<folder>`. iOS mounts the drive under a new
/// path each time, so the links are pointed again whenever the drive opens.
@MainActor final class ExternalDrive {
    static let shared = ExternalDrive()
    /// Posted when a folder is chosen or forgotten (BCDLiveStore asks for the free room again).
    static let changed = Notification.Name("ExternalDriveChanged")

    private static let bookmarkKey = "ExternalDriveBookmark"
    private static let nameKey = "ExternalDriveName"
    /// Steam app id → its install folder on the drive ("" until its first download starts).
    private static let gamesKey = "ExternalDriveGames"

    /// The picked folder while it is open (the drive is plugged in and iOS granted access).
    private(set) var folder: URL?

    var isChosen: Bool { UserDefaults.standard.data(forKey: Self.bookmarkKey) != nil }
    /// The picked folder's name, also while the drive is unplugged.
    var folderName: String? { UserDefaults.standard.string(forKey: Self.nameKey) }

    private var games: [String: String] {
        get { UserDefaults.standard.dictionary(forKey: Self.gamesKey) as? [String: String] ?? [:] }
        set { UserDefaults.standard.set(newValue, forKey: Self.gamesKey) }
    }

    /// Keeps the folder the player picked (a URL from the folder picker) and opens it.
    func choose(_ url: URL) throws {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let data = try url.bookmarkData(options: [], includingResourceValuesForKeys: nil, relativeTo: nil)
        close()
        UserDefaults.standard.set(data, forKey: Self.bookmarkKey)
        UserDefaults.standard.set(url.lastPathComponent, forKey: Self.nameKey)
        LogStore.shared.log("[bcd-drive] chosen folder=\(url.lastPathComponent)")
        open()
        NotificationCenter.default.post(name: Self.changed, object: nil)
    }

    /// Forgets the folder. Games on the drive stay there and in the library; they start again
    /// once the same folder is chosen.
    func forget() {
        close()
        UserDefaults.standard.removeObject(forKey: Self.bookmarkKey)
        UserDefaults.standard.removeObject(forKey: Self.nameKey)
        LogStore.shared.log("[bcd-drive] forgotten")
        NotificationCenter.default.post(name: Self.changed, object: nil)
    }

    /// Opens the kept folder unless it is open; true when it is open afterwards.
    @discardableResult func open() -> Bool {
        if let folder, FileManager.default.fileExists(atPath: folder.path) { return true }
        close()
        guard let data = UserDefaults.standard.data(forKey: Self.bookmarkKey) else { return false }
        var stale = false
        guard let url = try? URL(resolvingBookmarkData: data, options: [], relativeTo: nil, bookmarkDataIsStale: &stale),
              url.startAccessingSecurityScopedResource() else { return false }
        guard FileManager.default.fileExists(atPath: url.path) else {
            url.stopAccessingSecurityScopedResource()
            return false
        }
        if stale, let fresh = try? url.bookmarkData(options: [], includingResourceValuesForKeys: nil, relativeTo: nil) {
            UserDefaults.standard.set(fresh, forKey: Self.bookmarkKey)
        }
        folder = url
        LogStore.shared.log("[bcd-drive] open folder=\(url.lastPathComponent) games=\(games.count)")
        relink()
        return true
    }

    private func close() {
        folder?.stopAccessingSecurityScopedResource()
        folder = nil
    }

    /// Free room on the drive, or nil when no drive is chosen or it is unplugged.
    var freeBytes: Int64? {
        guard open(), let folder else { return nil }
        let values = try? folder.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey,
                                                          .volumeAvailableCapacityKey])
        if let important = values?.volumeAvailableCapacityForImportantUsage, important > 0 { return important }
        return values?.volumeAvailableCapacity.map(Int64.init)
    }

    /// The drive's own `steamapps/common`.
    private var driveCommon: URL? { folder?.appendingPathComponent("steamapps/common", isDirectory: true) }

    func holds(_ appID: Int) -> Bool { games[String(appID)] != nil }

    /// Install › External drive: the next download of this game goes to the drive.
    func claim(_ appID: Int) {
        var all = games
        if all[String(appID)] == nil { all[String(appID)] = "" }
        games = all
        LogStore.shared.log("[bcd-drive] claim app=\(appID)")
    }

    /// The game left the drive (uninstalled, or its first download cancelled).
    func release(_ appID: Int) {
        var all = games
        guard all.removeValue(forKey: String(appID)) != nil else { return }
        games = all
        LogStore.shared.log("[bcd-drive] release app=\(appID)")
    }

    /// Before a download of a game the drive holds: its folder there and its link in `common`.
    /// Does nothing for other games; throws when the drive is unplugged.
    func prepare(appID: Int, folder name: String, common: URL) throws {
        guard holds(appID) else { return }
        guard open(), let driveCommon else { throw ExternalDriveError.notConnected }
        let target = driveCommon.appendingPathComponent(name, isDirectory: true)
        try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: common, withIntermediateDirectories: true)
        try Self.link(common.appendingPathComponent(name, isDirectory: true), to: target)
        var all = games
        all[String(appID)] = name
        games = all
        LogStore.shared.log("[bcd-drive] prepared app=\(appID) folder=\(name)")
    }

    /// What Play says instead of starting a game the drive holds while it is unplugged.
    func startBlocker(appID: Int) -> String? {
        guard holds(appID), !open() else { return nil }
        return ExternalDriveError.notConnected.message
    }

    /// Points every drive game's link at the drive's current path.
    private func relink() {
        guard let driveCommon else { return }
        let common = SteamInstallPaths.common(drive: SteamOwnedLibrary.drive)
        for (app, name) in games where !name.isEmpty {
            let target = driveCommon.appendingPathComponent(name, isDirectory: true)
            guard FileManager.default.fileExists(atPath: target.path) else {
                LogStore.shared.log("[bcd-drive] missing on the drive app=\(app) folder=\(name)")
                continue
            }
            do { try Self.link(common.appendingPathComponent(name, isDirectory: true), to: target) } catch {
                LogStore.shared.log("[bcd-drive] relink failed app=\(app): \(error)")
            }
        }
    }

    /// A symbolic link at `at` to `target`, replacing an older link; never replaces a real folder.
    private static func link(_ at: URL, to target: URL) throws {
        let fm = FileManager.default
        if let old = try? fm.destinationOfSymbolicLink(atPath: at.path) {
            if old == target.path { return }
            try fm.removeItem(at: at)
        } else if fm.fileExists(atPath: at.path) {
            throw ExternalDriveError.folderOnDevice(at.lastPathComponent)
        }
        try fm.createSymbolicLink(at: at, withDestinationURL: target)
    }
}

enum ExternalDriveError: LocalizedError {
    case notConnected
    case folderOnDevice(String)

    var message: String {
        switch self {
        case .notConnected:
            return "Connect the external drive with your games, then try again."
        case .folderOnDevice(let name):
            return "\(name) is already installed on this device. Uninstall it first to put it on the external drive."
        }
    }

    var errorDescription: String? { message }
}
