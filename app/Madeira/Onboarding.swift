// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 125hz
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
// Modified by BCD (thebcd69), 2026-10-18: the setup screens moved to BCDSetupView.swift with BCD's own
// look and wording; Settings › Steam reworded.

import SwiftUI
import UIKit
import UniformTypeIdentifiers

// First-run setup for JIT, Steam sign-in, Madeira Dock and Wine Mono (docs/LIBRARY.md).
// On a new install, and once after an update that raises the setup revision
// (`madeiraOnboardingRevision` in UserDefaults, which iOS removes with the app,
// is below OnboardingRules.revision), the library opens a full-screen setup: welcome, JIT,
// Steam sign-in, Valve's client components for Madeira Dock (only when Dock is
// available), Wine Mono (only when this device has none), done. After an update, only the
// pages added since the revision this device last saw are shown. Every step can be
// skipped. Settings › JIT or Settings › Steam can reopen it in full.
// env.MADEIRA_ONBOARDING = 0 never opens it.
//
// The JIT page offers three ways in (in-app pairing on iOS 27, a pairing
// file from a computer, StikDebug), each with its own numbered steps. It stores
// only the chosen method and, when selected, pairs on this device through
// OnDevicePairing or imports the pairing file through JITCoordinator.
// Sign-in goes through SteamSignIn (the
// token stays in its Keychain store) and the components through
// MadeiraDockModel.prepareClient(), which downloads and verifies files without
// starting Wine. Setup starts no Wine session and changes no JIT pool, engine
// switch or launch configuration.
// Log tag: [onboarding] (no account names, tokens or paths).

// MARK: - Rules (Foundation and MadeiraConfig only; tests/host/check-onboarding.py compiles this part)

enum OnboardingRules {
    /// The setup revision this device last finished or skipped. Raise `revision` in a
    /// release whose setup every existing install should see once.
    static let revisionKey = "madeiraOnboardingRevision"
    /// 1 was the first setup, stored as `madeiraOnboardingDone` (no longer read); 2 adds
    /// Install LocalDevVPN, on-device pairing and the Madeira JIT shortcut; 3 adds the
    /// Wine Mono download; 4 adds the Visual C++ runtime download (builds without
    /// Microsoft's DLLs: BCD's public ones).
    static let revision = 4

    /// `env.MADEIRA_ONBOARDING = 0` (bcd.cfg or the environment) never opens
    /// setup and hides "Run setup again". On by default.
    static var enabled: Bool { MadeiraConfig.flag("MADEIRA_ONBOARDING") }

    enum Step: String, CaseIterable {
        case welcome, localDevVPN = "localdevvpn", jit, signIn = "sign-in", dockClient = "dock-client",
             wineMono = "wine-mono", vcRuntime = "vc-runtime", done

        /// The revision whose setup first had this page.
        var introduced: Int {
            switch self {
            case .localDevVPN: return 2
            case .wineMono: return 3
            case .vcRuntime: return 4
            default: return 1
            }
        }
    }

    /// LocalDevVPN comes first when it is not installed (`localDevVPN`): every JIT way
    /// reaches this device through it. JIT is always offered. Sign-in is offered when
    /// Steam sign-in is enabled, or when Madeira Dock is available (Dock needs a
    /// sign-in). Valve's client components are offered only when Dock is available,
    /// Wine Mono only when this device has none (`wineMono`), the Visual C++ runtime only
    /// when neither the build nor a download provides it (`vcRuntime`). With `since` (the
    /// revision this device last saw, after an update) only the pages added after it are offered.
    static func steps(signIn: Bool, dock: Bool, localDevVPN: Bool = false, wineMono: Bool = false,
                      vcRuntime: Bool = false, since: Int = 0) -> [Step] {
        var list: [Step] = [.welcome]
        if localDevVPN { list.append(.localDevVPN) }
        list.append(.jit)
        if signIn || dock { list.append(.signIn) }
        if dock { list.append(.dockClient) }
        if wineMono { list.append(.wineMono) }
        if vcRuntime { list.append(.vcRuntime) }
        list.append(.done)
        return since > 0 ? list.filter { $0 == .welcome || $0 == .done || $0.introduced > since } : list
    }

    /// Whether there is anything to set up between the welcome and done pages.
    static func hasSetup(_ steps: [Step]) -> Bool {
        steps.contains(.jit) || steps.contains(.signIn) || steps.contains(.dockClient) || steps.contains(.wineMono)
            || steps.contains(.vcRuntime)
    }

    /// Whether setup opens by itself when the library appears.
    static func shouldShow(seen: Int, enabled: Bool, steps: [Step]) -> Bool {
        enabled && seen < revision && hasSetup(steps)
    }

    /// The page after `step`, or nil when setup is finished.
    static func next(after step: Step, in steps: [Step]) -> Step? {
        guard let index = steps.firstIndex(of: step), index + 1 < steps.count else { return nil }
        return steps[index + 1]
    }

