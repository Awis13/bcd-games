// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

// The touch controls' named layouts and their part of an in-game menu, in BCD's look
// (docs/BCD-UI-ARCHITECTURE.md). Layouts are kept as they always were (the built-in full
// controller in code, the player's own in bcd-control-presets.json); BCDTouchLive lists them and
// carries out every choice through the engine's layout store, so nothing about them changes on
// disk.
//
// For an in-game menu: `BCDTouchMenuSection(state:actions:opened:)`, with BCDTouchLive's state
// and actions in the app (`BCDTouchMenuSectionLive(closeMenu:)` does that wiring) and
// BCDTouchPreviewModel in the preview app. There a controller reaches every row: each is
// `.bcdFocusable("menu.touch.<thing>")`, and left and right step the sliders (BCDFocusSteppers).
// Elsewhere (the editor, the classic menu's row) `focusPrefix` is nil and nothing is focusable.
// These views are drawn over a running game: no animation, no blur.

// MARK: - What a list of layouts shows

struct BCDTouchLayouts: Equatable {
    struct Entry: Identifiable, Equatable {
        var id: String
        var name: String
        var builtIn: Bool
    }

    var entries: [Entry] = []
    var activeID: String?
    /// What is on screen is in no layout: switching away loses it unless it is kept first.
    var unsaved = false
    /// The layouts file could not be read: nothing can be saved, made or deleted.
    var readOnly = false
    /// The name keeping what is on screen gives it ("Custom Layout 2").
    var nextName = "Custom Layout 1"
    /// Layouts are offered at all (env.MADEIRA_CONTROL_PRESETS is not 0).
    var enabled = true

    var active: Entry? { entries.first { $0.id == activeID } }
    /// The name shown for what is on screen.
    var activeName: String { active?.name ?? (unsaved ? "Not saved" : "None") }
}

@MainActor protocol BCDTouchLayoutActions: AnyObject {
    /// Put this layout on screen; `keepCurrentFirst` saves what is on screen as a new layout first.
    func chooseLayout(_ id: String, keepCurrentFirst: Bool)
    /// A new, empty layout, opened in the editor.
    func newLayout(keepCurrentFirst: Bool)
    func deleteLayout(_ id: String)
}

// MARK: - The list

/// The layouts, the active one marked; tapping one puts it on screen (asking first when what is
/// on screen would be lost), plus New layout, Delete for the active one of the player's own, and
/// in the editor Save as new layout. Its questions are cards in the list, not system alerts.
struct BCDTouchLayoutList: View {
    let layouts: BCDTouchLayouts
    let actions: BCDTouchLayoutActions
    /// The editor's "Save as new layout"; nil hides it.
    var saveAsNew: (() -> Void)? = nil
    /// Called after a layout was chosen, made or deleted; true when the editor opened.
    var done: ((_ openedEditor: Bool) -> Void)? = nil
    /// Where a controller drives the focus (an in-game menu): the rows' ids start with it.
    var focusPrefix: String? = nil

