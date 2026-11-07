// Shared details: a status dot, a stat tile, labels, empty screens, a "key — value" list.
import SwiftUI

/// Background for the whole window: no blur, the wallpaper shows through sharply behind a light veil (a color from the style). On macOS 15 this is the window container's background, before that — a backing layer
struct WindowGlass: ViewModifier {
    @Environment(\.colorScheme) private var scheme
    func body(content: Content) -> some View {
        if #available(macOS 15, *) {
            content.containerBackground(for: .window) { Style.Colors.veil(scheme) }
        } else {
            content.background { Style.Colors.veil(scheme).ignoresSafeArea() }
        }
    }
}

/// Window setup: system buttons are hidden (our own live in the left block), the window drags from any empty spot.
/// Its view covers the whole window, so it is invisible to the mouse: it used to sit on top of everything and took every
/// file dragged into the window for itself — the chat's drop zone underneath never heard of it
struct WindowSetup: NSViewRepresentable {
    final class Passthrough: NSView { override func hitTest(_ point: NSPoint) -> NSView? { nil } }
    func makeNSView(context: Context) -> NSView { let v = Passthrough(); DispatchQueue.main.async { apply(v.window) }; return v }
    func updateNSView(_ v: NSView, context: Context) { apply(v.window) }
    private func apply(_ w: NSWindow?) {
        guard let w = w else { return }
        w.isMovableByWindowBackground = true
        w.isOpaque = false   // the window is transparent: the background is drawn by the glass or the veil from the style
        w.backgroundColor = .clear
        for kind in [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton] { w.standardWindowButton(kind)?.isHidden = true }
    }
}

/// Window buttons in our own block: close, minimize, zoom — system colors, icons appear on hover
struct WindowLights: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.controlActiveState) private var active
    @State private var hover = false
    var body: some View {
        HStack(spacing: Style.Space.m) {
            light(Style.Colors.close, "xmark", L("Закрыть")) { if model.discardUnsaved() { NSApp.keyWindow?.performClose(nil) } }   // closing the window loses unsaved edits, same as leaving a section
            light(Style.Colors.minimize, "minus", L("Свернуть")) { NSApp.keyWindow?.miniaturize(nil) }
            light(Style.Colors.zoom, "plus", L("Развернуть")) { NSApp.keyWindow?.zoom(nil) }
        }
        .onHover { hover = $0 }
        .motion(Style.Motion.quick, value: hover)
    }
    private func light(_ color: Color, _ glyph: String, _ name: String, _ act: @escaping () -> Void) -> some View {
        Button(action: act) {
            ZStack {
                Circle().fill(active == .key ? color : Style.Colors.windowOff)
                Circle().strokeBorder(Style.Colors.windowEdge)
                Image(systemName: glyph).font(Style.Icon.windowButton).foregroundStyle(Style.Colors.windowGlyph).opacity(hover ? 1 : 0)
            }
            .frame(width: Style.Size.windowButton, height: Style.Size.windowButton)
            .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(name)   // the icon is visible only under the cursor, VoiceOver always needs the label
        .help(name)
    }
}

/// A section's actions for the right block's header: the screen declares them via .actions { ... }, the header in ContentView shows them on the right
struct ActionsKey: PreferenceKey {
    static var defaultValue: AnyView? = nil
    static func reduce(value: inout AnyView?, nextValue: () -> AnyView?) { if let v = nextValue() { value = v } }
}
extension View {
    func actions<V: View>(@ViewBuilder _ content: () -> V) -> some View { preference(key: ActionsKey.self, value: AnyView(content())) }
}

/// A solid block over the glass: content is clipped to the rounded corners, with the block's surface beneath it (background by default is the system one, a thin border, a soft shadow)
struct Card: ViewModifier {
    var fill: Color? = nil
    func body(content: Content) -> some View {
        content
            .clipShape(RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
            .blockSurface(radius: Style.Radius.all, fill: fill ?? Style.Colors.card)
    }
}
extension View { func card(_ fill: Color? = nil) -> some View { modifier(Card(fill: fill)) } }

/// A status dot: green — running, coral — not running, gray (bad) — no connection. While running, a soft ring spreads from it
/// (with "reduce motion" there's no ring, just the color)
struct StatusDot: View {
    var ok: Bool
    var bad = false
    @Environment(\.accessibilityReduceMotion) private var still
    @State private var pulse = false
    var body: some View {
        let color = bad ? Style.Colors.off : ok ? Style.Colors.ok : Style.Colors.bad
        ZStack {
            if ok && !bad && !still {
                Circle().stroke(color, lineWidth: Style.Size.line)
                    .scaleEffect(pulse ? Style.Motion.pulseReach : 1).opacity(pulse ? 0 : Style.Motion.pulseOpacity)
                    .animation(Style.Motion.pulse, value: pulse)
                    .onAppear { pulse = true }
                    .onDisappear { pulse = false }
            }
            Circle().fill(color)
        }
        .frame(width: Style.Size.dot, height: Style.Size.dot)
    }
}

/// The app's shared button style: one height, corner radius and font. primary — filled with the accent (a block's main action),
/// secondary — a contrasting pill (ordinary), quiet — outlined (secondary importance). Assigned by the window's root to every button;
/// its own look (menu rows, chips, icons) is set with .plain or PressStyle directly
struct SenaButton: ButtonStyle {
    enum Kind { case primary, secondary, quiet }
    var kind: Kind = .secondary
    func makeBody(configuration: Configuration) -> some View { SenaButtonBody(configuration: configuration, kind: kind) }
}
/// The button's body: lightens and grows under the cursor, and (the primary one) glows; icons inside play their own motion; pressed — shrinks
private struct SenaButtonBody: View {
    let configuration: ButtonStyleConfiguration
    let kind: SenaButton.Kind
    @Environment(\.isEnabled) private var enabled
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var still
    @State private var hover = false
    var body: some View {
        // rule: nothing white or gray on white — an ordinary button is dark (ink), primary is the accent, destructive is coral, quiet is colored text
        let destructive = configuration.role == .destructive
        let shape = RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous)
        let fill: Color = kind == .quiet ? .clear : destructive ? Style.Colors.bad : kind == .primary ? Style.Colors.accent : Style.Colors.pill(scheme)   // quiet is an outlined button: always readable, with no gray fill
        // text follows the fill: white on coral is barely readable (around 2.6:1), so onFill picks the color; a quiet destructive button uses the coral text color badText
        let fg: Color = kind == .quiet ? (destructive ? Style.Colors.badText : Style.Colors.ink)
            : destructive ? Style.Colors.onFill(fill) : kind == .secondary ? Style.Colors.pillText(scheme) : .white
        let hot = hover && enabled          // the fill's highlight isn't motion, it stays even with "reduce motion"
        let lit = hot && !still             // the grow and the glow are motion
        configuration.label
            .environment(\.hovering, hover && enabled)
            .font(Style.Text.body.weight(.medium))
            .lineLimit(1)
            .padding(.horizontal, Style.Space.xl)
            .frame(height: Style.Size.controlHeight)
            .foregroundStyle(fg)
            .background(fill, in: shape)
            .overlay(shape.fill(Color.white.opacity(hot && kind != .quiet ? Style.Motion.hoverLight : 0)))
            .overlay(shape.fill(Style.Colors.ink.opacity(hot && kind == .quiet ? Style.Motion.hoverLight / 2 : 0)))
            .overlay(shape.strokeBorder(kind == .quiet ? Style.Colors.ink.opacity(Style.Colors.blockBorder) : Color.clear, lineWidth: Style.Size.line))
            .shadow(color: (kind == .primary ? Style.Colors.accent : Color.black).opacity(lit ? Style.Motion.glow : 0), radius: Style.Size.shadowRadius, y: Style.Size.shadowY)
            .opacity(enabled ? (configuration.isPressed ? Style.Motion.pressedFade : 1) : Style.Motion.disabledFade)
            .scaleEffect(configuration.isPressed ? Style.Motion.pressScale : lit ? Style.Motion.buttonHover : 1)
            .motion(Style.Motion.quick, value: configuration.isPressed)
            .motion(value: hover)
            .contentShape(shape)
            .onHover { hover = $0 }
    }
}
extension ButtonStyle where Self == SenaButton {
    static var primary: SenaButton { SenaButton(kind: .primary) }
    static var secondary: SenaButton { SenaButton(kind: .secondary) }
    static var quiet: SenaButton { SenaButton(kind: .quiet) }
}

