// App motion: every icon has its own animation, plus hover, appearing, loading. Everything respects the system's "reduce motion":
// finite animations go through .motion(value:) (at the end of this file), infinite ones don't start at all with that setting on.
// Icons: Glyph plays its icon's motion (the Glyphs table) on hovering over whatever holds it (the hovering environment value), and on a
// trigger signal (a tap, a selection). Loading: Loading (the leaf), Skeleton (shimmering list placeholders), ArcSpinner (a small ring).
import SwiftUI

// ---------- environment: "under this cursor" ----------

private struct HoveringKey: EnvironmentKey { static let defaultValue = false }
extension EnvironmentValues { var hovering: Bool { get { self[HoveringKey.self] } set { self[HoveringKey.self] = newValue } } }

// ---------- icons ----------

/// Icon motion: each track has up to five values (segments of `Style.Motion.glyphStep`), then a spring to neutral
/// (for rotations — to `…End`, so a full turn ends where it started). Fewer than five values — the last one holds,
/// empty — neutral: it's impossible to go out of the array's bounds. flip — a turn around the vertical axis with depth (a coin, a card),
/// fade — an opacity flicker, anchor — the point the icon swings and shrinks around (a bell hangs from the top)
struct GlyphMotion {
    var rotate: [Double] = []; var rotateEnd: Double = 0
    var flip: [Double] = []; var flipEnd: Double = 0
    var sx: [CGFloat] = []
    var sy: [CGFloat] = []
    var dx: [CGFloat] = []
    var dy: [CGFloat] = []
    var fade: [Double] = []
    var anchor: UnitPoint = .center

    /// pop: grows past its size and settles
    static let pop = GlyphMotion(sx: [1.34, 0.88, 1.12, 0.98], sy: [1.34, 0.88, 1.12, 0.98])
    static func scale(_ a: CGFloat, _ b: CGFloat, _ c: CGFloat) -> GlyphMotion { GlyphMotion(sx: [a, b, c], sy: [a, b, c]) }
    /// a wobble around the center that fades out
    static func wiggle(_ a: Double) -> GlyphMotion { GlyphMotion(rotate: [-a, a * 0.85, -a * 0.6, a * 0.3, -a * 0.12]) }
    /// pendulum: hangs from a pivot point and swings slower and slower (a bell, an alarm clock, a tablet)
    static func swing(_ a: Double, from anchor: UnitPoint = .top) -> GlyphMotion { GlyphMotion(rotate: [a, -a * 0.8, a * 0.55, -a * 0.3, a * 0.12], anchor: anchor) }
    /// a sideways shake (failure, a lock)
    static func shake(_ d: CGFloat) -> GlyphMotion { GlyphMotion(dx: [-d, d, -d * 0.7, d * 0.4, -d * 0.15]) }
    static func lift(_ d: CGFloat) -> GlyphMotion { GlyphMotion(sx: [1.1, 1.05, 1.02], sy: [1.1, 1.05, 1.02], dy: [-d, d * 0.3, -d * 0.2]) }
    /// a jump: crouches, leaps, lands with a squash
    static func bounce(_ h: CGFloat) -> GlyphMotion {
        GlyphMotion(sx: [1.12, 0.94, 1.06, 0.98], sy: [0.82, 1.18, 0.9, 1.04], dy: [h * 0.4, -h, h * 0.3, -h * 0.25], anchor: .bottom)
    }
    static func slide(_ x: CGFloat, _ y: CGFloat) -> GlyphMotion { GlyphMotion(dx: [x, -x * 0.35, x * 0.15], dy: [y, -y * 0.35, y * 0.15]) }
    /// a turn; a smooth ramp-up in speed and a gentle stop
    static func spin(_ deg: Double) -> GlyphMotion { GlyphMotion(rotate: [deg * 0.18, deg * 0.5, deg * 0.86, deg * 1.04], rotateEnd: deg) }
    /// a coin or a card: flips over with depth
    static func coin(_ turns: Double = 1) -> GlyphMotion { GlyphMotion(flip: [turns * 140, turns * 250, turns * 340], flipEnd: turns * 360, sx: [1.12, 1.12, 1.04]) }
    /// two heartbeats
    static let heartbeat = GlyphMotion(sx: [1.3, 0.94, 1.2, 1.0], sy: [1.3, 0.94, 1.2, 1.0])
    /// a spark: flares up, flickers and settles down
    static let sparkle = GlyphMotion(rotate: [24, -14, 8, -3], sx: [1.32, 0.82, 1.2, 0.97], sy: [1.32, 0.82, 1.2, 0.97], fade: [0.45, 1, 0.65, 1])
    /// flies off the edge and comes back from the opposite side (send, a plane, an up arrow)
    static func launch(_ dx: CGFloat, _ dy: CGFloat) -> GlyphMotion {
        GlyphMotion(rotate: [-8, -4, 0], dx: [dx * 0.6, dx * 2.4, -dx * 2.4, 0], dy: [dy * 0.6, dy * 2.4, -dy * 2.4, 0], fade: [1, 0, 0, 1])
    }
    /// sound bars jitter up and down
    static let wave = GlyphMotion(sy: [1.6, 0.55, 1.35, 0.8, 1.05], anchor: .center)
    /// a "blink": squashes flat and opens back up
    static let blink = GlyphMotion(sy: [0.15, 1.25, 0.95], fade: [0.5, 1, 1])
    /// closes and opens from the bottom (a folder, a box, a document)
    static func unfold(_ d: CGFloat = 1.5) -> GlyphMotion { GlyphMotion(sy: [0.8, 1.14, 0.96, 1.02], dy: [d, -d * 0.7, d * 0.2], anchor: .bottom) }
    /// "arrives": drops from above into a tray
    static func drop(_ h: CGFloat) -> GlyphMotion { GlyphMotion(sy: [1.1, 0.88, 1.04], dy: [-h, h * 0.4, -h * 0.15], fade: [0.4, 1, 1]) }
    /// a checkmark drawing itself: pops out small, overshoots its size and settles
    static let tick = GlyphMotion(rotate: [-14, 8, -3], sx: [0.3, 1.5, 0.92, 1.04], sy: [0.3, 1.5, 0.92, 1.04], fade: [0.2, 1, 1, 1])
    /// a magnifying glass circling around
    static let search = GlyphMotion(rotate: [-18, 14, -7], dx: [-2.5, 0.5, 2.5, -1], dy: [0, -2.5, 0, 1.5])
}

