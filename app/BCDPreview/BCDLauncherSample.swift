// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

/// The launchers' engine on sample data (BCDLaunchers.swift), for the preview app. Only the preview app compiles this file; the
/// app's engine is BCDLaunchersLive. Screens (with -BCDTab library):
///   -BCDSection launcher-battlenet | launcher-eaapp               the launcher's chip, not installed
///   -BCDSection launcher-battlenet+installed                       installed: Open
///   -BCDSection launcher-battlenet+downloading                     the installer's download, part way
///   -BCDSection launcher-battlenet+failed                          a download that failed
///   -BCDSection launcher-battlenet+starting                        Open was pressed: Starting…
///   -BCDSection launcher-eaapp+gecko                               the HTML engine's download, part way (the installer was fetched)
/// Any other section leaves the chips there with nothing installed.
@MainActor final class BCDLauncherSample: BCDLauncherEngine {
    var installed: Set<String> = []
    var failure: BCDLauncherError?
    var hangs = false
    var component = false
    private static let filterKey = "bcd.library.filter"

    func installedProgram(_ spec: BCDLauncherSpec) -> String? { installed.contains(spec.id) ? spec.programs[0] : nil }
    func sessionBlocker() -> String? { nil }

    func downloadInstaller(_ spec: BCDLauncherSpec, progress: @escaping @Sendable (Int64, Int64) -> Void) async throws {
        if let failure { throw failure }
        if hangs {
            progress(1_880_000, 4_700_880)
            try await Task.sleep(nanoseconds: 3_600_000_000_000)
        }
        progress(4_700_880, 4_700_880)
    }

    func cancelDownload() {}
    func missingComponent(_ spec: BCDLauncherSpec) -> String? { component ? "the HTML engine" : nil }
    func downloadComponent(_ spec: BCDLauncherSpec, progress: @escaping @Sendable (Int64, Int64) -> Void) async throws {
        progress(71_000_000, BCDGeckoPin.totalBytes)
        try await Task.sleep(nanoseconds: 3_600_000_000_000)
    }
    func startInstaller(_ spec: BCDLauncherSpec) { print("[preview] launcher installer \(spec.id)") }
    func startLauncher(_ spec: BCDLauncherSpec, program: String) { print("[preview] launcher open \(spec.id)") }

    /// Reads -BCDSection, puts the sample engine in the model and the chosen chip in the library's filter.
    static func prepare(_ section: String?, defaults: UserDefaults = .standard) {
        let parts = (section ?? "").split(separator: "+").map(String.init)
        let sample = BCDLauncherSample()
        let model = BCDLauncherModel.shared
        guard let first = parts.first, first.hasPrefix("launcher-"), let spec = BCDLauncherSpec.spec(String(first.dropFirst("launcher-".count))) else {
            // The filter is kept across launches: a launcher's chip from an earlier screen must not stay chosen.
            if defaults.string(forKey: filterKey)?.hasPrefix("launcher.") == true { defaults.set("all", forKey: filterKey) }
            model.engine = sample
            return
        }
        let state = parts.dropFirst().first
        if state == "installed" || state == "starting" { sample.installed.insert(spec.id) }
        if state == "failed" { sample.failure = .http(503, company: spec.company) }
        sample.hangs = state == "downloading"
        sample.component = state == "gecko"
        model.settleSeconds = 3600
        model.engine = sample
        defaults.set("launcher." + spec.id, forKey: filterKey)
        guard let state else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
            switch state {
            case "downloading", "failed", "gecko": model.install(spec)
            case "starting": model.open(spec)
            default: break
            }
        }
    }
}