/// A segmented control in our style: dark pills of one height and corner radius, the selected one filled with the accent.
/// Used instead of the system segmented Picker (white and gray on white). Three layers: backings for every option, ONE accent
/// pill that smoothly moves to the selected option, and labels with tap handling on top. The pill doesn't live inside the
/// options: otherwise the neighboring backings would cover it in transit and only the gap between them would show
struct Segments<T: Hashable>: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var still
    @Binding var selection: T
    let options: [(T, String)]
    var collapses = false   // a header filter: in a narrow window it collapses into a dropdown list instead of shrinking labels to "…"
    @State private var frames: [Int: CGRect] = [:]   // where each option sits (measured from the label layer)
    @State private var hover: Int?
    @State private var x1: CGFloat = 0   // the pill's edges: each has its own spring, so the pill stretches toward the target and then catches up
    @State private var x2: CGFloat = 0
    @State private var placed = false
    var body: some View {
        // a header filter: full labels if there's room (the header can also have "Reply to Sena", "Stop"), otherwise a dropdown list
        if collapses { ViewThatFits(in: .horizontal) { plates.fixedSize(); Select(selection: $selection, options: options, width: Style.Size.select) } } else { plates }
    }
    @ViewBuilder private var plates: some View {
        let shape = RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous)
        let current = options.firstIndex { $0.0 == selection }
        let lift = { (i: Int) in hover == i && current != i && !still }   // lightens and grows slightly under the cursor — except for the selected one
        ZStack(alignment: .topLeading) {
            HStack(spacing: Style.Space.xs) {   // layer 1: backings (labels are transparent: only needed for sizing)
                ForEach(Array(options.enumerated()), id: \.offset) { i, o in
                    label(o.1).opacity(0)
                        .background(shape.fill(Style.Colors.pill(scheme)))
                        .overlay(shape.fill(Color.white.opacity(lift(i) ? Style.Motion.hoverLight : 0)))
                        .scaleEffect(lift(i) ? Style.Motion.hoverScale : 1)
                }
            }
            .accessibilityHidden(true)   // labels here only set the pills' size; the tap layer below reads them
            if let c = current, let f = frames[c], placed {   // layer 2: the selected option's pill
                shape.fill(Style.Colors.accent).frame(width: max(0, x2 - x1), height: f.height).offset(x: x1, y: f.minY)
            }
            HStack(spacing: Style.Space.xs) {   // layer 3: labels and tap handling
                ForEach(Array(options.enumerated()), id: \.offset) { i, o in
                    Button { selection = o.0 } label: {
                        label(o.1).foregroundStyle(current == i ? Color.white : Style.Colors.pillText(scheme))
                            .scaleEffect(lift(i) ? Style.Motion.hoverScale : 1).contentShape(shape)
                    }
                    .buttonStyle(PressStyle())
                    .accessibilityAddTraits(current == i ? .isSelected : [])
                    .onHover { inside in if inside { hover = i } else if hover == i { hover = nil } }
                    .background(GeometryReader { g in Color.clear.preference(key: SegmentFrames.self, value: [i: g.frame(in: .named("segments"))]) })
                }
            }
        }
        .coordinateSpace(name: "segments")
        .onPreferenceChange(SegmentFrames.self) { frames = $0 }
        .onChange(of: frames) { settle(from: nil, to: current) }     // the first layout and size changes: the pill lands in place with no motion
        .onChange(of: current) { old, new in settle(from: old, to: new) }
        .motion(Style.Motion.slide, value: current)   // the labels' color
        .motion(value: hover)
    }
    /// The pill moves to the selected option: the edge closer to the target catches up fast, the far one lags behind (the pill stretches and then gathers back together)
    private func settle(from old: Int?, to new: Int?) {
        guard let n = new, let f = frames[n] else { return }
        guard placed, !still, let o = old, o != n, let was = frames[o] else { x1 = f.minX; x2 = f.maxX; placed = true; return }
        let right = f.minX > was.minX
        withAnimation(right ? Style.Motion.plateFast : Style.Motion.plateSlow) { x2 = f.maxX }
        withAnimation(right ? Style.Motion.plateSlow : Style.Motion.plateFast) { x1 = f.minX }
    }
    private func label(_ text: String) -> some View {
        Text(cap(text)).font(Style.Text.body.weight(.medium)).lineLimit(1).padding(.horizontal, Style.Space.xl).frame(height: Style.Size.controlHeight)
    }
}
private struct SegmentFrames: PreferenceKey {
    static var defaultValue: [Int: CGRect] = [:]
    static func reduce(value: inout [Int: CGRect], nextValue: () -> [Int: CGRect]) { value.merge(nextValue()) { $1 } }
}

/// A toggle's or list's label — capitalized, like all controls (values from the server arrive lowercase)
func cap(_ s: String) -> String { s.prefix(1).uppercased() + s.dropFirst() }