enum Glyphs {
    /// Every app icon has its own motion; an unknown one gets a bounce
    static func motion(_ symbol: String) -> GlyphMotion { table[symbol] ?? .bounce(2.5) }
    static let table: [String: GlyphMotion] = [
        // menu sections
        "bubble.left.and.bubble.right": GlyphMotion(sx: [1.16, 0.94, 1.06, 1.0], sy: [0.84, 1.2, 0.92, 1.03], dy: [2, -4, 1, -1], anchor: .bottom),   // chat: bubbles bounce
        "list.clipboard": .swing(10, from: .top),                                                                                                      // tasks: a clipboard swings on its clip
        "text.badge.checkmark": .tick,                                                                                                                 // rules: a checkmark draws itself
        "archivebox": .unfold(2.5),                                                                                                                    // memory: a box unfolds
        "point.3.filled.connected.trianglepath.dotted": .spin(120),                                                                                    // integrations: nodes rotate
        "creditcard": .coin(),                                                                                                                         // journal: a card flips in depth
        "gearshape": GlyphMotion(rotate: [60, 170, 290, 370], rotateEnd: 360, sx: [1.1, 1.14, 1.06, 1.0], sy: [1.1, 1.14, 1.06, 1.0]),                 // settings: a gear spins up
        // actions
        "trash": .swing(16, from: .bottom), "bell": .swing(24, from: .top), "alarm": .swing(22, from: .top), "xmark": .spin(90), "plus": .spin(90),
        "doc.on.doc": .slide(3, -3), "rectangle.portrait.on.rectangle.portrait": .slide(3, -3),
        "arrow.up": .launch(0, -4), "magnifyingglass": .search,
        "mic": GlyphMotion(sx: [1.15, 0.94, 1.06, 1.0], sy: [1.34, 0.9, 1.1, 1.0], anchor: .bottom), "mic.fill": GlyphMotion(sx: [1.15, 0.94, 1.06, 1.0], sy: [1.34, 0.9, 1.1, 1.0], anchor: .bottom),
        "power": GlyphMotion(rotate: [180, 300, 380], rotateEnd: 360, sx: [1.2, 1.1, 1.05], sy: [1.2, 1.1, 1.05]),
        "stop.circle": .pop, "chevron.right": .slide(3.5, 0), "chevron.up.chevron.down": GlyphMotion(sy: [1.4, 0.8, 1.12, 0.98]),
        "checkmark": .tick, "xmark.circle.fill": .spin(90), "arrow.triangle.2.circlepath": .spin(360),
        "paperplane": .launch(4, -4), "paperplane.fill": .launch(4, -4), "arrow.up.forward.app": .launch(3, -3),
        // entry kinds and entities
        "calendar": .swing(10, from: .top), "repeat": .spin(360), "infinity": GlyphMotion(sx: [1.4, 0.84, 1.14, 0.98]),
        "hourglass": GlyphMotion(rotate: [60, 150, 190, 180], rotateEnd: 180), "bitcoinsign": .coin(), "dollarsign": .coin(),
        "chart.line.uptrend.xyaxis": GlyphMotion(sx: [0.9, 1.08, 1.02], sy: [1.3, 0.9, 1.06], dy: [-2.5, 1, 0], anchor: .bottomLeading),
        "airplane": .launch(5, -5), "person.fill": .bounce(2), "person.2": .bounce(2), "person.crop.circle.fill": .bounce(2),
        "wand.and.stars": .sparkle, "sparkles": .sparkle, "doc.text": .unfold(1), "doc": .unfold(1), "photo": .unfold(1), "photo.fill": .unfold(1),
        "film": .slide(3, 0), "waveform": .wave, "text.quote": .pop, "text.alignleft": GlyphMotion(sx: [1.2, 0.9, 1.06, 1.0], anchor: .leading),
        "wrench.and.screwdriver": GlyphMotion(rotate: [-28, 22, -12, 5]), "hammer.fill": .swing(24, from: .bottomTrailing),
        "arrow.triangle.branch": .slide(2.5, -2.5), "bolt.fill": GlyphMotion(sx: [1.4, 0.86, 1.14, 1.0], sy: [1.4, 0.86, 1.14, 1.0], fade: [0.4, 1, 0.6, 1]),
        "exclamationmark": .shake(2.5), "exclamationmark.triangle": .shake(2.5), "minus": GlyphMotion(sx: [1.44, 0.84, 1.06, 1.0]),
        "tablecells": .pop, "doc.richtext": .unfold(1), "doc.plaintext": .unfold(1), "doc.zipper": .unfold(1), "folder": .unfold(1.5),
        "tray.and.arrow.down": .drop(4), "ear": .blink, "globe": GlyphMotion(rotate: [90, 200, 300], rotateEnd: 360), "moon.stars.fill": .sparkle,
        "terminal": GlyphMotion(fade: [0.2, 1, 0.3, 1]), "terminal.fill": GlyphMotion(fade: [0.2, 1, 0.3, 1]),
        "questionmark.bubble": .swing(14, from: .bottomLeading), "flag.checkered": .swing(12, from: .bottomLeading), "desktopcomputer": .pop,
        "iphone": .swing(10, from: .bottom), "macwindow": .pop, "envelope.fill": .drop(3), "cloud.sun.fill": .sparkle, "brain.fill": .heartbeat,
        "map.fill": .unfold(1), "scalemass.fill": .swing(12, from: .top), "safari.fill": GlyphMotion(rotate: [70, 150, 360], rotateEnd: 360),
        "note.text": .unfold(1), "checklist": .tick, "text.bubble.fill": .bounce(2.5), "archivebox.fill": .unfold(2.5),
        "doc.text.magnifyingglass": .search, "chevron.left.forwardslash.chevron.right": GlyphMotion(sx: [1.22, 0.9, 1.06, 1.0]),
    ]
}

