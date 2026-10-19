// SPDX-License-Identifier: GPL-3.0-or-later
// Madeira Converter Exception: see LICENSE-EXCEPTION.md

import Foundation

/// The private NSExtension API (ExtensionFoundation) that starts an app extension of
/// Madeira's own: the same calls LiveContainer uses to start its LiveProcess.
@objc private protocol NSExtensionClassShim {
    @objc(extensionWithIdentifier:error:)
    func `extension`(withIdentifier identifier: String) throws -> AnyObject
}

@objc private protocol NSExtensionShim {
    @objc(beginExtensionRequestWithInputItems:completion:)
    func beginExtensionRequest(withInputItems items: [Any], completion: @escaping (NSUUID?) -> Void)
    @objc(pidForRequestIdentifier:)
    func pid(forRequestIdentifier identifier: NSUUID) -> Int32
    @objc(setRequestCompletionBlock:)
    func setRequestCompletionBlock(_ block: @escaping (NSUUID?, [Any]?) -> Void)
    @objc(setRequestCancellationBlock:)
    func setRequestCancellationBlock(_ block: @escaping (NSUUID?, NSError?) -> Void)
    @objc(setRequestInterruptionBlock:)
    func setRequestInterruptionBlock(_ block: @escaping (NSUUID?) -> Void)
}

/// Starts the separate helper process required to debug Madeira without
/// deadlocking Madeira itself.
///
/// The helper is a classic app extension (PlugIns/MadeiraJITHelper.appex, on
/// com.apple.ar.viewer as LiveContainer's LiveProcess is), started by the bundle ID it
/// has in this installation. Sideloaders that sign with the user's own Apple ID rename
/// Madeira's bundle ID and the helper's along with it (SideStore, AltStore and Plume
/// append the team ID), and that is all this lookup needs. An ExtensionKit extension
/// point, which the helper used before, is named in files sideloaders do not rewrite, so
/// iOS registered none for a renamed Madeira ("Failed to add observer").
///
/// One extension request per MadeiraJITRequest: the request goes as JSON in the input
/// item's userInfo, and the helper completes the request with the JSON response in the
/// returned item's userInfo. Log tag: [jit-helper].
@MainActor
enum MadeiraBuiltInJIT {
    static let helperFile = "MadeiraJITHelper.appex"

    /// Requests that have started and not finished, kept alive until they do.
    private static var running: [HelperRequest] = []

    static var unavailableReason: String? {
#if targetEnvironment(simulator)
        return "Built-in JIT works only on a real device, not in the simulator."
#else
        guard #available(iOS 26.0, *) else {
            return "iOS 26 or later is needed for Built-in JIT."
        }
        if getenv("LC_HOME_PATH") != nil {
            return "Built-in JIT can't run while BCD is inside LiveContainer. Use StikDebug there instead."
        }
        return nil
#endif
    }

    static var isAvailable: Bool { unavailableReason == nil }

    /// The helper's bundle ID in this installation, read from its own Info.plist; nil when
    /// a sideloader left it out.
    static var helperIdentifier: String? {
        guard let url = Bundle.main.builtInPlugInsURL?.appendingPathComponent(helperFile) else { return nil }
        return Bundle(url: url)?.bundleIdentifier
    }

    static func send(_ request: MadeiraJITRequest,
                     started: @escaping () -> Void = {},
                     completion: @escaping (Result<MadeiraJITRequest.Response, Error>) -> Void) {
        func fail(_ code: Int, _ message: String) {
            LogStore.shared.log("[jit-helper] \(message)", level: .error)
            completion(.failure(NSError(domain: "MadeiraBuiltInJIT", code: code,
                                        userInfo: [NSLocalizedDescriptionKey: message])))
        }
        guard unavailableReason == nil else {
            fail(1, unavailableReason ?? "Built-in JIT can't be used on this device.")
            return
        }
        guard let identifier = helperIdentifier else {
            fail(2, "This copy of BCD has no JIT helper. Install BCD again, and if your sideloader asks about app extensions, keep them.")
            return
        }
        if NSClassFromString("NSExtension") == nil {
            dlopen("/System/Library/Frameworks/ExtensionFoundation.framework/ExtensionFoundation", RTLD_NOW)
        }
        guard let extensionClass = NSClassFromString("NSExtension") else {
            fail(3, "The JIT helper can't start: iOS is missing the extension API it relies on.")
            return
        }
        let factory = unsafeBitCast(extensionClass as AnyObject, to: NSExtensionClassShim.self)
        let found: AnyObject
        do { found = try factory.extension(withIdentifier: identifier) }
        catch {
            fail(2, "iOS couldn't find BCD's JIT helper (\(identifier)): \(error.localizedDescription) Install BCD again to fix this.")
            return
        }
        guard let data = try? JSONEncoder().encode(request) else {
            fail(4, "BCD couldn't package the request for its JIT helper.")
            return
        }
        let helper = HelperRequest(extension: found, operation: request.operation.rawValue, completion: completion)
        running.append(helper)
        helper.begin(data: data, identifier: identifier, started: started)
    }

    fileprivate static func finished(_ helper: HelperRequest) {
        running.removeAll { $0 === helper }
    }
}