/// A dropdown list — ONE for the whole app: its own backing instead of the system "pill" (the system one drew a border inside a
/// border, and at a different height), height `controlHeight`, width from the style. framed: false — no backing, already inside
/// a contrasting box (the addressee in the chat input field). capitalize: false — labels as is, uncapitalized (codes like "ag_web")
struct Select<T: Hashable>: View {
    @Binding var selection: T
    let options: [(T, String)]
    var width: CGFloat = Style.Size.select
    var framed = true
    var capitalize = true
    @Environment(\.colorScheme) private var scheme
    @Environment(\.outerScheme) private var outer
    @State private var open = false
    private func shown(_ text: String) -> String { capitalize ? cap(text) : text }
    /// List height: rows all one height (controlHeight) with a gap and margins; beyond listMax the list stops growing and scrolls
    private var listHeight: CGFloat {
        let n = CGFloat(options.count)
        return min(Style.Size.listMax, n * Style.Size.controlHeight + max(0, n - 1) * Style.Space.xxs + 2 * Style.Space.m)
    }
    var body: some View {
        let current = shown(options.first { $0.0 == selection }?.1 ?? "")
        // the field is drawn ourselves, the options list is a popover with the app's own rows (a system menu reduces the label to plain text)
        Button { open.toggle() } label: {
            HStack(spacing: Style.Space.s) {
                Text(current).font(Style.Text.body).lineLimit(1)
                Spacer(minLength: 0)
                Glyph("chevron.up.chevron.down", trigger: open ? 1 : 0).font(Style.Icon.sub)   // the symbol is symmetric: a rotation would change nothing, so it plays its own motion instead
            }
            .foregroundStyle(Color.primary)
            .padding(.horizontal, framed ? Style.Space.l : 0)
            .frame(width: width, height: Style.Size.controlHeight)
            .modifier(SelectFrame(framed: framed))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .modifier(FieldHover())
        .popover(isPresented: $open, arrowEdge: .bottom) {
            ScrollView {
                VStack(alignment: .leading, spacing: Style.Space.xxs) {
                    ForEach(Array(options.enumerated()), id: \.offset) { _, o in
                        Button { selection = o.0; open = false } label: {
                            HStack(spacing: Style.Space.m) {
                                Text(shown(o.1)).font(Style.Text.body).lineLimit(1)
                                Spacer(minLength: Style.Space.l)
                                if o.0 == selection { Image(systemName: "checkmark").font(Style.Icon.sub) }
                            }
                            .row(selected: o.0 == selection, height: Style.Size.controlHeight)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(Style.Space.m)
            }
            .scrollIndicators(.never)   // a popover isn't part of the window: the root setting doesn't reach it
            .frame(minWidth: max(width, Style.Size.selectShort), minHeight: listHeight, maxHeight: listHeight)
            .environment(\.colorScheme, outer ?? scheme)   // the list popover follows the app's theme, not the main block's inverted one: otherwise white text on light glass
        }
    }
}
private struct SelectFrame: ViewModifier {
    let framed: Bool
    func body(content: Content) -> some View { if framed { content.contrast() } else { content } }
}

/// A tap: the row shrinks slightly and returns
struct PressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? Style.Motion.pressScale : 1)
            .motion(Style.Motion.quick, value: configuration.isPressed)
    }
}

/// A block's surface: a color from the style with its own theme inside, one for every block in the app. dark — contrasting (chat: bubbles, the input field, cards in the feed).
/// fill — a custom color for the light surface instead of the block's color (Card, the menu)
struct BlockSurface: ViewModifier {
    @Environment(\.colorScheme) private var scheme
    var radius: CGFloat = Style.Radius.all
    var dark = false
    var fill: Color? = nil
    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: radius, style: .continuous)
        if dark {
            content
                .background(Style.Colors.leadBlock(scheme), in: shape)
                .environment(\.colorScheme, Style.Colors.opposite(scheme))
                .environment(\.outerScheme, scheme)   // fields inside revert to the app's theme: a white field on a dark card
        } else {
            content
                .background(shape.fill(fill ?? Style.Colors.block(scheme)).shadow(color: .black.opacity(Style.Colors.blockShadow), radius: Style.Size.shadowRadius, y: Style.Size.shadowY))
                .overlay(shape.strokeBorder(Color.primary.opacity(Style.Size.borderOpacity)))
        }
    }
}
extension View {
    func blockSurface(radius: CGFloat = Style.Radius.all, dark: Bool = false, fill: Color? = nil) -> some View { modifier(BlockSurface(radius: radius, dark: dark, fill: fill)) }
}

/// A stat tile inside a block: a pastel fill in a brand color, the color is picked by the label, so one label always gets the same color
struct Stat: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.outerScheme) private var outer
    let value: String
    let label: String
    var bad = false
    var body: some View {
        let tint = bad ? Style.Colors.bad : Style.Colors.tint(label)
        let shape = RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous)
        VStack(alignment: .leading, spacing: Style.Space.xxs) {
            Text(value).font(Style.Text.display.monospacedDigit()).foregroundStyle(bad ? Style.Colors.badText : Color.primary)   // "bad" gets the coral text color: the ordinary one is unreadable over a pastel
                .lineLimit(1).minimumScaleFactor(Style.Text.shrink)   // in a narrow tile the number shrinks instead of wrapping
                .rolling(value)   // the number rolls over instead of jumping
            Text(label).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(1).minimumScaleFactor(Style.Text.shrink)   // one line for everyone: tiles in a row are the same height
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Style.Space.gap)
        .background(outer != nil ? AnyShapeStyle(Style.Colors.paper) : AnyShapeStyle(tint.opacity(Style.Colors.tintOpacity)), in: shape)   // in the main block it's a white tile (an island)
        .overlay(alignment: .leading) { shape.fill(tint).frame(width: Style.Size.bar / 2).padding(.vertical, Style.Space.m) }
        .environment(\.colorScheme, outer ?? scheme)
        .hoverScale()
    }
}