struct GlyphValues { var sx: CGFloat = 1; var sy: CGFloat = 1; var rotate = 0.0; var flip = 0.0; var dx: CGFloat = 0; var dy: CGFloat = 0; var fade = 1.0 }

/// An icon with its own motion: plays on hovering over the element that holds it (`hovering`) and on a `trigger` signal.
/// When the icon itself changes (play ↔ pause, on ↔ off) it doesn't flicker, it morphs (symbolEffect .replace)
struct Glyph: View {
    @Environment(\.hovering) private var hovering
    @Environment(\.accessibilityReduceMotion) private var still
    let name: String
    var trigger = 0
    @State private var plays = 0
    @State private var armed = false   // until it has played once, it's just a plain image: hundreds of icons in lists don't hold animators
    init(_ name: String, trigger: Int = 0) { self.name = name; self.trigger = trigger }
    var body: some View {
        Group {
            if armed { animated } else { symbol }
        }
        .onChange(of: hovering) { _, h in if h { play() } }
        .onChange(of: trigger) { play() }
    }
    private var symbol: some View { Image(systemName: name).contentTransition(.symbolEffect(.replace)) }
    private func play() {
        guard !still else { return }
        if armed { plays += 1 } else { armed = true; DispatchQueue.main.async { plays += 1 } }
    }
    private var animated: some View {
        let m = Glyphs.motion(name)
        return symbol
            .keyframeAnimator(initialValue: GlyphValues(), trigger: plays) { view, v in
                view.scaleEffect(x: v.sx, y: v.sy, anchor: m.anchor)
                    .rotationEffect(.degrees(v.rotate), anchor: m.anchor)
                    .rotation3DEffect(.degrees(v.flip), axis: (x: 0, y: 1, z: 0), perspective: Style.Motion.glyphPerspective)
                    .offset(x: v.dx, y: v.dy)
                    .opacity(v.fade)
            } keyframes: { _ in
                KeyframeTrack(\.rotate) { glyphSteps(m.rotate, end: m.rotateEnd, neutral: 0) }
                KeyframeTrack(\.flip) { glyphSteps(m.flip, end: m.flipEnd, neutral: 0) }
                KeyframeTrack(\.sx) { glyphSteps(m.sx, end: 1, neutral: 1) }
                KeyframeTrack(\.sy) { glyphSteps(m.sy, end: 1, neutral: 1) }
                KeyframeTrack(\.dx) { glyphSteps(m.dx, end: 0, neutral: 0) }
                KeyframeTrack(\.dy) { glyphSteps(m.dy, end: 0, neutral: 0) }
                KeyframeTrack(\.fade) { glyphSteps(m.fade, end: 1, neutral: 1) }
            }
    }
}