    private enum Question: Equatable {
        case keep(then: Then)
        case delete(BCDTouchLayouts.Entry)
    }
    private enum Then: Equatable { case choose(String), create }
    @State private var question: Question?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let question {
                card(question)
            } else {
                if let focusPrefix { BCDTouchFocusClaim(id: focusPrefix + ".layout", onlyIfLost: true) }
                ForEach(layouts.entries) { entry in row(entry) }
                if layouts.entries.isEmpty {
                    Text("No layouts yet.")
                        .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                }
                if !layouts.readOnly {
                    HStack(spacing: 8) {
                        BCDTouchPanelButton(title: "New layout", symbol: "plus") { ask(.create) }
                            .bcdTouchFocus(focusPrefix, "layout.new", radius: 21) { ask(.create) }
                        if let saveAsNew {
                            BCDTouchPanelButton(title: "Save as new", symbol: "square.and.arrow.down") { saveAsNew() }
                                .bcdTouchFocus(focusPrefix, "layout.save", radius: 21) { saveAsNew() }
                        }
                    }
                    if let active = layouts.active, !active.builtIn {
                        BCDTouchPanelButton(title: "Delete \u{201C}\(active.name)\u{201D}", symbol: "trash", role: .destructive) {
                            question = .delete(active)
                        }
                        .bcdTouchFocus(focusPrefix, "layout.delete", radius: 21) { question = .delete(active) }
                    }
                } else {
                    Text("The layouts file could not be read, so nothing is saved to it. It is left as it is.")
                        .font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .transaction { $0.animation = nil }
    }

    private func row(_ entry: BCDTouchLayouts.Entry) -> some View {
        let on = entry.id == layouts.activeID
        return Button {
            guard !on else { return }
            ask(.choose(entry.id))
        } label: {
            HStack(spacing: 10) {
                Image(systemName: on ? "largecircle.fill.circle" : "circle")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(on ? BCDStyle.fuchsia : BCDStyle.textSoft)
                Text(entry.name)
                    .font(BCDStyle.label(15)).foregroundStyle(BCDStyle.text)
                    .lineLimit(1).truncationMode(.middle)
                Spacer(minLength: 4)
                if entry.builtIn {
                    Text("BUILT IN")
                        .font(.system(size: 10, weight: .heavy, design: .rounded)).kerning(0.8)
                        .foregroundStyle(BCDStyle.lavender)
                        .padding(.horizontal, 7).padding(.vertical, 3)
                        .overlay(Capsule().stroke(BCDStyle.lavender.opacity(0.5), lineWidth: 1))
                }
            }
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(on ? BCDStyle.surfaceRaised : BCDStyle.ground.opacity(0.55)))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(on ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.stroke), lineWidth: on ? 1.5 : 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(BCDTouchPressStyle())
        .accessibilityAddTraits(on ? .isSelected : [])
        .bcdTouchFocus(focusPrefix, "layout." + entry.id, radius: 12) { if !on { ask(.choose(entry.id)) } }
    }

    /// Asks first when what is on screen is in no layout.
    private func ask(_ then: Then) {
        BCDTouchHaptic.tap()
        if layouts.unsaved { question = .keep(then: then) } else { perform(then, keep: false) }
    }

    private func perform(_ then: Then, keep: Bool) {
        question = nil
        switch then {
        case .choose(let id):
            actions.chooseLayout(id, keepCurrentFirst: keep)
            done?(false)
        case .create:
            actions.newLayout(keepCurrentFirst: keep)
            done?(true)
        }
    }

    private func delete(_ entry: BCDTouchLayouts.Entry) {
        question = nil
        actions.deleteLayout(entry.id)
        done?(false)
    }

    @ViewBuilder private func card(_ q: Question) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            switch q {
            case .keep(let then):
                Text("Keep what\u{2019}s on screen?").font(BCDStyle.heading(17)).foregroundStyle(BCDStyle.text)
                Text("These controls are not saved in a layout yet. Switching replaces them.")
                    .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
                if let focusPrefix { BCDTouchFocusClaim(id: focusPrefix + (layouts.readOnly ? ".confirm.replace" : ".confirm.keep")) }
                if !layouts.readOnly {
                    BCDTouchPanelButton(title: "Keep as \u{201C}\(layouts.nextName)\u{201D}", symbol: "square.and.arrow.down",
                                        prominent: true) { perform(then, keep: true) }
                        .bcdTouchFocus(focusPrefix, "confirm.keep", radius: 21) { perform(then, keep: true) }
                }
                BCDTouchPanelButton(title: "Replace them", symbol: "arrow.triangle.2.circlepath", role: .destructive) {
                    perform(then, keep: false)
                }
                .bcdTouchFocus(focusPrefix, "confirm.replace", radius: 21) { perform(then, keep: false) }
                BCDTouchPanelButton(title: "Cancel", symbol: nil) { question = nil }
                    .bcdTouchFocus(focusPrefix, "confirm.cancel", radius: 21) { question = nil }
            case .delete(let entry):
                Text("Delete \u{201C}\(entry.name)\u{201D}?").font(BCDStyle.heading(17)).foregroundStyle(BCDStyle.text)
                Text("The controls on screen stay until you pick another layout. This can\u{2019}t be undone.")
                    .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
                if let focusPrefix { BCDTouchFocusClaim(id: focusPrefix + ".confirm.cancel") }
                BCDTouchPanelButton(title: "Delete", symbol: "trash", role: .destructive, prominent: true) {
                    delete(entry)
                }
                .bcdTouchFocus(focusPrefix, "confirm.delete", radius: 21) { delete(entry) }
                BCDTouchPanelButton(title: "Cancel", symbol: nil) { question = nil }
                    .bcdTouchFocus(focusPrefix, "confirm.cancel", radius: 21) { question = nil }
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(BCDStyle.surfaceRaised))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(BCDStyle.glow, lineWidth: 1.2))
    }
}