/// A block inside a section: a light rounded panel, an optional title and actions on the right.
/// Sections are assembled from these blocks; there are no divider lines
struct Block<Content: View, Trailing: View>: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.stretchBlock) private var stretch   // in an AdaptiveRow row, blocks stretch to match the tallest one
    @Environment(\.insideBlock) private var inside      // a nested block doesn't slide in on its own: it already moves along with the outer one
    let title: String?
    let lead: Bool   // the section's main block: contrasting (dark in light mode, light in dark mode), with an inverted theme inside
    let content: () -> Content
    let trailing: () -> Trailing
    init(_ title: String? = nil, lead: Bool = false, @ViewBuilder content: @escaping () -> Content, @ViewBuilder trailing: @escaping () -> Trailing) {
        self.title = title; self.lead = lead; self.content = content; self.trailing = trailing
    }
    var body: some View {
        let shape = RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous)
        VStack(alignment: .leading, spacing: Style.Space.gap) {
            if title != nil || Trailing.self != EmptyView.self {
                HStack(alignment: .center, spacing: Style.Space.s) {
                    if let t = title { Text(t).font(Style.Text.heading).lineLimit(1).rolling(t) }   // no colored dot: it used to sit crooked and only distracted
                    Spacer()
                    trailing()
                }
                .frame(minHeight: Style.Size.controlHeight)   // every block's title is one height, whether there's a button on the right or not
            }
            VStack(alignment: .leading, spacing: Style.Space.gap) { content() }.environment(\.stretchBlock, false)   // nested blocks don't stretch
        }
        .environment(\.insideBlock, true)
        .environment(\.outerScheme, lead ? scheme : nil)                          // the app's theme: fields inside the main block revert to it
        .environment(\.colorScheme, lead ? Style.Colors.opposite(scheme) : scheme)   // the main block: the theme inside is inverted, text and labels adapt
        .padding(Style.Space.inset)
        .frame(maxWidth: .infinity, maxHeight: stretch ? .infinity : nil, alignment: .topLeading)
        // an ordinary block is white with a dark border on the section's card (Artemiy asked for no color fill); the main one is contrasting
        .background(lead ? Style.Colors.leadBlock(scheme) : Style.Colors.block(scheme), in: shape)
        .overlay(shape.strokeBorder(lead ? Style.Colors.leadEdge(scheme) : Style.Colors.ink.opacity(Style.Colors.blockBorder), lineWidth: Style.Size.line))
        .modifier(Rise(on: !inside))
    }
}
extension Block where Trailing == EmptyView {
    init(_ title: String? = nil, lead: Bool = false, @ViewBuilder content: @escaping () -> Content) { self.init(title, lead: lead, content: content, trailing: { EmptyView() }) }
}

/// The app's theme inside the main block (where the theme is inverted): set only by Block, needed by fields — they revert to it
private struct OuterSchemeKey: EnvironmentKey { static let defaultValue: ColorScheme? = nil }
extension EnvironmentValues { var outerScheme: ColorScheme? { get { self[OuterSchemeKey.self] } set { self[OuterSchemeKey.self] = newValue } } }

/// An "island" inside the main block: reverts to the app's theme and draws its own backing — a white card on a dark block and vice
/// versa. Every card and field inside the main block is an island, so there's contrast both outside and inside. Changes nothing outside the main block
struct Island: ViewModifier {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.outerScheme) private var outer
    func body(content: Content) -> some View {
        content
            .background(Style.Colors.paper, in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
            .environment(\.colorScheme, outer ?? scheme)
    }
}
extension View { func island() -> some View { modifier(Island()) } }

/// A contrasting card inside an ordinary block: dark on light and light on dark, with an inverted theme inside. This is the block's dark surface
extension View { func darkIsland() -> some View { blockSurface(radius: Style.Radius.all, dark: true) } }

/// A contrasting backing that adapts to its surroundings: white inside the main block (the app's theme), dark inside an ordinary one. For fields, editors, lists
struct ContrastIsland: ViewModifier {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.outerScheme) private var outer
    func body(content: Content) -> some View {
        content
            .background(outer != nil ? Style.Colors.paper : Style.Colors.leadBlock(scheme), in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
            .environment(\.colorScheme, outer ?? Style.Colors.opposite(scheme))
    }
}
extension View { func contrast() -> some View { modifier(ContrastIsland()) } }

/// The whole app's input field — a contrasting "island": a dark field with light text on a light block, and a light one on a dark
/// one (in the main block), so input is visible everywhere and never blends into the block. Assigned to every field by the window's root; sheets set it explicitly
struct SenaField: TextFieldStyle {
    func _body(configuration: TextField<Self._Label>) -> some View {
        configuration
            .textFieldStyle(.plain)   // no system border: a border inside a border
            .padding(.horizontal, Style.Space.l).padding(.vertical, Style.Space.xs).frame(minHeight: Style.Size.controlHeight)
            .contrast()
            .modifier(FieldHover())
    }
}

/// Hovering over a field, a list, a date: an accent border fades in (it stays even with "reduce motion"), icons inside play their motion
struct FieldHover: ViewModifier {
    @State private var hover = false
    func body(content: Content) -> some View {
        content
            .environment(\.hovering, hover)
            .overlay(RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).strokeBorder(Style.Colors.accent.opacity(hover ? Style.Motion.hoverRing : 0), lineWidth: Style.Size.line * 2).allowsHitTesting(false))
            .onHover { hover = $0 }
            .motion(Style.Motion.quick, value: hover)
    }
}

/// A round icon for an entry kind: the kind's color, with a dark or white symbol inside, whichever is visible on that color
struct Badge: View {
    @Environment(\.colorScheme) private var scheme
    let icon: String
    let color: Color
    var body: some View {
        let neutral = color == Style.Colors.ink   // ink changes with the theme — the badge takes the "pill" color, like a neutral tag
        ZStack {
            Circle().fill(color)
            Glyph(icon).font(Style.Icon.chip).foregroundStyle(neutral ? Style.Colors.pillText(scheme) : Style.Colors.onFill(color))
        }
        .frame(width: Style.Size.badge, height: Style.Size.badge)
    }
}

/// A record card — ONE for every list in the app (rules, tasks, notes, skills, files, people): a round kind icon on the left,
/// a title, tags below it, and anything on the right (a switch, a trash can, a time). Contrasting: dark on a light block, light on a dark one.
/// A tappable card (it has an action) gets an accent border when selected, lifts on hover, and is a button for VoiceOver
struct RecordCard<Meta: View, Trailing: View>: View {
    let icon: String
    let tint: Color
    let title: String
    let lines: Int          // how many lines the title takes up
    let reserve: Bool       // reserve room for all the lines: cards in a grid are the same height
    let dim: Bool           // disabled: gray
    let selected: Bool
    let action: (() -> Void)?
    let leading: AnyView?   // a custom icon instead of the round one (a file thumbnail)
    let meta: () -> Meta
    let trailing: (Bool) -> Trailing   // receives "cursor over the card" — so a trash can appears on hover
    @State private var hover = false
    init(icon: String, tint: Color, title: String, lines: Int = 2, reserve: Bool = true, dim: Bool = false, selected: Bool = false, action: (() -> Void)? = nil,
         leading: AnyView? = nil, @ViewBuilder meta: @escaping () -> Meta, @ViewBuilder trailing: @escaping (Bool) -> Trailing) {
        self.icon = icon; self.tint = tint; self.title = title; self.lines = lines; self.reserve = reserve; self.dim = dim; self.selected = selected
        self.action = action; self.leading = leading; self.meta = meta; self.trailing = trailing
    }
    var body: some View {
        let shape = RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous)
        HStack(alignment: .center, spacing: Style.Space.l) {
            if let leading = leading { leading } else { Badge(icon: icon, color: dim ? Style.Colors.off : tint) }
            VStack(alignment: .leading, spacing: Style.Space.xs) {
                Text(title).font(Style.Text.body.weight(.medium)).foregroundStyle(dim ? Style.Colors.off : Color.primary)
                    .lineLimit(lines, reservesSpace: reserve)
                HStack(spacing: Style.Space.s) { meta() }
            }
            Spacer(minLength: Style.Space.m)
            trailing(hover)
        }
        .padding(Style.Space.l)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contrast()   // inverted relative to the block it sits in
        .overlay(shape.strokeBorder(Style.Colors.accent, lineWidth: selected ? Style.Size.line * 2 : 0))
        .environment(\.hovering, hover)   // the icon on the left plays its motion while the card is under the cursor
        .raised(hover && action != nil)    // only a tappable one lifts
        .rise(compact: true)
        .contentShape(shape)
        .modifier(Tappable(action: action, label: title, selected: selected))
        .onHover { hover = $0 }
    }
}