/// One icon motion track: up to five segments from `values`, then a spring to `end`. One function for every track (rotation, flip, scale, offset, opacity)
@KeyframeTrackContentBuilder<V> private func glyphSteps<V: Animatable>(_ values: [V], end: V, neutral: V) -> some KeyframeTrackContent<V> {
    let step = Style.Motion.glyphStep
    CubicKeyframe(glyphValue(values, 0, neutral), duration: step)
    CubicKeyframe(glyphValue(values, 1, neutral), duration: step)
    CubicKeyframe(glyphValue(values, 2, neutral), duration: step)
    CubicKeyframe(glyphValue(values, 3, neutral), duration: step)
    CubicKeyframe(glyphValue(values, 4, neutral), duration: step)
    SpringKeyframe(end, duration: Style.Motion.glyphSettle, spring: .bouncy)
}
private func glyphValue<V>(_ values: [V], _ i: Int, _ neutral: V) -> V { values.isEmpty ? neutral : values[min(i, values.count - 1)] }

// ---------- rolling number ----------

extension View {
    /// A number (or text with a number) changes with a rolling blur, like in costs
    func rolling<V: Equatable>(_ value: V) -> some View { modifier(Rolling(value: value)) }
}
private struct Rolling<V: Equatable>: ViewModifier {
    let value: V
    func body(content: Content) -> some View { content.contentTransition(.numericText()).motion(value: value) }
}

// ---------- hover ----------

/// The element grows slightly under the cursor and tells the icons inside about it (the hovering environment value)
struct HoverScale: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var still
    var scale: CGFloat = Style.Motion.hoverScale
    @State private var hover = false
    func body(content: Content) -> some View {
        content
            .environment(\.hovering, hover)
            .scaleEffect(hover && !still ? scale : 1)
            .onHover { hover = $0 }
            .motion(value: hover)
    }
}
extension View { func hoverScale(_ scale: CGFloat = Style.Motion.hoverScale) -> some View { modifier(HoverScale(scale: scale)) } }

// ---------- loading ----------

/// A shimmering band over a placeholder: runs left to right until the data arrives
struct Shimmer: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var still
    @Environment(\.colorScheme) private var scheme
    func body(content: Content) -> some View {
        content.overlay {
            if !still {
                TimelineView(.animation(minimumInterval: 1 / 30)) { ctx in
                    let t = ctx.date.timeIntervalSinceReferenceDate / Style.Motion.shimmerPeriod
                    let phase = t - t.rounded(.down)
                    GeometryReader { g in
                        let band = g.size.width * Style.Motion.shimmerBand   // the band runs from the left edge past the right one
                        LinearGradient(colors: [.clear, (scheme == .dark ? Color.black : Color.white).opacity(Style.Motion.shimmerStrength), .clear], startPoint: .leading, endPoint: .trailing)
                            .frame(width: band)
                            .offset(x: -band + (g.size.width + band) * phase)
                    }
                }
                .allowsHitTesting(false)
            }
        }
        .clipped()
    }
}
extension View { func shimmer() -> some View { modifier(Shimmer()) } }

