// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import CryptoKit
import Foundation
import zlib

// Checksums and zlib for the content engine, both streaming: a chunk is hashed and unpacked
// piece by piece as it arrives, so no chunk or file is ever held whole in memory.

/// A running checksum of one kind.
struct BCDHasher {
    private var md5: Insecure.MD5?
    private var sha1: Insecure.SHA1?
    private var sha256: SHA256?

    init(_ kind: BCDDigestKind) {
        switch kind {
        case .md5: md5 = Insecure.MD5()
        case .sha1: sha1 = Insecure.SHA1()
        case .sha256: sha256 = SHA256()
        }
    }

    mutating func update(_ bytes: UnsafeRawBufferPointer) {
        guard bytes.count > 0 else { return }
        md5?.update(bufferPointer: bytes)
        sha1?.update(bufferPointer: bytes)
        sha256?.update(bufferPointer: bytes)
    }

    mutating func update(_ data: Data) { data.withUnsafeBytes { update($0) } }

    func finalize() -> String {
        func hex<D: Sequence>(_ digest: D) -> String where D.Element == UInt8 {
            digest.map { String(format: "%02x", $0) }.joined()
        }
        if let md5 { return hex(md5.finalize()) }
        if let sha1 { return hex(sha1.finalize()) }
        return hex(sha256!.finalize())
    }

    static func hex(_ kind: BCDDigestKind, of data: Data) -> String {
        var hasher = BCDHasher(kind)
        hasher.update(data)
        return hasher.finalize()
    }
}

/// A zlib stream (with its header, as GOG sends it) unpacked as it arrives. The output goes to
/// `emit` in pieces of at most `outputSize` bytes; the inflater itself holds zlib's 32 KiB window.
final class BCDInflater {
    private var stream = z_stream()
    private var buffer: [UInt8]
    private(set) var ended = false
    private var open = false

    init(outputSize: Int = 256 << 10) throws {
        buffer = [UInt8](repeating: 0, count: outputSize)
        guard inflateInit_(&stream, ZLIB_VERSION, Int32(MemoryLayout<z_stream>.size)) == Z_OK else { throw BCDContentError.decode }
        open = true
    }

    deinit { if open { inflateEnd(&stream) } }

    /// Feeds compressed bytes. Bytes after the end of the stream are an error.
    func feed(_ input: UnsafeRawBufferPointer, emit: (UnsafeRawBufferPointer) throws -> Void) throws {
        guard input.count > 0 else { return }
        guard !ended, let base = input.baseAddress else { throw BCDContentError.decode }
        stream.next_in = UnsafeMutablePointer(mutating: base.assumingMemoryBound(to: Bytef.self))
        stream.avail_in = uInt(input.count)
        defer { stream.next_in = nil; stream.avail_in = 0 }
        while stream.avail_in > 0 {
            let (status, produced) = buffer.withUnsafeMutableBytes { out -> (Int32, Int) in
                stream.next_out = out.baseAddress!.assumingMemoryBound(to: Bytef.self)
                stream.avail_out = uInt(out.count)
                let status = inflate(&stream, Z_NO_FLUSH)
                return (status, out.count - Int(stream.avail_out))
            }
            if produced > 0 { try buffer.withUnsafeBytes { try emit(UnsafeRawBufferPointer(rebasing: $0[0..<produced])) } }
            switch status {
            case Z_OK:
                continue
            case Z_STREAM_END:
                ended = true
                guard stream.avail_in == 0 else { throw BCDContentError.decode }
                return
            case Z_BUF_ERROR where produced > 0:
                continue
            default:
                throw BCDContentError.decode
            }
        }
        // All input taken; zlib may still hold output for a full buffer.
        while !ended {
            let (status, produced) = buffer.withUnsafeMutableBytes { out -> (Int32, Int) in
                stream.next_out = out.baseAddress!.assumingMemoryBound(to: Bytef.self)
                stream.avail_out = uInt(out.count)
                let status = inflate(&stream, Z_NO_FLUSH)
                return (status, out.count - Int(stream.avail_out))
            }
            if produced > 0 { try buffer.withUnsafeBytes { try emit(UnsafeRawBufferPointer(rebasing: $0[0..<produced])) } }
            if status == Z_STREAM_END { ended = true; break }
            if status == Z_BUF_ERROR || produced == 0 { break }
            guard status == Z_OK else { throw BCDContentError.decode }
        }
    }

    /// The input is complete: the stream must have ended.
    func finish() throws {
        guard ended else { throw BCDContentError.decode }
    }
}

enum BCDZlib {
    /// A whole zlib stream unpacked, refusing more than `limit` bytes of output: for manifests,
    /// never for game data.
    static func inflate(_ data: Data, limit: Int) throws -> Data {
        let inflater = try BCDInflater()
        var out = Data()
        try data.withUnsafeBytes { input in
            try inflater.feed(input) { piece in
                guard out.count + piece.count <= limit else { throw BCDContentError.tooLarge }
                out.append(contentsOf: piece)
            }
        }
        try inflater.finish()
        return out
    }

    /// A store reply that is zlib, or plain JSON (it starts with '{' or '[' then).
    static func jsonData(_ data: Data, limit: Int) throws -> Data {
        guard let first = data.first(where: { $0 != 0x20 && $0 != 0x0A && $0 != 0x0D && $0 != 0x09 }) else {
            throw BCDContentError.decode
        }
        if first == UInt8(ascii: "{") || first == UInt8(ascii: "[") {
            guard data.count <= limit else { throw BCDContentError.tooLarge }
            return data
        }
        return try inflate(data, limit: limit)
    }
}