/// Tapping a card with the mouse and for VoiceOver: a button with the card's title, a selected one is marked. Without an action the card intercepts nothing
private struct Tappable: ViewModifier {
    let action: (() -> Void)?
    let label: String
    let selected: Bool
    func body(content: Content) -> some View {
        if let action = action {
            content
                .onTapGesture(perform: action)
                .accessibilityElement(children: .contain)   // buttons inside (a trash can, a switch) stay reachable
                .accessibilityLabel(label)
                .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
                .accessibilityAction(.default, action)
        } else { content }
    }
}
extension View {
    /// A tappable piece of screen made with a gesture rather than a button (an image, a row): for VoiceOver and the keyboard it's one button with the same action
    func tapAsButton(_ act: @escaping () -> Void) -> some View {
        accessibilityElement(children: .combine).accessibilityAddTraits(.isButton).accessibilityAction(.default) { act() }
    }
}
extension RecordCard where Trailing == EmptyView {
    init(icon: String, tint: Color, title: String, lines: Int = 2, reserve: Bool = true, dim: Bool = false, selected: Bool = false, action: (() -> Void)? = nil,
         leading: AnyView? = nil, @ViewBuilder meta: @escaping () -> Meta) {
        self.init(icon: icon, tint: tint, title: title, lines: lines, reserve: reserve, dim: dim, selected: selected, action: action, leading: leading, meta: meta, trailing: { _ in EmptyView() })
    }
}

/// Sheets are separate windows: the window root's styles (buttons, fields, switches, size, accent) don't apply inside them, so every sheet sets them here with one call
extension View {
    func sheetStyle() -> some View {
        buttonStyle(.secondary).textFieldStyle(SenaField()).toggleStyle(.sena).controlSize(Style.Size.control).tint(Style.Colors.accent).scrollIndicators(.never)
    }
}

/// A flag meaning "I'm inside a block": set only by Block
private struct InsideBlockKey: EnvironmentKey { static let defaultValue = false }
extension EnvironmentValues { var insideBlock: Bool { get { self[InsideBlockKey.self] } set { self[InsideBlockKey.self] = newValue } } }

/// Appearing, like the charts in costs: a block slides up from below and fades in. Right when a section has just opened, the delay
/// grows with the on-screen height — the section lays itself out top to bottom on its own, with no numbering; later (scrolling, new rows) — instantly. With "reduce motion" — no animation
struct Rise: ViewModifier {
    @Environment(\.pageSlide) private var slide     // which way was flipped: blocks slide in from that side, and the bigger ones also tilt slightly, like a page
    @Environment(\.openedAt) private var openedAt   // from the environment, not the model: otherwise every block and card would redraw on any overview update
    @Environment(\.accessibilityReduceMotion) private var still
    var on = true
    var compact = false   // a list card: slides in closer and faster
    @State private var shown = false
    func body(content: Content) -> some View {
        if !on || still { content }
        else {
            content
                .opacity(shown ? 1 : 0)
                .offset(x: shown ? 0 : slide * (compact ? Style.Motion.pageSlide / 2 : Style.Motion.pageSlide), y: shown ? 0 : (compact ? Style.Motion.riseShift / 2 : Style.Motion.riseShift))
                .rotation3DEffect(.degrees(shown || compact ? 0 : -Double(slide) * Style.Motion.pageTurn), axis: (x: 0, y: 1, z: 0), anchor: slide > 0 ? .leading : .trailing, perspective: Style.Motion.glyphPerspective)
                .scaleEffect(shown ? 1 : (compact ? Style.Motion.riseScaleSmall : Style.Motion.riseScale), anchor: .top)
                .background(GeometryReader { g in
                    Color.clear.onAppear {
                        let top = g.frame(in: .global).minY
                        if top > (NSScreen.main?.frame.height ?? Style.Motion.riseBottom) { shown = true; return }   // below the screen there's nothing to animate: a hundred list cards don't spin layers for nothing
                        let fresh = Date().timeIntervalSince(openedAt) < Style.Motion.openWindow
                        let delay = fresh ? min(Style.Motion.riseMax, max(0, top - Style.Motion.riseTop) * Style.Motion.riseStagger) : 0
                        withAnimation(Style.Motion.rise.delay(delay)) { shown = true }
                    }
                })
        }
    }
}
extension View { func rise(compact: Bool = false) -> some View { modifier(Rise(compact: compact)) } }

private struct OpenedAtKey: EnvironmentKey { static let defaultValue = Date.distantPast }
extension EnvironmentValues { var openedAt: Date { get { self[OpenedAtKey.self] } set { self[OpenedAtKey.self] = newValue } } }

/// The "lifted" look: slightly bigger and with a shadow. The flag comes from whoever knows about hovering (RecordCard, Lift)
struct Raised: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var still
    let on: Bool
    func body(content: Content) -> some View {
        content
            .scaleEffect(on && !still ? Style.Motion.lift : 1)
            .shadow(color: .black.opacity(on && !still ? Style.Motion.liftShadow : 0), radius: Style.Size.shadowRadius, y: Style.Size.shadowY)
            .motion(value: on)
    }
}
extension View { func raised(_ on: Bool) -> some View { modifier(Raised(on: on)) } }

/// Hovering over something tappable: it lifts, icons inside play their motion (the hovering environment value)
struct Lift: ViewModifier {
    @State private var hover = false
    func body(content: Content) -> some View {
        content
            .environment(\.hovering, hover)
            .raised(hover)
            .onHover { hover = $0 }
    }
}
extension View { func lift() -> some View { modifier(Lift()) } }