/// Placeholder for one list card: an icon, two lines; shimmers
struct SkeletonCard: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        HStack(spacing: Style.Space.l) {
            Circle().fill(Style.Colors.leadBlock(scheme).opacity(Style.Motion.skeletonFill)).frame(width: Style.Size.badge, height: Style.Size.badge)
            VStack(alignment: .leading, spacing: Style.Space.s) {
                RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).fill(Style.Colors.leadBlock(scheme).opacity(Style.Motion.skeletonFill)).frame(height: Style.Space.l)
                RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).fill(Style.Colors.leadBlock(scheme).opacity(Style.Motion.skeletonFill / 2)).frame(width: Style.Size.field, height: Style.Space.m)
            }
            Spacer(minLength: 0)
        }
        .padding(Style.Space.l)
        .background(Style.Colors.leadBlock(scheme).opacity(Style.Motion.skeletonFill / 3), in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
        .shimmer()
        .clipShape(RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
    }
}

/// A list of placeholders while cards are loading: they appear one after another
struct SkeletonList: View {
    var rows = 5
    @State private var shown = false
    var body: some View {
        VStack(spacing: Style.Space.s) {
            ForEach(0..<rows, id: \.self) { i in
                SkeletonCard().opacity(shown ? 1 : 0).offset(y: shown ? 0 : Style.Motion.riseShift / 2)
                    .motion(Style.Motion.rise.delay(Double(i) * Style.Motion.listStagger), value: shown)
            }
        }
        .onAppear { shown = true }
    }
}

/// A large centered loading state: Sena's leaf breathes with a wave. Doesn't show up right away (fast replies without a flash)
struct Loading: View {
    @State private var visible = false
    var body: some View {
        LeafMark(size: Style.Size.loader, mode: .wave)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .opacity(visible ? 1 : 0)
            .scaleEffect(visible ? 1 : Style.Motion.loadingScale)
            .motion(value: visible)
            .task { try? await Task.sleep(nanoseconds: UInt64(Style.Motion.loadingDelay * 1e9)); visible = true }
    }
}

/// A small inline loading ring: used instead of the system spinner
struct ArcSpinner: View {
    @Environment(\.accessibilityReduceMotion) private var still
    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 60, paused: still)) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate
            Circle().trim(from: 0.08, to: 0.72)
                .stroke(AngularGradient(colors: [Style.Colors.accent.opacity(0), Style.Colors.accent], center: .center), style: StrokeStyle(lineWidth: Style.Size.line * 2, lineCap: .round))
                .rotationEffect(.degrees(still ? 0 : t * 360 / Style.Motion.spinPeriod))
                .frame(width: Style.Size.spinner, height: Style.Size.spinner)
        }
    }
}


/// Animation by value that respects the system's "reduce motion" setting (no animation when it's on). One shared way for the whole
/// app instead of `still ? nil : …` on every screen: `.motion(value: x)` — the spring, `.motion(Style.Motion.quick, value: x)` — a custom one
private struct MotionOn<V: Equatable>: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var still
    let animation: Animation
    let value: V
    func body(content: Content) -> some View { content.animation(still ? nil : animation, value: value) }
}
extension View {
    func motion<V: Equatable>(_ animation: Animation = Style.Motion.spring, value: V) -> some View { modifier(MotionOn(animation: animation, value: value)) }
}

/// The window is narrower than the threshold (AppModel.narrow): in the header, search collapses into an icon, and filter toggles into a list. Set once at the window's root
private struct PageSlideKey: EnvironmentKey { static let defaultValue: CGFloat = 0 }
extension EnvironmentValues { var pageSlide: CGFloat { get { self[PageSlideKey.self] } set { self[PageSlideKey.self] = newValue } } }

private struct NarrowKey: EnvironmentKey { static let defaultValue = false }
extension EnvironmentValues { var narrowWindow: Bool { get { self[NarrowKey.self] } set { self[NarrowKey.self] = newValue } } }
