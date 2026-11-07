// The app's toggle switch: the knob flies over with a spring and stretches while held; inside it a checkmark and an x-mark morph into each other;
// the track smoothly changes color and glows when turned on; a ring spreads out from an on switch. With "reduce motion" — no springs or ring.
import SwiftUI

struct SenaSwitch: ToggleStyle {
    var showsLabel = true   // switches with a label in a settings row (labelsHidden) have their own label to the left: it isn't drawn here a second time
    var accent = Style.Colors.accent   // the notch shelf lives in black and white: there the accent is white, the on-knob is black
    var knobOn = Color.white
    var scale: CGFloat = 1             // the notch shelf keeps switches more compact
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: Style.Space.m) {
            if showsLabel { configuration.label }
            else { configuration.label.fixedSize().frame(width: 0, height: 0).clipped().opacity(0) }   // not visible, but VoiceOver reads the name
            Button { configuration.isOn.toggle() } label: { EmptyView() }
                .buttonStyle(SwitchButtonStyle(isOn: configuration.isOn, accent: accent, knobOn: knobOn, scale: scale))
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isToggle)
        .accessibilityValue(configuration.isOn ? L("включено") : L("выключено"))
        .accessibilityAction(.default) { configuration.isOn.toggle() }
    }
}
extension ToggleStyle where Self == SenaSwitch {
    /// The app's switch: set once at the root of windows and sheets
    static var sena: SenaSwitch { SenaSwitch() }
    /// Same, without a label: the settings row already labels it to the left
    static var senaBare: SenaSwitch { SenaSwitch(showsLabel: false) }
    /// Black and white, for the notch shelf: no blue there
    static var senaMono: SenaSwitch { SenaSwitch(accent: .white, knobOn: .black, scale: Style.Size.switchNotch) }
    /// Same, without a label: the shelf's settings row labels it to the left itself
    static var senaMonoBare: SenaSwitch { SenaSwitch(showsLabel: false, accent: .white, knobOn: .black, scale: Style.Size.switchNotch) }
}

private struct SwitchButtonStyle: ButtonStyle {
    let isOn: Bool
    let accent: Color
    let knobOn: Color
    let scale: CGFloat
    func makeBody(configuration: Configuration) -> some View { SwitchGraphic(isOn: isOn, pressed: configuration.isPressed, accent: accent, knobOn: knobOn, scale: scale) }
}

private struct SwitchGraphic: View {
    let isOn: Bool
    let pressed: Bool
    let accent: Color
    let knobOn: Color
    let scale: CGFloat
    @Environment(\.isEnabled) private var enabled
    @Environment(\.accessibilityReduceMotion) private var still
    @State private var hover = false
    @State private var bursts = 0   // increments on turning on: the ring plays again

    var body: some View {
        let width = Style.Size.switchWidth * scale, height = Style.Size.switchHeight * scale, knob = Style.Size.switchKnob * scale
        let inset = (height - knob) / 2
        ZStack(alignment: isOn ? .trailing : .leading) {
            track(width: width, height: height)
            Circle().stroke(accent, lineWidth: Style.Size.line * 2).frame(width: knob, height: knob)   // ring from the knob
                .keyframeAnimator(initialValue: Burst(), trigger: bursts) { ring, b in ring.scaleEffect(b.scale).opacity(b.opacity) } keyframes: { _ in
                    KeyframeTrack(\.scale) { LinearKeyframe(1, duration: 0.01); CubicKeyframe(Style.Motion.burstReach, duration: Style.Motion.burstTime) }
                    KeyframeTrack(\.opacity) { LinearKeyframe(Style.Motion.burstOpacity, duration: 0.01); CubicKeyframe(0, duration: Style.Motion.burstTime) }
                }
                .padding(.horizontal, inset).allowsHitTesting(false)
            knobView(knob)
                .frame(width: pressed ? knob * Style.Motion.switchStretch : knob, height: knob)
                .padding(.horizontal, inset)
        }
        .frame(width: width, height: height)
        .opacity(enabled ? 1 : Style.Motion.disabledFade)
        .scaleEffect(hover && enabled && !still ? Style.Motion.buttonHover : 1)
        .onHover { hover = $0 }
        .motion(Style.Motion.switchSpring, value: isOn)
        .motion(Style.Motion.quick, value: pressed)
        .motion(Style.Motion.quick, value: hover)
        .onChange(of: isOn) { _, on in if on && !still { bursts += 1 } }
    }

    /// Track: blue when on, muted when not; glows when on
    private func track(width: CGFloat, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: height / 2, style: .continuous)
            .fill(isOn ? accent : Style.Colors.ink.opacity(Style.Motion.switchOff))
            .frame(width: width, height: height)
            .shadow(color: accent.opacity(isOn ? Style.Motion.glow : 0), radius: Style.Size.shadowRadius, y: Style.Size.shadowY / 2)
    }

    /// Knob: white, with a checkmark (on) or x-mark (off) inside — the icon morphs via symbolEffect
    private func knobView(_ knob: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: knob / 2, style: .continuous)
            .fill(isOn ? knobOn : Color.white)
            .shadow(color: Color.black.opacity(Style.Motion.liftShadow), radius: Style.Size.shadowRadius / 2, y: Style.Size.shadowY / 2)
            .overlay {
                Image(systemName: isOn ? "checkmark" : "xmark")
                    .font(Style.Icon.switchMark)
                    .scaleEffect(scale)
                    .foregroundStyle(isOn ? (knobOn == Color.white ? accent : Color.white) : Style.Colors.off)
                    .contentTransition(.symbolEffect(.replace))
            }
    }

    private struct Burst { var scale: CGFloat = 1; var opacity = 0.0 }   // no ring at rest
}