/// A list row inside a block: hover and selection highlighting, no lines
struct RowStyle: ViewModifier {
    @Environment(\.colorScheme) private var scheme
    let selected: Bool
    var height: CGFloat? = nil   // rows of one height (lists where the height needs to be known ahead of time)
    @State private var hover = false
    func body(content: Content) -> some View {
        content
            .padding(.horizontal, Style.Space.gap).padding(.vertical, Style.Space.gapSmall)
            .frame(maxWidth: .infinity, minHeight: height, maxHeight: height, alignment: .leading)
            .background(selected ? Style.Colors.selection : hover ? Style.Colors.hover(scheme) : Color.clear, in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
            .contentShape(Rectangle())
            .onHover { hover = $0 }
            .motion(Style.Motion.quick, value: hover)
    }
}
extension View { func row(selected: Bool = false, height: CGFloat? = nil) -> some View { modifier(RowStyle(selected: selected, height: height)) } }

/// An agent code without a subpath: "ag_web/search" → "ag_web". Everywhere a code arrives from the server together with who ran it, it's taken from here
func agentCode(_ who: String) -> String { who.split(separator: "/").first.map(String.init) ?? who }

/// An agent's icon in a circle of its own color: Sena gets her real leaf, everyone else gets their own symbol. One shared across every screen
struct AgentIcon: View {
    let code: String
    var size: CGFloat = Style.Size.counter
    var iconFont: Font = Style.Icon.tiny
    var trigger: Int = 0   // when it changes, the icon plays its animation from the Glyphs table, not only on hover
    var fill: Color? = nil   // the notch shelf paints free agents black; nil — the agent's usual color
    static let symbols = ["ag_web": "globe", "ag_mac": "desktopcomputer", "ag_code": "chevron.left.forwardslash.chevron.right",
                          "ag_judge": "scalemass", "ag_night": "moon.stars", "ag_ears": "ear"]
    var body: some View {
        let c = agentCode(code)
        ZStack {
            Circle().fill(fill ?? Style.Colors.agent(c))
            if c == "ag_sena" { Image(nsImage: leafImage(size: size * 0.62, color: .white, template: false)) }   // the leaf image is served from the cache
            else { Glyph(AgentIcon.symbols[c] ?? "circle", trigger: trigger).font(iconFont).foregroundStyle(.white) }
        }
        .frame(width: size, height: size)
    }
}

/// A progress bar: a share from 0 to 1, coral when the limit is reached
struct Bar: View {
    @Environment(\.accessibilityReduceMotion) private var still
    let value: Double
    var limit = false   // spending against a limit (a budget, a free quota): at the limit the bar turns coral; a share or a progress never does
    @State private var grown = false   // on appearing, the bar grows from zero, like the bars in costs
    var body: some View {
        let share = value.isFinite ? min(1, max(0, value)) : 0
        let color = limit && value >= 1 ? Style.Colors.bad : Style.Colors.accent
        GeometryReader { g in
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).fill(color.opacity(Style.Colors.tintOpacity))   // the track matches the bar's color, not gray
                RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).fill(color).frame(width: g.size.width * (grown || still ? share : 0))
                    .motion(value: value)
            }
        }
        .frame(height: Style.Size.bar)
        .accessibilityElement()
        .accessibilityValue("\(Int((share * 100).rounded())) %")
        .onAppear { withAnimation(still ? nil : Style.Motion.spring.delay(Style.Motion.barDelay)) { grown = true } }
    }
}

/// Two columns for a section: a fixed-width list on the left, details on the right. Stacked in a narrow window
struct Columns<Left: View, Right: View>: View {
    @EnvironmentObject var model: AppModel
    var width: CGFloat = Style.Size.column
    @ViewBuilder let left: () -> Left
    @ViewBuilder let right: () -> Right
    var body: some View {
        // the same container (AnyLayout) in both narrow and wide windows: the screens inside aren't rebuilt, drafts and scrolling aren't lost
        let layout = model.narrow ? AnyLayout(VStackLayout(spacing: Style.Space.xl)) : AnyLayout(HStackLayout(alignment: .top, spacing: Style.Space.xl))
        layout {
            left().frame(width: model.narrow ? nil : width).frame(maxHeight: model.narrow ? Style.Size.listMax : nil)
            right().frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .motion(Style.Motion.quick, value: model.narrow)
    }
}

/// Choosing a number from a dropdown list: one tap instead of tiny arrows. If the current value isn't in the list, it's added
struct Choice: View {
    @Binding var value: Int
    let options: [Int]
    let label: (Int) -> String
    var width: CGFloat = Style.Size.selectShort
    var body: some View {
        let all = options.contains(value) ? options : (options + [value]).sorted()
        Select(selection: $value, options: all.map { ($0, label($0)) }, width: width)
    }
}

/// A date field: a contrasting field matching the others; a tap opens a calendar. Used instead of the system date field (it has its own border and height)
struct DateField: View {
    @Binding var date: Date
    let range: ClosedRange<Date>
    @State private var open = false
    var body: some View {
        Button { open.toggle() } label: {
            Text(date.formatted(.dateTime.day(.twoDigits).month(.twoDigits).year())).font(Style.Text.body.monospacedDigit())
                .frame(width: Style.Size.date, height: Style.Size.controlHeight)
                .contrast()
        }
        .buttonStyle(.plain)
        .modifier(FieldHover())
        .popover(isPresented: $open, arrowEdge: .bottom) {
            DatePicker("", selection: $date, in: range, displayedComponents: .date).datePickerStyle(.graphical).labelsHidden().padding(Style.Space.l)
        }
        .onChange(of: date) { open = false }
    }
    /// The lower bound for picking a date: five years back — the journal doesn't go any deeper
    static var earliest: Date { Calendar.current.date(byAdding: .year, value: -5, to: Date()) ?? .distantPast }
}

/// The "from … to …" data period for a section's header: one shared pair of dates for the whole app (model.since / until), Tema picks the dates himself.
/// There are no ready-made "today / week / month" presets anywhere — Artemiy's request on 2026-10-18
struct PeriodPicker: View {
    @EnvironmentObject var model: AppModel
    // "from" can't end up later than "to" (or the other way around): the second date tracks the one just picked, instead of hiding the needed days past the list's edge
    private var sinceBinding: Binding<Date> { Binding(get: { model.since }, set: { model.since = $0; if model.until < $0 { model.until = $0 } }) }
    private var untilBinding: Binding<Date> { Binding(get: { model.until }, set: { model.until = $0; if model.since > $0 { model.since = $0 } }) }
    var body: some View {
        if !model.narrow { Text(L("с")).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText) }   // labels don't fit in a narrow window: tooltips instead
        DateField(date: sinceBinding, range: DateField.earliest...Date()).help(L("Период: с какого дня"))
        if !model.narrow { Text(L("по")).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText) }
        DateField(date: untilBinding, range: DateField.earliest...Date()).help(L("Период: по какой день"))
    }
}

