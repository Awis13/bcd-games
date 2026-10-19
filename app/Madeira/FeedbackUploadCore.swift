// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  FeedbackUploadCore.swift
//  Madeira
//
//  madeira-bcd FORK-ONLY: never in a pull request to upstream (owner, 2026-10-19).
//
//  Settings › Feedback (FeedbackUpload.swift): what a message is made of and what leaves the
//  phone. Foundation only, so tests/host/check-feedback.py compiles it alone and holds it to the
//  intake Worker's rules (bcd-logs/worker/worker.js, checkFeedback).
//
//  The message is the person's own words: nothing is taken out of them but control characters
//  (a line break stays). Beside the words go exactly four facts about the build: the BCD build
//  number, the iOS version and the device model identifier ("iPhone14,2", never the device's
//  name), and, if the person attached a log, the code Send Log answered with. No name, no
//  account, no address: the body below has no other field, and the intake refuses any other.
//

import Foundation

enum FeedbackCore {
    /// The most characters (Unicode scalars, as the intake counts them) a message holds.
    static let maxCharacters = 4000
    /// The most bytes the intake takes for one body.
    static let maxBody = 20 * 1024   // the intake's limit: 4000 characters of up to 4 UTF-8 bytes, plus the JSON
    static let kinds = ["bug", "idea", "other"]

    enum Problem: Error, Equatable {
        case empty, tooLong, bodyTooLarge, badKind
    }

    /// The person's words as they leave: line breaks (CR LF, CR) become one "\n", a tab a space,
    /// every other control character goes, and the ends are trimmed. Nothing else is touched.
    static func clean(_ text: String) -> String {
        var out = String.UnicodeScalarView()
        var previousWasReturn = false
        for scalar in text.unicodeScalars {
            defer { previousWasReturn = scalar == "\r" }
            switch scalar {
            case "\r": out.append("\n")
            case "\n": if !previousWasReturn { out.append("\n") }
            case "\t": out.append(" ")
            default: if scalar.properties.generalCategory != .control { out.append(scalar) }
            }
        }
        return String(out).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// How many characters the intake counts in a message.
    static func count(_ text: String) -> Int { text.unicodeScalars.count }

    /// The message cut to the most it holds (the text field never lets it grow past).
    static func limited(_ text: String) -> String {
        count(text) <= maxCharacters ? text : String(String.UnicodeScalarView(text.unicodeScalars.prefix(maxCharacters)))
    }

    /// The first line that says something, at most `width` characters, for the list of what was sent.
    static func firstLine(_ text: String, width: Int = 80) -> String {
        let line = clean(text).split(separator: "\n", omittingEmptySubsequences: true)
            .first.map { $0.trimmingCharacters(in: .whitespaces) } ?? ""
        return line.count <= width ? line : String(line.prefix(width - 1)) + "…"
    }

    /// The model identifier, or "unknown" for anything that does not look like one (a device's
    /// name must never travel by this road).
    static func model(_ machine: String) -> String {
        machine.range(of: #"^[A-Za-z]{2,12}[0-9]{1,3},[0-9]{1,3}$"#, options: .regularExpression) != nil ? machine : "unknown"
    }

    /// The build number as the Info.plist has it; "0" when it is empty or odd.
    static func build(_ raw: String) -> String {
        raw.range(of: #"^[0-9A-Za-z._-]{1,20}$"#, options: .regularExpression) != nil ? raw : "0"
    }

    /// A log code as Send Log's intake makes them.
    static func isLogCode(_ code: String) -> Bool {
        code.range(of: #"^[0-9A-HJKMNP-TV-Z]{6}$"#, options: .regularExpression) != nil
    }

    /// The JSON the intake takes: kind, text, build, ios, model and, when a log was attached, log.
    static func body(kind: String, text: String, build: String, ios: String, model: String, log: String? = nil) throws -> Data {
        guard kinds.contains(kind) else { throw Problem.badKind }
        let words = clean(text)
        guard !words.isEmpty else { throw Problem.empty }
        guard count(words) <= maxCharacters else { throw Problem.tooLong }
        var fields = ["kind": kind, "text": words, "build": Self.build(build), "ios": ios, "model": Self.model(model)]
        if let log, isLogCode(log) { fields["log"] = log }
        let data = try JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys, .withoutEscapingSlashes])
        guard data.count <= maxBody else { throw Problem.bodyTooLarge }
        return data
    }
}