    /// "Step n of m" for the pages between welcome and done; nil on those two.
    static func position(of step: Step, in steps: [Step]) -> (number: Int, count: Int)? {
        let middle = steps.filter { $0 != .welcome && $0 != .done }
        guard let index = middle.firstIndex(of: step) else { return nil }
        return (index + 1, middle.count)
    }
}

// MARK: - Setup model

@MainActor final class OnboardingModel: ObservableObject {
    static let shared = OnboardingModel()
    typealias Step = OnboardingRules.Step

    @Published var presented = false
    @Published private(set) var step: Step = .welcome
    /// Considered once per app run, when the library first appears.
    private var considered = false

    static var enabled: Bool { OnboardingRules.enabled }
    /// 0 on a new install, and on one that finished setup before revisions (revision 1).
    static var seen: Int { UserDefaults.standard.integer(forKey: OnboardingRules.revisionKey) }

    /// LocalDevVPN was missing when setup opened, so its page is offered. Fixed for that
    /// run of setup: installing it on the way does not renumber the steps.
    private var offerLocalDevVPN = false
    /// The same for Wine Mono: offered when this device had none when setup opened.
    private var offerWineMono = !WineMonoModel.available
    /// And the Visual C++ runtime: offered when neither the build nor a download has it.
    private var offerVCRuntime = !VCRuntime.shared.isAvailable
    /// The revision this device had seen when setup opened by itself after an update (only
    /// the pages added since are shown); 0 for a new install and for Run setup again.
    @Published private(set) var since = 0
    var steps: [Step] { steps(since: since) }
    private func steps(since: Int) -> [Step] {
        OnboardingRules.steps(signIn: SteamSignIn.isEnabled, dock: MadeiraDock.enabled, localDevVPN: offerLocalDevVPN,
                              wineMono: offerWineMono, vcRuntime: offerVCRuntime, since: since)
    }
    /// Setup can be opened: enabled, and something to set up.
    var available: Bool { Self.enabled && OnboardingRules.hasSetup(steps(since: 0)) }

    private init() {}

    /// The library appeared: open setup once on a new install, and once after an update
    /// that raised the setup revision.
    func presentIfNeeded() {
        guard !considered else { return }
        considered = true
        offerWineMono = !WineMonoModel.available
        offerVCRuntime = !VCRuntime.shared.isAvailable
        guard OnboardingRules.shouldShow(seen: Self.seen, enabled: Self.enabled, steps: steps(since: Self.seen)) else { return }
        open(reason: "revision \(Self.seen)->\(OnboardingRules.revision)", since: Self.seen)
    }

    /// Settings › Steam › Run setup again.
    func rerun() { open(reason: "settings", since: 0) }

    private func open(reason: String, since: Int) {
        // Never over a running session.
        guard available, LibraryModel.shared.current == nil, wine_process_is_running() == 0 else { return }
        offerLocalDevVPN = !LocalDevVPN.isInstalled
        offerWineMono = !WineMonoModel.available
        offerVCRuntime = !VCRuntime.shared.isAvailable
        self.since = since
        LogStore.shared.log("[onboarding] shown reason=\(reason) steps=\(steps.map(\.rawValue).joined(separator: ","))")
        go(.welcome)
        presented = true
    }

    func go(_ next: Step) {
        step = next
        LogStore.shared.log("[onboarding] step=\(next.rawValue)")
    }

    func next() {
        guard let following = OnboardingRules.next(after: step, in: steps) else { finish(); return }
        go(following)
    }

    /// "Skip setup" on the welcome page: done, and not shown again.
    func skip() {
        UserDefaults.standard.set(OnboardingRules.revision, forKey: OnboardingRules.revisionKey)
        LogStore.shared.log("[onboarding] skipped")
        close()
    }

    func finish() {
        UserDefaults.standard.set(OnboardingRules.revision, forKey: OnboardingRules.revisionKey)
        LogStore.shared.log("[onboarding] done")
        close()
    }

    private func close() {
        EndedSessionSurface.hide(reason: "setup-closed")
        presented = false
    }
}

// MARK: - Setup screens

// BCD's setup screens (OnboardingView) are in BCDSetupView.swift since 2026-10-18.

// MARK: - Madeira Dock in the library

extension LibraryEntry {
    static let dockSessionID = UUID(uuidString: "5D0C4A3E-2B7F-4C61-9E0A-6B1D2F3C4E5A")!

    /// A Dock start from the library runs as a library session (full-screen
    /// view, starting screen, in-game menu, one session per run) with this
    /// entry. It is a desktop session: explorer's virtual desktop runs the
    /// host. It is never saved to the library.
    static func dockSession(title: String, width: Int, height: Int) -> LibraryEntry {
        var entry = LibraryEntry(title: title.isEmpty ? "BCD Dock" : title, relativePath: "windows/system32/explorer.exe", bits: 64)
        entry.id = dockSessionID; entry.desktop = true; entry.resolution = "\(width)x\(height)"
        entry.graphicsAPI = "BCD Dock"
        return entry
    }
}