// MARK: - An in-game menu's touch controls

/// What the touch controls' part of a menu shows.
struct BCDTouchMenuState: Equatable {
    var visible = true
    var layouts = BCDTouchLayouts()
    /// The controls' opacity in play (a library game's own, or the shared one).
    var opacity = 0.7
    var opacityRange: ClosedRange<Double> = 0.15...1
    /// The whole layout's size (a library game's Control size); nil where it is not offered.
    var layoutScale: Double? = 1
}

@MainActor protocol BCDTouchMenuActions: BCDTouchLayoutActions {
    func setVisible(_ on: Bool)
    func setOpacity(_ value: Double)
    func setLayoutScale(_ value: Double)
    /// Shows the controls and opens the editor over the game.
    func editControls()
}

/// Touch controls on or off, the layout, opacity, size and the editor: the in-game menu's
/// section. `opened` is called when the editor opens (Edit controls, New layout): the menu
/// closes then, or it would hide the controls being edited.
struct BCDTouchMenuSection: View {
    let state: BCDTouchMenuState
    let actions: BCDTouchMenuActions
    let opened: () -> Void
    /// The rows' focus ids start with this ("menu.touch"); nil where no controller drives a
    /// focus (no BCDFocus in the environment).
    let focusPrefix: String?
    @State private var layoutsOpen: Bool

    /// `layoutsOpen`: the layout list shown from the start.
    init(state: BCDTouchMenuState, actions: BCDTouchMenuActions, opened: @escaping () -> Void, layoutsOpen: Bool = false,
         focusPrefix: String? = "menu.touch") {
        self.state = state
        self.actions = actions
        self.opened = opened
        self.focusPrefix = focusPrefix
        _layoutsOpen = State(initialValue: layoutsOpen)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Toggle(isOn: Binding(get: { state.visible }, set: { actions.setVisible($0) })) {
                Label("Touch controls", systemImage: "hand.tap")
                    .font(BCDStyle.label(16)).foregroundStyle(BCDStyle.text)
            }
            .tint(BCDStyle.fuchsia)
            .bcdTouchFocus(focusPrefix, "visible", radius: 12) { actions.setVisible(!state.visible) }
            if state.visible {
                if state.layouts.enabled {
                    BCDTouchLayoutRow(layouts: state.layouts, actions: actions, open: $layoutsOpen, done: { editor in
                        if editor { opened() }
                    }, focusPrefix: focusPrefix)
                }
                BCDTouchSliderRow(title: "Opacity", value: state.opacity, range: state.opacityRange,
                                  text: "\(Int((state.opacity * 100).rounded())) %") { actions.setOpacity($0) }
                    .bcdTouchStepper(focusPrefix, "opacity", value: state.opacity) { step in
                        let r = state.opacityRange
                        actions.setOpacity(min(max(state.opacity + Double(step) * 0.05, r.lowerBound), r.upperBound))
                    }
                if let size = state.layoutScale {
                    BCDTouchSliderRow(title: "Size", value: size, range: 0.5...2,
                                      text: String(format: "%.1f\u{00D7}", size)) { actions.setLayoutScale($0) }
                        .bcdTouchStepper(focusPrefix, "size", value: size) { step in
                            actions.setLayoutScale(min(max(size + Double(step) * 0.1, 0.5), 2))
                        }
                }
                BCDTouchPanelButton(title: "Edit controls", symbol: "slider.horizontal.3", prominent: true) { edit() }
                    .bcdTouchFocus(focusPrefix, "edit", radius: 21) { edit() }
            }
        }
        .transaction { $0.animation = nil }
    }

    private func edit() {
        actions.editControls()
        opened()
    }
}

