// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI

// The performance overlay: a few readings in one small text layer over the game, at most twice
// a second, on a plain dark plate (no material, nothing blurred). The live side samples the
// engine's counters (BCDSessionLive.swift) only while the overlay is on screen.

/// One reading: "60 FPS", "16.7 ms", "2104 MB"... in a colour that says whether it is fine.
struct BCDReading: Identifiable, Equatable {
    var id: String
    var text: String
    var tone: BCDSessionTone = .normal
}

/// A small switch on the developer's overlay: the frame limit, Eco, GPU sync, a frame capture.
struct BCDOverlayChip: Identifiable, Equatable {
    var id: String
    var text: String
    var tone: BCDSessionTone = .normal
    var on = false
}

struct BCDPerformanceReading: Equatable {
    var readings: [BCDReading] = []
    var chips: [BCDOverlayChip] = []
}

/// The overlay's values; the live side replaces them at most twice a second.
@MainActor final class BCDPerformanceFeed: ObservableObject {
    @Published var reading: BCDPerformanceReading
    /// Developer overlay: a tap on a chip. nil: the chips are not offered.
    var tapChip: ((String) -> Void)?
    init(_ reading: BCDPerformanceReading = BCDPerformanceReading(), tapChip: ((String) -> Void)? = nil) {
        self.reading = reading
        self.tapChip = tapChip
    }
}

/// Frames per second from the present counter's samples (time, count), oldest first: over the
/// newest stretch that holds at least three frames and a second, so the number is steady at any
/// rate (a short window would round a true 19 to steps of 5), else over all of them.
enum BCDFrameRate {
    static func fps(_ samples: [(t: Double, count: UInt64)]) -> Double {
        guard samples.count >= 2, let newest = samples.last else { return 0 }
        var oldest = samples[0]
        for sample in samples.reversed().dropFirst() {
            oldest = sample
            if newest.count &- sample.count >= 3 && newest.t - sample.t >= 1.0 { break }
        }
        let span = newest.t - oldest.t
        guard span > 0.0001, newest.count >= oldest.count else { return 0 }
        return Double(newest.count - oldest.count) / span
    }

    /// Green from 50, amber from 30, red below; grey before the first frame.
    static func tone(_ fps: Double) -> BCDSessionTone {
        if fps >= 50 { return .good }
        if fps >= 30 { return .warm }
        if fps > 0 { return .hot }
        return .soft
    }

    /// Readings packed into lines of at most `characters` characters (the plate never runs off
    /// a narrow screen and never needs to measure itself).
    static func lines(_ readings: [BCDReading], characters: Int) -> [[BCDReading]] {
        var out: [[BCDReading]] = [], line: [BCDReading] = [], used = 0
        for reading in readings {
            let width = reading.text.count + (line.isEmpty ? 0 : 3)
            if !line.isEmpty && used + width > characters {
                out.append(line); line = []; used = 0
            }
            used += line.isEmpty ? reading.text.count : width
            line.append(reading)
        }
        if !line.isEmpty { out.append(line) }
        return out
    }
}

struct BCDPerformanceOverlay: View {
    @ObservedObject var feed: BCDPerformanceFeed
    var maxWidth: CGFloat = 360
    /// The developer's narrow bar beside the game: one reading per line.
    var vertical = false

    var body: some View {
        let reading = feed.reading
        VStack(alignment: vertical ? .center : .leading, spacing: 5) {
            ForEach(Array(lines(reading).enumerated()), id: \.offset) { _, line in
                text(line)
                .font(.system(size: 12, weight: .semibold, design: .rounded).monospacedDigit())
                .lineLimit(1)
            }
            if !reading.chips.isEmpty, let tap = feed.tapChip {
                chips(reading.chips, tap: tap)
            }
        }
        .padding(.horizontal, 11).padding(.vertical, 7)
        .background(Color.black.opacity(0.62), in: RoundedRectangle(cornerRadius: 11, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 11, style: .continuous).strokeBorder(Color.white.opacity(0.08), lineWidth: 1))
        .accessibilityElement(children: .combine)
        .accessibilityLabel(reading.readings.map(\.text).joined(separator: ", "))
    }

    /// One line as one text: the readings in their colours, a soft dot between them.
    private func text(_ parts: [BCDReading]) -> Text {
        var text = Text(verbatim: "")
        for (i, part) in parts.enumerated() {
            if i > 0 { text = text + Text(verbatim: "  ·  ").foregroundColor(BCDStyle.textSoft.opacity(0.7)) }
            text = text + Text(verbatim: part.text).foregroundColor(part.tone.color)
        }
        return text
    }

    private func lines(_ reading: BCDPerformanceReading) -> [[BCDReading]] {
        if vertical { return reading.readings.map { [$0] } }
        return BCDFrameRate.lines(reading.readings, characters: max(12, Int((maxWidth - 22) / 7.4)))
    }

    @ViewBuilder private func chips(_ chips: [BCDOverlayChip], tap: @escaping (String) -> Void) -> some View {
        let layout = vertical ? AnyLayout(VStackLayout(spacing: 5)) : AnyLayout(HStackLayout(spacing: 5))
        layout {
            ForEach(chips) { chip in
                Button { tap(chip.id) } label: {
                    Text(verbatim: chip.text)
                        .font(.system(size: 11, weight: .heavy, design: .rounded).monospacedDigit())
                        .foregroundStyle(chip.on ? BCDStyle.ground : chip.tone.color)
                        .padding(.horizontal, 7).frame(minWidth: 30, minHeight: 22)
                        .background(chip.on ? AnyShapeStyle(chip.tone.color) : AnyShapeStyle(Color.clear), in: Capsule())
                        .overlay(Capsule().strokeBorder(chip.tone.color.opacity(0.8), lineWidth: 1))
                }
                .buttonStyle(.plain)
            }
        }
    }
}
