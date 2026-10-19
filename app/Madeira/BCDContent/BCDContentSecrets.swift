// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
#if canImport(Security)
import Security
#endif

/// Where a store keeps its sign-in: one item per store, read and written whole.
protocol BCDSecretStore: AnyObject, Sendable {
    func read(_ name: String) -> Data?
    func write(_ name: String, _ data: Data) throws
    func delete(_ name: String)
}

/// The Keychain: one generic-password item per store ("bcd.gog.tokens"), this device only and
/// never synced. Readable after the first unlock, so a download running in the background with
/// the phone locked can renew its addresses (Steam's item stays as it is). Sign-out deletes it.
final class BCDKeychain: BCDSecretStore, @unchecked Sendable {
    static let shared = BCDKeychain()

    private func query(_ name: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: name,
         kSecAttrAccount as String: "default",
         kSecAttrSynchronizable as String: false]
    }

    func read(_ name: String) -> Data? {
        var q = query(name)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &result) == errSecSuccess else { return nil }
        return result as? Data
    }

    func write(_ name: String, _ data: Data) throws {
        let attributes: [String: Any] = [kSecValueData as String: data,
                                         kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        var status = SecItemUpdate(query(name) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(query(name).merging(attributes) { $1 } as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw BCDContentError.refused("BCD couldn't save the sign-in in the Keychain (\(status)).") }
    }

    func delete(_ name: String) {
        SecItemDelete(query(name) as CFDictionary)
    }
}

/// Secrets in memory only: the host checks, never the app.
final class BCDMemorySecrets: BCDSecretStore, @unchecked Sendable {
    private let lock = NSLock()
    private var items: [String: Data] = [:]

    func read(_ name: String) -> Data? { lock.lock(); defer { lock.unlock() }; return items[name] }
    func write(_ name: String, _ data: Data) throws { lock.lock(); items[name] = data; lock.unlock() }
    func delete(_ name: String) { lock.lock(); items[name] = nil; lock.unlock() }
}
