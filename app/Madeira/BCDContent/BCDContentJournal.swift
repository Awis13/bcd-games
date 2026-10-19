// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

/// The finished chunks of one job, so a download survives a pause, an app kill or a reboot.
///
/// An append-only file: a 72-byte header (`BCDJRNL1` and the plan's identity), then one 8-byte
/// record per finished chunk (its index and the index's complement, little-endian). A record is
/// appended only after the chunk's bytes are on the disk (BCDContentEngine syncs them first), so
/// every listed chunk is whole. A torn last record, a record that fails its complement, or a header
/// for another plan is ignored, and those chunks are fetched again.
final class BCDContentJournal {
    private static let magic = Array("BCDJRNL1".utf8)
    private let fd: Int32

    /// The chunks a journal lists for this plan (empty when there is none, or it is another plan's).
    static func load(_ url: URL, identity: String, chunkCount: Int) -> Set<Int> {
        guard let data = try? Data(contentsOf: url), data.count >= 72,
              Array(data.prefix(8)) == magic, data[8..<72].elementsEqual(identity.utf8) else { return [] }
        var done = Set<Int>()
        var offset = 72
        while offset + 8 <= data.count {
            var index: UInt32 = 0, check: UInt32 = 0
            for i in 0..<4 {
                index |= UInt32(data[data.startIndex + offset + i]) << (8 * UInt32(i))
                check |= UInt32(data[data.startIndex + offset + 4 + i]) << (8 * UInt32(i))
            }
            if check == ~index, Int(index) < chunkCount { done.insert(Int(index)) }
            offset += 8
        }
        return done
    }

    /// Opens the journal for appending. A journal for another plan, or a fresh start (`keep`
    /// empty), is rewritten with the header and `keep`.
    init(url: URL, identity: String, keep: Set<Int>) throws {
        let existing = Self.load(url, identity: identity, chunkCount: Int(UInt32.max))
        let rewrite = existing != keep || (try? Data(contentsOf: url))?.count != 72 + 8 * existing.count
        let flags = O_WRONLY | O_CREAT | O_APPEND | (rewrite ? O_TRUNC : 0)
        fd = open(url.path, flags, 0o600)
        guard fd >= 0 else { throw BCDContentError.io(errno) }
        if rewrite {
            var bytes = Self.magic + Array(identity.utf8)
            guard bytes.count == 72 else { close(fd); throw BCDContentError.badPlan("identity") }
            for index in keep.sorted() { bytes += Self.record(index) }
            do { try write(bytes) } catch { close(fd); throw error }
            fsync(fd)
        }
    }

    deinit { close(fd) }

    private static func record(_ index: Int) -> [UInt8] {
        let value = UInt32(index), check = ~value
        return (0..<4).map { UInt8(truncatingIfNeeded: value >> (8 * UInt32($0))) }
            + (0..<4).map { UInt8(truncatingIfNeeded: check >> (8 * UInt32($0))) }
    }

    private func write(_ bytes: [UInt8]) throws {
        var done = 0
        while done < bytes.count {
            let n = bytes.withUnsafeBytes { bcdWrite(fd, $0.baseAddress! + done, bytes.count - done) }
            if n < 0 {
                if errno == EINTR { continue }
                throw errno == ENOSPC ? BCDContentError.diskFull : BCDContentError.io(errno)
            }
            done += n
        }
    }

    /// Records chunks whose bytes are already synced, then syncs the journal itself.
    func append(_ chunks: [Int]) throws {
        guard !chunks.isEmpty else { return }
        try write(chunks.flatMap(Self.record))
        fsync(fd)
    }
}

/// write(2), named apart from the journal's own method.
@inline(__always) private func bcdWrite(_ fd: Int32, _ buffer: UnsafeRawPointer, _ count: Int) -> Int {
    write(fd, buffer, count)
}