/// One request to the helper, from its start to its one result.
@MainActor
private final class HelperRequest {
    private let shim: NSExtensionShim
    private let `extension`: AnyObject   // the NSExtension, kept alive for the request
    private let operation: String
    private var completion: ((Result<MadeiraJITRequest.Response, Error>) -> Void)?

    init(extension found: AnyObject, operation: String,
         completion: @escaping (Result<MadeiraJITRequest.Response, Error>) -> Void) {
        self.extension = found
        self.shim = unsafeBitCast(found, to: NSExtensionShim.self)
        self.operation = operation
        self.completion = completion
    }

    func begin(data: Data, identifier: String, started: @escaping () -> Void) {
        // The blocks can arrive on any queue; everything below runs on the main thread.
        shim.setRequestCompletionBlock { [weak self] _, items in
            let info = (items?.first as? NSExtensionItem)?.userInfo
            let payload = info?[MadeiraJITRequest.Response.itemKey] as? Data
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.returned(payload) } }
        }
        shim.setRequestCancellationBlock { [weak self] _, error in
            let message = error?.localizedDescription ?? "no reason given"
            DispatchQueue.main.async { MainActor.assumeIsolated {
                self?.end(.failure(Self.error(5, "The JIT helper's request was canceled: \(message)")))
            } }
        }
        shim.setRequestInterruptionBlock { [weak self] _ in
            DispatchQueue.main.async { MainActor.assumeIsolated {
                self?.end(.failure(Self.error(6, "The JIT helper quit without warning. Give it another try.")))
            } }
        }
        let item = NSExtensionItem()
        item.userInfo = [MadeiraJITRequest.itemKey: data]
        shim.beginExtensionRequest(withInputItems: [item]) { [weak self] uuid in
            DispatchQueue.main.async { MainActor.assumeIsolated {
                guard let self else { return }
                guard let uuid else {
                    self.end(.failure(Self.error(7, "The JIT helper couldn't be started. Install BCD again, and if your sideloader asks about app extensions, keep them.")))
                    return
                }
                LogStore.shared.log("[jit-helper] \(self.operation): started \(identifier) as pid \(self.shim.pid(forRequestIdentifier: uuid))")
                started()
            } }
        }
    }

    private func returned(_ payload: Data?) {
        guard let payload, let response = try? JSONDecoder().decode(MadeiraJITRequest.Response.self, from: payload) else {
            end(.failure(Self.error(8, "The JIT helper ended without sending a result.")))
            return
        }
        LogStore.shared.log("[jit-helper] \(operation): finished success=\(response.success ? 1 : 0)")
        end(.success(response))
    }

    private func end(_ result: Result<MadeiraJITRequest.Response, Error>) {
        guard let completion else { return }   // the first result counts
        self.completion = nil
        if case .failure(let error) = result {
            LogStore.shared.log("[jit-helper] \(operation): \(error.localizedDescription)", level: .error)
        }
        MadeiraBuiltInJIT.finished(self)
        completion(result)
    }

    private static func error(_ code: Int, _ message: String) -> NSError {
        NSError(domain: "MadeiraBuiltInJIT", code: code, userInfo: [NSLocalizedDescriptionKey: message])
    }
}