/// "Layout: <name>", opening the list below it.
struct BCDTouchLayoutRow: View {
    let layouts: BCDTouchLayouts
    let actions: BCDTouchLayoutActions
    @Binding var open: Bool
    var done: ((_ openedEditor: Bool) -> Void)? = nil
    var focusPrefix: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { BCDTouchHaptic.tap(); open.toggle() } label: {
                HStack(spacing: 8) {
                    Text("Layout").font(BCDStyle.label(16)).foregroundStyle(BCDStyle.text)
                    Spacer(minLength: 8)
                    Text(layouts.activeName)
                        .font(BCDStyle.body(15)).foregroundStyle(BCDStyle.lavender)
                        .lineLimit(1).truncationMode(.middle)
                    Image(systemName: open ? "chevron.up" : "chevron.down")
                        .font(.system(size: 13, weight: .bold)).foregroundStyle(BCDStyle.lavender)
                }
                .frame(minHeight: 40)
                .contentShape(Rectangle())
            }
            .buttonStyle(BCDTouchPressStyle())
            .accessibilityLabel("Layout, \(layouts.activeName)")
            .bcdTouchFocus(focusPrefix, "layout", radius: 12) { open.toggle() }
            if open {
                BCDTouchLayoutList(layouts: layouts, actions: actions, done: { editor in
                    open = false
                    done?(editor)
                }, focusPrefix: focusPrefix)
            } else if let focusPrefix {
                // The list closed under the ring: it comes back to this row.
                BCDTouchFocusClaim(id: focusPrefix + ".layout", onlyIfLost: true)
            }
        }
    }
}

// MARK: - Shared pieces

/// A labelled slider with its value.
struct BCDTouchSliderRow: View {
    let title: String
    let value: Double
    let range: ClosedRange<Double>
    let text: String
    let set: (Double) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(title).font(BCDStyle.label(15)).foregroundStyle(BCDStyle.text)
                Spacer()
                Text(text).font(BCDStyle.body(14).monospacedDigit()).foregroundStyle(BCDStyle.textSoft)
            }
            Slider(value: Binding(get: { min(max(value, range.lowerBound), range.upperBound) }, set: set), in: range)
                .tint(BCDStyle.fuchsia)
                .accessibilityLabel(title)
                .accessibilityValue(text)
        }
    }
}

/// A full-width button in a panel: outlined, filled with the logo's light when prominent, in
/// the alert colour when it removes something. A press changes colour only.
struct BCDTouchPanelButton: View {
    let title: String
    let symbol: String?
    var role: ButtonRole? = nil
    var prominent = false
    let action: () -> Void

    var body: some View {
        Button(role: role) { BCDTouchHaptic.tap(); action() } label: {
            HStack(spacing: 8) {
                if let symbol { Image(systemName: symbol).font(.system(size: 14, weight: .bold)) }
                Text(title).font(BCDStyle.label(15)).lineLimit(1).minimumScaleFactor(0.7)
            }
            .foregroundStyle(role == .destructive && !prominent ? BCDStyle.alert : (prominent ? Color.white : BCDStyle.text))
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity, minHeight: 42)
            .background {
                if prominent {
                    Capsule().fill(role == .destructive ? AnyShapeStyle(BCDStyle.alert.opacity(0.85)) : AnyShapeStyle(BCDStyle.glow))
                } else {
                    Capsule().fill(BCDStyle.ground.opacity(0.55))
                }
            }
            .overlay(Capsule().stroke(prominent ? Color.clear : BCDStyle.stroke, lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(BCDTouchPressStyle())
    }
}

/// Pressed: a little dimmer, at once (never animated: this is drawn over a running game).
struct BCDTouchPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.6 : 1)
            .transaction { $0.animation = nil }
    }
}