/// A row of blocks: side by side and one height in a wide window (matching the tallest), stacked in a narrow one
struct AdaptiveRow<Content: View>: View {
    @EnvironmentObject var model: AppModel
    var fill = false   // true — the row stretches to the section's full height (the integrations board); otherwise — matches its own content's height (cards)
    @ViewBuilder let content: () -> Content
    var body: some View {
        // one shared container for both cases (see Columns): blocks inside aren't rebuilt when the window's width changes
        let layout = model.narrow ? AnyLayout(VStackLayout(spacing: Style.Space.xl)) : AnyLayout(HStackLayout(alignment: .top, spacing: Style.Space.xl))
        layout { content().environment(\.stretchBlock, !model.narrow) }
            .fixedSize(horizontal: false, vertical: !model.narrow && !fill)
    }
}
/// A flag meaning "stretch to height" for blocks in a row; set only by AdaptiveRow
private struct StretchBlockKey: EnvironmentKey { static let defaultValue = false }
extension EnvironmentValues { var stretchBlock: Bool { get { self[StretchBlockKey.self] } set { self[StretchBlockKey.self] = newValue } } }

struct KV: View {
    let rows: [(String, String)]
    var body: some View {
        Grid(alignment: .leading, horizontalSpacing: Style.Space.xl, verticalSpacing: Style.Space.s) {
            ForEach(Array(rows.enumerated()), id: \.offset) { _, r in
                GridRow {
                    Text(r.0).foregroundStyle(Style.Colors.secondaryText).gridColumnAlignment(.leading)
                    Text(r.1).textSelection(.enabled)
                }
            }
        }
        .font(Style.Text.small)
    }
}