/// The light tick a touch control or a chrome button gives.
enum BCDTouchHaptic {
    private static let generator = UIImpactFeedbackGenerator(style: .light)
    static func tap() { generator.impactOccurred() }
}

/// A panel over the game: an opaque BCD surface (no blur behind it), hairline rim.
struct BCDTouchPanelBackground: View {
    var body: some View {
        RoundedRectangle(cornerRadius: 20, style: .continuous)
            .fill(BCDStyle.surface.opacity(0.97))
            .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(BCDStyle.stroke, lineWidth: 1))
    }
}

// MARK: - Controller focus (in an in-game menu)

/// A value under the controller's ring that left and right step instead of moving the ring (a
/// slider). BCDFocus.move asks here first.
@MainActor enum BCDFocusSteppers {
    private static var steppers: [String: (Int) -> Void] = [:]

    static func set(_ id: String, _ stepper: ((Int) -> Void)?) { steppers[id] = stepper }

    /// Steps the value `id` names (-1 left, +1 right); false when it is not a value or the
    /// direction is up or down.
    static func step(_ id: String?, _ direction: BCDMove) -> Bool {
        guard direction == .left || direction == .right, let id, let stepper = steppers[id] else { return false }
        stepper(direction == .left ? -1 : 1)
        return true
    }
}

extension View {
    /// Reachable by the controller where `prefix` is given (a BCDFocus is in the environment):
    /// `.bcdFocusable("<prefix>.<name>")`, scrolled to by that id. Elsewhere nothing changes.
    @ViewBuilder func bcdTouchFocus(_ prefix: String?, _ name: String, radius: CGFloat = 14,
                                    action: @escaping () -> Void) -> some View {
        if let prefix {
            self.bcdFocusable(prefix + "." + name, cornerRadius: radius, action: action).id(prefix + "." + name)
        } else {
            self
        }
    }

    /// A slider the controller reaches and steps with left and right (`step` gets -1 or +1).
    @ViewBuilder func bcdTouchStepper(_ prefix: String?, _ name: String, value: Double,
                                      step: @escaping (Int) -> Void) -> some View {
        if let prefix {
            self.modifier(BCDTouchStepperModifier(id: prefix + "." + name, value: value, step: step))
        } else {
            self
        }
    }
}

private struct BCDTouchStepperModifier: ViewModifier {
    let id: String
    let value: Double
    let step: (Int) -> Void

    func body(content: Content) -> some View {
        content
            .bcdFocusable(id, cornerRadius: 14) {}
            .id(id)
            .onAppear { BCDFocusSteppers.set(id, step) }
            // The stepper reads the value it was made with: a new one comes with each change.
            .onChange(of: value) { _, _ in BCDFocusSteppers.set(id, step) }
            .onDisappear { BCDFocusSteppers.set(id, nil) }
    }
}

/// Puts the controller's ring on `id` when this appears (a question's first answer), or, with
/// `onlyIfLost`, when the ring was on a row that has just gone. Only where a BCDFocus is in the
/// environment, and only while a controller is in use.
struct BCDTouchFocusClaim: View {
    @EnvironmentObject private var focus: BCDFocus
    let id: String
    var onlyIfLost = false

    var body: some View {
        Color.clear.frame(width: 0, height: 0)
            .accessibilityHidden(true)
            .onAppear {
                // After the rows that went have let go of the ring.
                DispatchQueue.main.async {
                    guard focus.controllerActive, !onlyIfLost || focus.focused == nil else { return }
                    focus.focused = id
                }
            }
    }
}