/// A tag: colored — a pastel fill with dark text; neutral (ink color) — dark with white text. There's no gray variant
struct Tag: View {
    @Environment(\.colorScheme) private var scheme
    let text: String
    var color: Color = Style.Colors.ink
    var body: some View {
        let neutral = color == Style.Colors.ink
        let bright = scheme == .dark && !neutral   // on a dark background a transparent pastel sinks in — there a colored tag goes bright instead
        Text(text).font(Style.Text.caption.weight(.medium)).lineLimit(1).padding(.horizontal, Style.Space.s).padding(.vertical, Style.Space.xxs)
            .background(neutral || bright ? color : color.opacity(Style.Colors.tintOpacity), in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
            .foregroundStyle(neutral ? Style.Colors.pillText(scheme) : bright ? Style.Colors.onFill(color) : Style.Colors.ink)   // ink by theme
    }
}

/// A confirmation before a risky action, as a system dialog: true — the main button was pressed, not "Cancel". One dialog for the whole app
/// (deleting a skill, removing a rule, clearing a conversation, and so on)
func confirm(_ title: String, _ text: String, button: String, destructive: Bool = false) -> Bool {
    let a = NSAlert(); a.messageText = title; a.informativeText = text
    let ok = a.addButton(withTitle: button); ok.hasDestructiveAction = destructive
    a.addButton(withTitle: L("Отмена")).keyEquivalent = "\u{1b}"   // Esc cancels
    if destructive { ok.keyEquivalent = "" }   // a risky action only fires on a click: an accidental Enter destroys nothing
    return a.runModal() == .alertFirstButtonReturn
}

/// Running a background chore by hand spends money (a model): ask, same as the mini panel and the chore's card. true — go ahead and run it
func confirmPaidRun(_ title: String) -> Bool {
    confirm(String(format: L("Запустить «%@»?"), title), L("Это платный запуск: модель потратит деньги."), button: L("Запустить"))
}

/// Simple markup (bold, italics, code) in plain text: Sena's replies and events support it. One shared way across the whole app
func markdownText(_ s: String) -> AttributedString {
    if let hit = markdownCache[s] { return hit }
    let out = (try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(s)
    if markdownCache.count > 600 { markdownCache.removeAll(keepingCapacity: true) }
    markdownCache[s] = out   // the same text gets parsed on every redraw and every scroll of the chat
    return out
}
private var markdownCache: [String: AttributedString] = [:]

/// One block of a written answer: a heading, a paragraph, a list item (its mark and nesting), a quote, code, a break
enum MarkdownBlock {
    case heading(Int, String), paragraph(String), item(String, Int, String), quote(String), code(String), rule

    /// Lines -> blocks. Only what answers really use; anything else stays a paragraph with its inline marks
    static func parse(_ s: String) -> [MarkdownBlock] {
        if let hit = blockCache[s] { return hit }
        var out: [MarkdownBlock] = [], para: [String] = [], code: [String]? = nil
        func flush() { if !para.isEmpty { out.append(.paragraph(para.joined(separator: "\n"))); para = [] } }
        for raw in s.components(separatedBy: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if var c = code {   // inside a code block everything is kept as typed until the closing fence
                if line.hasPrefix("```") { out.append(.code(c.joined(separator: "\n"))); code = nil } else { c.append(raw); code = c }
                continue
            }
            let depth = min(3, raw.prefix { $0 == " " || $0 == "\t" }.count / 2)
            if line.hasPrefix("```") { flush(); code = [] }
            else if line.isEmpty { flush() }
            else if line.range(of: #"^(-{3,}|\*{3,}|_{3,})$"#, options: .regularExpression) != nil { flush(); out.append(.rule) }
            else if let m = line.range(of: #"^#{1,6}\s+"#, options: .regularExpression) {
                flush(); out.append(.heading(line[m].filter { $0 == "#" }.count, String(line[m.upperBound...])))
            }
            else if let m = line.range(of: #"^[-*+•]\s+"#, options: .regularExpression) { flush(); out.append(.item("•", depth, String(line[m.upperBound...]))) }
            else if let m = line.range(of: #"^\d{1,3}[.)]\s+"#, options: .regularExpression) {
                flush(); out.append(.item(line[m].trimmingCharacters(in: .whitespaces), depth, String(line[m.upperBound...])))
            }
            else if line.hasPrefix(">") { flush(); out.append(.quote(line.dropFirst().trimmingCharacters(in: .whitespaces))) }
            else { para.append(line) }
        }
        if let c = code { out.append(.code(c.joined(separator: "\n"))) }   // an unclosed fence: still code
        flush()
        if blockCache.count > 600 { blockCache.removeAll(keepingCapacity: true) }
        blockCache[s] = out   // parsed once: the chat redraws on every poll and scroll
        return out
    }
}
private var blockCache: [String: [MarkdownBlock]] = [:]

/// Sena's written answer with its structure — headings, lists, quotes, code — instead of one wall of text; bold, italic,
/// code and links inside a line as before (markdownText). Breaks between sections are space, not lines
struct MarkdownView: View {
    let text: String
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.s) {
            ForEach(Array(MarkdownBlock.parse(text).enumerated()), id: \.offset) { i, b in block(b, first: i == 0) }
        }
        .textSelection(.enabled)
    }
    @ViewBuilder func block(_ b: MarkdownBlock, first: Bool) -> some View {
        switch b {
        case .heading(let level, let t):
            Text(markdownText(t)).font(level <= 2 ? Style.Text.title : Style.Text.heading).padding(.top, first ? 0 : Style.Space.s)
        case .paragraph(let t):
            Text(markdownText(t)).font(Style.Text.body)
        case .item(let mark, let depth, let t):
            HStack(alignment: .firstTextBaseline, spacing: Style.Space.s) {
                Text(mark).font(Style.Text.body.weight(.semibold)).foregroundStyle(Style.Colors.accent).frame(minWidth: Style.Space.l, alignment: .trailing)
                Text(markdownText(t)).font(Style.Text.body).frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.leading, CGFloat(depth) * Style.Space.xl)
        case .quote(let t):
            Text(markdownText(t)).font(Style.Text.body).foregroundStyle(Style.Colors.secondaryText)
                .padding(.leading, Style.Space.m)
                .overlay(alignment: .leading) { RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).fill(Style.Colors.accent).frame(width: Style.Space.xxs) }
        case .code(let t):
            Text(verbatim: t).font(Style.Text.mono).padding(Style.Space.m).frame(maxWidth: .infinity, alignment: .leading)
                .background(Style.Colors.theirs, in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
        case .rule:
            Color.clear.frame(height: Style.Space.xs)
        }
    }
}

func verdictColor(_ v: String) -> Color {
    switch v { case "done": return Style.Colors.ok; case "failed": return Style.Colors.bad; case "done_with_remarks": return Style.Colors.warn; default: return Style.Colors.ink }
}
func verdictTag(_ task: JSON) -> Tag {
    let v = task["outcome"]["verdict"].string ?? "none"
    return Tag(text: VERDICT_RU[v] ?? v, color: verdictColor(v))
}
func verdictIcon(_ v: String) -> String {
    switch v { case "done": return "checkmark"; case "failed": return "xmark"; case "done_with_remarks": return "exclamationmark"; default: return "minus" }
}
func kindColor(_ k: String) -> Color {
    switch k { case "error", "stop": return Style.Colors.bad; case "model", "embeddings", "transcribe", "speech", "media": return Style.Colors.model
    case "message_in", "message_out", "file_out": return Style.Colors.ok; case "task_start", "task_end", "job", "verdict", "comment", "review": return Style.Colors.warn; default: return Style.Colors.ink }
}

/// An empty screen and an error: a big icon and title (Style.Icon.big, Style.Text.display), a smaller, quieter explanation.
/// button and act — an optional button below the text ("Retry")
struct Empty: View {
    @Environment(\.accessibilityReduceMotion) private var still
    let title: String
    var text: String = ""
    var icon = "tray"
    var button: String? = nil
    var act: (() -> Void)? = nil
    @State private var shown = false
    var body: some View {
        ContentUnavailableView {
            Label { Text(title).font(Style.Text.display) } icon: { Image(systemName: icon).font(Style.Icon.big).foregroundStyle(Style.Colors.secondaryText) }
                .symbolEffect(.bounce, options: .speed(0.8), value: shown && !still)
        } description: {
            if !text.isEmpty { Text(text).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText) }
        } actions: {
            if let b = button, let a = act { Button(b, action: a) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .opacity(shown || still ? 1 : 0).scaleEffect(shown || still ? 1 : Style.Motion.riseScale)
        .motion(Style.Motion.rise.delay(Style.Motion.emptyDelay), value: shown)
        .onAppear { shown = true }
    }
}

struct SectionHeader: View {
    let title: String
    var body: some View { Text(title).font(Style.Text.caption.weight(.semibold)).foregroundStyle(Style.Colors.secondaryText).textCase(.uppercase).padding(.top, Style.Space.xs).rolling(title) }
}

/// Loading with auto-refresh: the body gets data while the screen is open — refreshed every seconds.
/// While there's no data and loading failed, it retries after a few seconds rather than after seconds, and the screen shows a "Retry" button.
/// If a failure happens after a success, the old data stays, but marked "stale" with the same button
struct Polling<T, Content: View>: View {
    let seconds: Double
    let load: () async throws -> T
    @ViewBuilder let content: (T) -> Content
    @State private var value: T?
    @State private var error: String?   // the last load failed (nil — succeeded)
    private static var retryAfter: Double { 3 }   // how soon to try again while there's no data or the last attempt failed
    var body: some View {
        Group {
            if let v = value { content(v) }
            else if let e = error { Empty(title: L("Не загрузилось"), text: e, icon: "exclamationmark.triangle", button: L("Повторить"), act: retry) }
            else { Loading() }
        }
        .overlay(alignment: .bottom) {
            if value != nil && error != nil { staleNote.transition(Style.Motion.appear) }
        }
        .motion(value: error == nil)
        .task(id: seconds) {
            while !Task.isCancelled {
                await refresh()
                let wait = value == nil || error != nil ? min(seconds, Polling.retryAfter) : seconds
                try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
            }
        }
    }
    private var staleNote: some View {
        HStack(spacing: Style.Space.m) {
            Text(L("Данные устарели")).font(Style.Text.small)
            Button(L("Повторить"), action: retry).buttonStyle(.quiet)
        }
        .padding(Style.Space.m)
        .darkIsland()
        .padding(Style.Space.m)
    }
    private func retry() { Task { await refresh() } }
    @MainActor private func refresh() async {
        do {
            let fresh = try await load()
            if let a = value as? JSON, let b = fresh as? JSON, a.isSame(b) {} else { value = fresh }
            error = nil
        } catch { self.error = error.localizedDescription }
    }
}

/// An icon button ("+", a mic, canceling a recording, a trash can in the header): no background, lights up on hover. label — what it does, for VoiceOver
struct IconButton: View {
    let icon: String
    var label = ""
    let act: () -> Void
    @State private var hover = false
    private var core: some View {
        Button(action: act) {
            Glyph(icon).font(Style.Icon.composer).foregroundStyle(.primary)
                .frame(width: Style.Size.button, height: Style.Size.button)
                .background(Color.primary.opacity(hover ? Style.Motion.hoverLight / 2 : 0), in: Circle())
                .scaleEffect(hover ? Style.Motion.buttonHover : 1)
                .contentShape(Circle())
        }
        .buttonStyle(PressStyle())
        .environment(\.hovering, hover)
        .onHover { hover = $0 }
        .motion(value: hover)
    }
    var body: some View {
        if label.isEmpty { core } else { core.accessibilityLabel(label) }
    }
}
