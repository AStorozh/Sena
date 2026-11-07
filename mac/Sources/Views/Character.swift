import AppKit
import SwiftUI

/// An agent's character by the notch, ported from the owner's logo project (make_character.py) — flat, without its volume and light.
/// A round body with a thought bubble cut into its top-left corner and two capsule eyes. The face (emotion) says how the agent is,
/// the bubble (thought) what it is doing; both come from the server (desk.py _moods), the drawing and the eyes' behavior live here.
/// All geometry is in the original's 344×344 units, scaled to the view's size
struct AgentCharacter: View {
    let code: String
    let emotion: String
    let thought: String?
    @State private var face = Face()

    var body: some View {
        GeometryReader { g in
            let place = g.frame(in: .global)
            TimelineView(.animation(minimumInterval: 1 / 60)) { tl in
                Canvas { ctx, size in
                    let t = tl.date.timeIntervalSinceReferenceDate
                    face.step(t, emotion: emotion, toMouse: mouse(from: place))
                    ctx.scaleBy(x: size.width / Figure.side, y: size.height / Figure.side)
                    let paper = Style.Colors.characters[code] ?? Style.Colors.agent(code)   // the bubble is the body's own color
                    ctx.fill(Figure.body, with: .color(paper))
                    face.drawEyes(ctx, t)
                    ctx.fill(Path(ellipseIn: CGRect(x: 72 - Figure.bubbleR, y: 72 - Figure.bubbleR, width: Figure.bubbleR * 2, height: Figure.bubbleR * 2)),
                             with: .color(paper))
                    if let thought {
                        var inBubble = ctx
                        inBubble.translateBy(x: 72, y: 72); inBubble.scaleBy(x: 1.5, y: 1.5); inBubble.translateBy(x: -37, y: -37)
                        Thought.draw(thought, inBubble, t, paper: paper)
                    }
                }
            }
        }
    }

    /// From the character's center to the mouse, in points, y pointing down (the shelf window's place on screen plus the view's place in it)
    func mouse(from place: CGRect) -> CGVector? {
        guard let win = NotchPanel.shared.windowFrame else { return nil }
        let m = NSEvent.mouseLocation
        return CGVector(dx: m.x - (win.minX + place.midX), dy: (win.maxY - place.midY) - m.y)
    }
}

/// The character's fixed shapes (make_character.py: BODY, CUT, BUBBLE, the eyes' rest positions)
enum Figure {
    static let side: CGFloat = 344
    static let head = CGPoint(x: 172, y: 172), headR: CGFloat = 150
    static let bubbleR: CGFloat = 63             // the thought bubble as seen through its cut-out ring
    static let blocker = (x: CGFloat(-100), y: CGFloat(-100), r: CGFloat(75))   // the bubble with its ring, from the head's center: eyes keep off it
    static let eyes = [CGPoint(x: -35, y: 0), CGPoint(x: 34, y: 0)]   // rest centers from the head's center
    static let halfH: CGFloat = 30.7, stroke: CGFloat = 37.8
    static let maxShift = headR * 0.38, maxBend = headR * 0.03
    static let edgePad: CGFloat = 6, blockPad: CGFloat = 8

    /// The body: a circle with the bubble's ring cut out of its corner, both sharp tips rounded (make_character.py cutout_path)
    static let body: Path = {
        var p = Path()
        p.addLines(arc(head, headR, 185.42, -95.42))
        p.addLines(arc(CGPoint(x: 159.535, y: 40.590), 18, -95.42, -199.74))
        p.addLines(arc(CGPoint(x: 72, y: 72), 75, -19.74, 109.74))
        p.addLines(arc(CGPoint(x: 40.590, y: 159.535), 18, -70.26, -174.58))
        p.closeSubpath()
        return p
    }()

    /// Points along an arc, angles in degrees with y pointing down, from `from` to `to` in the direction of their difference
    static func arc(_ c: CGPoint, _ r: CGFloat, _ from: CGFloat, _ to: CGFloat) -> [CGPoint] {
        let n = max(4, Int(abs(to - from) / 3))
        return (0...n).map { i in
            let a = (from + (to - from) * CGFloat(i) / CGFloat(n)) * .pi / 180
            return CGPoint(x: c.x + r * cos(a), y: c.y + r * sin(a))
        }
    }

    static let spiral: Path = {   // make_character.py spiral_path: 1.75 turns from r 4 to 26
        var p = Path()
        let total: CGFloat = 1.75 * 360
        p.addLines(stride(from: CGFloat(0), through: total, by: 12).map { a in
            let r = 4 + 22 * a / total, rad = a * .pi / 180
            return CGPoint(x: r * cos(rad), y: r * sin(rad))
        })
        return p
    }()

    static let heart: Path = {   // drawn around the origin, as wide as tall
        var p = Path()
        p.move(to: CGPoint(x: 0, y: 28))
        p.addCurve(to: CGPoint(x: -28, y: -8), control1: CGPoint(x: -16, y: 16), control2: CGPoint(x: -28, y: 4))
        p.addCurve(to: CGPoint(x: -10, y: -26), control1: CGPoint(x: -28, y: -20), control2: CGPoint(x: -18, y: -26))
        p.addCurve(to: CGPoint(x: 0, y: -19), control1: CGPoint(x: -4, y: -26), control2: CGPoint(x: -1, y: -22))
        p.addCurve(to: CGPoint(x: 10, y: -26), control1: CGPoint(x: 1, y: -22), control2: CGPoint(x: 4, y: -26))
        p.addCurve(to: CGPoint(x: 28, y: -8), control1: CGPoint(x: 18, y: -26), control2: CGPoint(x: 28, y: -20))
        p.addCurve(to: CGPoint(x: 0, y: 28), control1: CGPoint(x: 28, y: 4), control2: CGPoint(x: 16, y: 16))
        p.closeSubpath()
        return p
    }()

    static let cross: Path = {
        var p = Path()
        p.move(to: CGPoint(x: -20, y: -20)); p.addLine(to: CGPoint(x: 20, y: 20))
        p.move(to: CGPoint(x: 20, y: -20)); p.addLine(to: CGPoint(x: -20, y: 20))
        return p
    }()

    static let star: Path = {   // a four-pointed star around the origin
        var p = Path()
        let s: CGFloat = 1.15
        func q(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: (x - 37) * s, y: (y - 37) * s) }
        p.move(to: q(37, 10))
        p.addCurve(to: q(64, 37), control1: q(39.5, 27), control2: q(47, 34.5))
        p.addCurve(to: q(37, 64), control1: q(47, 39.5), control2: q(39.5, 47))
        p.addCurve(to: q(10, 37), control1: q(34.5, 47), control2: q(27, 39.5))
        p.addCurve(to: q(37, 10), control1: q(27, 34.5), control2: q(34.5, 27))
        p.closeSubpath()
        return p
    }()
}

/// An emotion: the eye's shape and, more than that, the gaze's habits (make_character.py EMOTIONS).
/// len, width, tilt, bend, dy — the eye; bx, by — where the gaze rests (by > 0 is down), rx, ry — how far it wanders, ease — how fast
/// it moves, gmin…gmax — the pause between glances, fw — how it takes the mouse (1 follows, 0 doesn't see it), still — eyes don't wander
struct Mood {
    var len: CGFloat, width: CGFloat, tilt: CGFloat, bend: CGFloat, dy: CGFloat
    var bx: CGFloat = 0, by: CGFloat = 0, rx: CGFloat = 0.9, ry: CGFloat = 0.9, ease: CGFloat = 0.07
    var gmin: Double = 0.9, gmax: Double = 3.8, fw: CGFloat = 1, still = false
    var blink = Blink.squash, bmin: Double = 1.2, bmax: Double = 9
    var shape: String? = nil

    enum Blink { case squash, slow, pulse, off }

    // What each face means is decided by the server (desk.py _moods): calm — free or working smoothly; surprise — a task just given,
    // listening, waiting for Tema; tense — a long step; angry — its tools keep failing; sad — failed or rated low; happy — just done
    // or speaking; sleepy — free for long or the core is off; dizzy — the model overloaded or two tasks at once; ko — its error;
    // love — rated 4; wow — rated 5
    static let all: [String: Mood] = [
        "calm": Mood(len: 1, width: 1, tilt: 0, bend: 0, dy: 0),
        "tense": Mood(len: 0.9, width: 0.42, tilt: -75, bend: 0, dy: 0, by: 0.05, rx: 0.8, ry: 0.7, ease: 0.22, gmin: 0.26, gmax: 0.76, bmin: 2.2, bmax: 7),
        "surprise": Mood(len: 0.12, width: 1.85, tilt: 0, bend: 0, dy: -6, by: -0.1, rx: 0.85, ry: 0.8, ease: 0.3, gmin: 0.18, gmax: 0.52, blink: .off),
        "sad": Mood(len: 0.7, width: 0.9, tilt: 38, bend: 0, dy: 6, by: 0.45, rx: 0.7, ry: 0.5, ease: 0.03, gmin: 2.2, gmax: 5.2, fw: 0.55,
                    blink: .slow, bmin: 2.5, bmax: 8),
        "happy": Mood(len: 0.55, width: 1, tilt: 90, bend: -16, dy: 6, by: -0.1, rx: 0.85, ry: 0.8, ease: 0.12, gmin: 0.6, gmax: 1.8, blink: .off),
        "sleepy": Mood(len: 0.95, width: 0.3, tilt: 90, bend: 6, dy: 10, by: 0.62, rx: 0.3, ry: 0.26, ease: 0.018, gmin: 3.2, gmax: 7.5, fw: 0.35,
                       blink: .slow, bmin: 1.4, bmax: 4.5),
        "angry": Mood(len: 0.7, width: 1.1, tilt: -50, bend: 0, dy: 2, by: 0.18, rx: 0.7, ry: 0.55, ease: 0.055, gmin: 1.3, gmax: 3.2, fw: -0.75,
                      bmin: 2.5, bmax: 9),
        "ko": Mood(len: 0.05, width: 1.8, tilt: 0, bend: 0, dy: 0, by: 0.3, rx: 0, ry: 0, ease: 0.02, fw: 0, still: true, blink: .off, shape: "cross"),
        "love": Mood(len: 0.05, width: 1.8, tilt: 0, bend: 0, dy: 0, by: -0.18, rx: 0.8, ry: 0.75, ease: 0.035, gmin: 1.5, gmax: 3.6,
                     blink: .pulse, bmin: 0.9, bmax: 2.2, shape: "heart"),
        "dizzy": Mood(len: 0.05, width: 1.9, tilt: 0, bend: 0, dy: 0, rx: 0, ry: 0, ease: 0.05, fw: 0, still: true, blink: .off, shape: "spiral"),
        "wow": Mood(len: 0.05, width: 1.9, tilt: 0, bend: 0, dy: 0, by: -0.15, rx: 0.9, ry: 0.85, ease: 0.18, gmin: 0.42, gmax: 1.2,
                    blink: .pulse, bmin: 1.2, bmax: 3.2, shape: "star"),
    ]
    static func named(_ id: String) -> Mood { all[id] ?? all["calm"]! }
}

/// The live state of one face: the eye's shape easing to the emotion's, the gaze easing to its target, blinks
final class Face {
    struct Eye { var len: CGFloat = 1, width: CGFloat = 1, tilt: CGFloat = 0, bend: CGFloat = 0, dy: CGFloat = 0 }
    static let transition = 0.42   // seconds to change the eye's shape
    static let mouseFresh = 3.0    // the mouse moved this recently — the eyes watch it; otherwise they look around on their own

    var mood = Mood.named("calm"), moodShape = Eye()
    var eye = Eye(), from = Eye(), to = Eye(), shiftStart = 0.0, progress = 1.0
    var shapeNow: String?, shapeTarget: String?, morph: CGFloat = 1
    var current = CGVector.zero, target = CGVector.zero, gazeN = CGVector.zero
    var last = 0.0, nextGaze = 0.0, nextBlink = 0.0, blinkStart = -10.0, blinkMode = Mood.Blink.off
    var lastMouse: CGVector?, mouseMoved = -10.0
    var emotion = ""

    func step(_ t: Double, emotion id: String, toMouse: CGVector?) {
        let dt = last > 0 ? min(0.1, t - last) : 1 / 60
        last = t
        if id != emotion {
            emotion = id
            change(to: Mood.named(id), t)
        }
        if progress < 1 {   // the eye's shape, with a small overshoot at the end
            progress = min(1, (t - shiftStart) / Face.transition)
            let e = Face.easeBack(progress)
            eye = Eye(len: from.len + (to.len - from.len) * e, width: from.width + (to.width - from.width) * e, tilt: from.tilt + (to.tilt - from.tilt) * e,
                      bend: from.bend + (to.bend - from.bend) * e, dy: from.dy + (to.dy - from.dy) * e)
        }
        if shapeNow != shapeTarget {   // a shape changes by shrinking to a point and popping back, never by fading
            morph -= CGFloat(dt / (Face.transition * 0.45))
            if morph <= 0 { morph = 0; shapeNow = shapeTarget }
        } else if morph < 1 {
            morph = min(1, morph + CGFloat(dt / (Face.transition * 0.55)))
        }
        // the gaze: the mouse while it moves, otherwise the emotion's own glances
        if let m = toMouse {
            if lastMouse.map({ abs($0.dx - m.dx) + abs($0.dy - m.dy) > 0.5 }) ?? true { mouseMoved = t }   // the shelf just opened: look at the mouse first
            lastMouse = m
        }
        if let m = toMouse, t - mouseMoved < Face.mouseFresh, mood.fw != 0 {
            let d = max(hypot(m.dx, m.dy), 1), reachOut = min(1, d / 160)
            look(m.dx / d * reachOut, m.dy / d * reachOut)
        } else if t >= nextGaze {
            pickGaze()
            nextGaze = t + Double.random(in: mood.gmin...max(mood.gmin, mood.gmax))
        }
        if mood.blink != .off, t >= nextBlink {
            blinkStart = t; blinkMode = mood.blink
            nextBlink = t + Double.random(in: mood.bmin...mood.bmax)
        }
        let k = 1 - pow(1 - mood.ease, CGFloat(dt * 60))   // the same heaviness at any frame rate
        current.dx += (target.dx - current.dx) * k
        current.dy += (target.dy - current.dy) * k
    }

    func change(to next: Mood, _ t: Double) {
        var tilt = next.tilt, bend = next.bend   // a capsule turned by 180° looks the same: take the shorter way round
        while tilt - eye.tilt > 90 { tilt -= 180; bend = -bend }
        while tilt - eye.tilt < -90 { tilt += 180; bend = -bend }
        mood = next
        from = eye
        to = Eye(len: next.len, width: next.width, tilt: tilt, bend: bend, dy: next.dy)
        shiftStart = t; progress = 0
        shapeTarget = next.shape
        blinkMode = .off
        nextBlink = t + Double.random(in: 0.3...2.5)
        pickGaze()
        nextGaze = t + Double.random(in: next.gmin...max(next.gmin, next.gmax))
    }

    /// The mouse's direction through the emotion's habit: calm follows it, sad half-heartedly, sleepy barely lifts its eyes
    func look(_ nx: CGFloat, _ ny: CGFloat) {
        var ax = mood.bx + nx * mood.fw * mood.rx, ay = mood.by + ny * mood.fw * mood.ry
        let len = hypot(ax, ay)
        if len > 1 { ax /= len; ay /= len }
        aim(ax, ay)
    }

    func pickGaze() {
        var nx = mood.bx, ny = mood.by
        if !mood.still {
            let r = sqrt(CGFloat.random(in: 0...1)), a = CGFloat.random(in: 0...(2 * .pi))
            nx += cos(a) * r * mood.rx; ny += sin(a) * r * mood.ry
        }
        let len = hypot(nx, ny)
        if len > 1 { nx /= len; ny /= len }
        aim(nx, ny)
    }

    /// A direction (−1…1) into a shift: the share of the free room that way, so any eye walks the whole head
    func aim(_ nx: CGFloat, _ ny: CGFloat) {
        gazeN = CGVector(dx: nx, dy: ny)
        let len = hypot(nx, ny)
        guard len > 1e-6 else { target = .zero; return }
        let ux = nx / len, uy = ny / len
        var lo: CGFloat = 0, hi = Figure.headR   // how far that way before the eye meets the body's edge, for the shape it's heading to
        for _ in 0..<14 {
            let mid = (lo + hi) / 2
            let c = constrain(ux * mid, uy * mid, to, edgeOnly: true)
            if hypot(c.dx - ux * mid, c.dy - uy * mid) < 0.5 { lo = mid } else { hi = mid }
        }
        target = CGVector(dx: ux * lo * len, dy: uy * lo * len)
    }

    static func mirror(_ i: Int) -> CGFloat { Figure.eyes[i].x < 0 ? 1 : -1 }

    func ends(_ i: Int, _ tx: CGFloat, _ ty: CGFloat, _ e: Eye) -> (a: CGPoint, b: CGPoint) {
        let h = Figure.halfH * e.len, ang = e.tilt * Face.mirror(i) * .pi / 180
        let cx = Figure.eyes[i].x + tx, cy = Figure.eyes[i].y + ty + e.dy
        return (CGPoint(x: cx + h * sin(ang), y: cy - h * cos(ang)), CGPoint(x: cx - h * sin(ang), y: cy + h * cos(ang)))
    }

    static func nearest(_ p: CGPoint, _ a: CGPoint, _ b: CGPoint) -> CGPoint {
        let vx = b.x - a.x, vy = b.y - a.y, len2 = vx * vx + vy * vy
        let t = len2 > 0 ? max(0, min(1, ((p.x - a.x) * vx + (p.y - a.y) * vy) / len2)) : 0
        return CGPoint(x: a.x + t * vx, y: a.y + t * vy)
    }

    /// Fits a wished shift into the allowed zone: the eye doesn't leave the body or ride onto the bubble — it slides along them
    func constrain(_ x: CGFloat, _ y: CGFloat, _ e: Eye, edgeOnly: Bool = false) -> CGVector {
        var tx = x, ty = y
        let cap = Figure.stroke * e.width / 2 + (abs(e.bend) + Figure.maxBend) / 2
        let o = CGPoint(x: Figure.blocker.x, y: Figure.blocker.y)
        var edgeLim: [CGFloat] = [], need: [CGFloat] = []
        for i in Figure.eyes.indices {
            let r = ends(i, 0, 0, e)
            edgeLim.append(max(Figure.headR - Figure.edgePad - cap, max(hypot(r.a.x, r.a.y), hypot(r.b.x, r.b.y))))
            let q = Face.nearest(o, r.a, r.b)
            need.append(min(Figure.blocker.r + cap + Figure.blockPad, hypot(q.x - o.x, q.y - o.y)))
        }
        for _ in 0..<8 {
            var moved = false
            for i in Figure.eyes.indices {
                var r = ends(i, tx, ty, e)
                let da = hypot(r.a.x, r.a.y), db = hypot(r.b.x, r.b.y), far = da > db ? r.a : r.b, fd = max(da, db)
                if fd > edgeLim[i], fd > 0 {
                    let k = (fd - edgeLim[i]) / fd
                    tx -= far.x * k; ty -= far.y * k; moved = true
                }
                if edgeOnly { continue }
                r = ends(i, tx, ty, e)
                let q = Face.nearest(o, r.a, r.b), d = hypot(q.x - o.x, q.y - o.y)
                if d < need[i], d > 0 {
                    let push = (need[i] - d) / d
                    tx += (q.x - o.x) * push; ty += (q.y - o.y) * push; moved = true
                }
            }
            if !moved { break }
        }
        return CGVector(dx: tx, dy: ty)
    }

    /// The blink's scale now: squash — a shutter, slow — heavy and not quite closed, pulse — a short push for stars
    func blinkScale(_ t: Double) -> CGSize {
        let (length, low, a, b): (Double, CGFloat, Double, Double) = switch blinkMode {
        case .squash: (0.34, 0.08, 0.38, 0.58)
        case .slow: (0.95, 0.14, 0.42, 0.62)
        case .pulse: (0.5, 1.18, 0.45, 0.45)
        case .off: (0, 1, 0, 0)
        }
        let p = length > 0 ? (t - blinkStart) / length : 1
        guard p >= 0, p < 1 else { return CGSize(width: 1, height: 1) }
        let w = CGFloat(p < a ? Thought.smooth(p / a) : p <= b ? 1 : Thought.smooth((1 - p) / (1 - b)))
        let v = 1 + (low - 1) * w
        return blinkMode == .pulse ? CGSize(width: v, height: v) : CGSize(width: 1, height: v)
    }

    func drawEyes(_ ctx: GraphicsContext, _ t: Double) {
        let c = constrain(current.dx, current.dy, eye)
        let nx = c.dx / Figure.maxShift, ny = max(-1, min(1, c.dy / Figure.maxShift))
        let near = pow(min(1, hypot(nx, ny)), 2)
        let tiltRad = near * (nx * 8 + ny * 4) * .pi / 180, bendX = near * nx * Figure.maxBend
        let vis = morph <= 0 ? 0 : (shapeNow == shapeTarget ? Face.easeBack(Double(morph)) : Face.easeInOut(Double(morph)))
        let capScale = shapeNow == nil ? CGFloat(vis) : 0, specScale = shapeNow == nil ? 0 : CGFloat(vis)
        let centers = Figure.eyes.indices.map { i in
            CGPoint(x: Figure.head.x + Figure.eyes[i].x + c.dx, y: Figure.head.y + Figure.eyes[i].y + c.dy + eye.dy)
        }
        var eyes = ctx   // the blink squeezes both eyes around their common middle
        let mid = CGPoint(x: (centers[0].x + centers[1].x) / 2, y: (centers[0].y + centers[1].y) / 2), s = blinkScale(t)
        eyes.translateBy(x: mid.x, y: mid.y); eyes.scaleBy(x: s.width, y: s.height); eyes.translateBy(x: -mid.x, y: -mid.y)
        if emotion == "ko" {   // knocked out: the look hangs and sways
            let w = CGFloat(sin(t / 2.4 * 2 * .pi))
            eyes.translateBy(x: mid.x + 2 * w, y: mid.y); eyes.rotate(by: .degrees(1.5 * w)); eyes.translateBy(x: -mid.x, y: -mid.y)
        }
        for i in Figure.eyes.indices {
            let m = Face.mirror(i), p = centers[i]
            var one = eyes
            one.translateBy(x: p.x, y: p.y)
            if capScale > 0.001 {
                var cap = one
                cap.scaleBy(x: capScale, y: capScale)
                let h = Figure.halfH * eye.len * (1 - near * 0.18), ang = tiltRad + eye.tilt * m * .pi / 180, bend = bendX + eye.bend * m
                func pt(_ lx: CGFloat, _ ly: CGFloat) -> CGPoint { CGPoint(x: lx * cos(ang) - ly * sin(ang), y: lx * sin(ang) + ly * cos(ang)) }
                var path = Path()
                path.move(to: pt(0, -h)); path.addQuadCurve(to: pt(0, h), control: pt(bend, 0))
                cap.stroke(path, with: .color(.black), style: StrokeStyle(lineWidth: Figure.stroke * eye.width, lineCap: .round))
            }
            if specScale > 0.001, let shape = shapeNow {
                var spec = one
                spec.scaleBy(x: specScale, y: specScale)
                switch shape {
                case "spiral":
                    spec.rotate(by: .degrees(t.truncatingRemainder(dividingBy: 3) / 3 * 360))
                    spec.stroke(Figure.spiral, with: .color(.black), style: StrokeStyle(lineWidth: 9, lineCap: .round, lineJoin: .round))
                case "cross": spec.stroke(Figure.cross, with: .color(.black), style: StrokeStyle(lineWidth: 14, lineCap: .round))
                case "heart": spec.fill(Figure.heart, with: .color(.black))
                default: spec.fill(Figure.star, with: .color(.black))
                }
            }
        }
    }

    static func easeInOut(_ t: Double) -> Double { t < 0.5 ? 4 * t * t * t : 1 - pow(-2 * t + 2, 3) / 2 }
    static func easeBack(_ t: Double) -> Double { let c = 1.1; return 1 + (c + 1) * pow(t - 1, 3) + c * pow(t - 1, 2) }
}

/// The thoughts, drawn in the original's 74×74 box (make_character.py THOUGHTS). Each one is a kind of work (desk.py THOUGHTS):
/// the whole sign is always drawn, only a little motion runs through it, so it reads at any moment
enum Thought {
    static let line: CGFloat = 14
    static let period = 2.2

    static func smooth(_ x: Double) -> Double { (1 - cos(.pi * min(1, max(0, x)))) / 2 }
    /// 0 at the period's start and end, 1 at its middle (the original's "0%, 100% → A; 50% → B" keyframes)
    static func wave(_ t: Double, _ delay: Double = 0, _ length: Double = period) -> CGFloat {
        CGFloat(smooth(1 - abs(phase(t, delay, length) * 2 - 1)))
    }
    static func phase(_ t: Double, _ delay: Double = 0, _ length: Double = period) -> Double { ((t + delay) / length).truncatingRemainder(dividingBy: 1) }
    /// Through keyframes [(share of the period, value)], smoothly between them
    static func keys(_ p: Double, _ k: [(Double, CGFloat)]) -> CGFloat {
        for i in 1..<k.count where p <= k[i].0 {
            let (a, va) = k[i - 1], (b, vb) = k[i]
            return va + (vb - va) * CGFloat(smooth(b > a ? (p - a) / (b - a) : 1))
        }
        return k.last!.1
    }

    static func dot(_ x: CGFloat, _ y: CGFloat, _ r: CGFloat = 7) -> Path { Path(ellipseIn: CGRect(x: x - r, y: y - r, width: r * 2, height: r * 2)) }
    static func lines(_ pts: [CGPoint]) -> Path { var p = Path(); p.addLines(pts); return p }
    static func pill(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat, _ r: CGFloat) -> Path {
        Path(roundedRect: CGRect(x: x, y: y, width: w, height: h), cornerSize: CGSize(width: r, height: r))
    }
    static func turned(_ ctx: GraphicsContext, around c: CGPoint, by deg: CGFloat, scale: CGSize = CGSize(width: 1, height: 1), move: CGVector = .zero) -> GraphicsContext {
        var g = ctx
        g.translateBy(x: c.x + move.dx, y: c.y + move.dy); g.rotate(by: .degrees(deg)); g.scaleBy(x: scale.width, y: scale.height); g.translateBy(x: -c.x, y: -c.y)
        return g
    }

    /// paper — the bubble's color: holes in a sign (the calculator's screen and keys) are cut in it
    static func draw(_ id: String, _ ctx: GraphicsContext, _ t: Double, paper: Color) {
        let ink = GraphicsContext.Shading.color(.black), style = StrokeStyle(lineWidth: line, lineCap: .round, lineJoin: .round)
        func stroke(_ g: GraphicsContext, _ p: Path, _ opacity: CGFloat = 1) { var g = g; g.opacity = opacity; g.stroke(p, with: ink, style: style) }
        func fill(_ g: GraphicsContext, _ p: Path, _ opacity: CGFloat = 1) { var g = g; g.opacity = opacity; g.fill(p, with: ink) }
        let center = CGPoint(x: 37, y: 37)
        switch id {
        case "dots":   // thinking: three dots in a wave
            for (i, x) in [CGFloat(15), 37, 59].enumerated() { fill(ctx, dot(x, 37 + 6 - 12 * wave(t, Double(i) * 0.37))) }
        case "search":   // searching: a magnifier circling
            let a = phase(t) * 2 * .pi
            var g = ctx; g.translateBy(x: 7 * cos(a), y: 7 * sin(a))
            stroke(g, Path(ellipseIn: CGRect(x: 18, y: 18, width: 28, height: 28))); stroke(g, lines([CGPoint(x: 42, y: 42), CGPoint(x: 53, y: 53)]))
        case "read", "upload":   // reading something in (an arrow down) or sending a file out (up)
            let down = id == "read", y = keys(phase(t), [(0, down ? -5 : 5), (0.3, down ? 6 : -6), (1, down ? -5 : 5)])
            var g = ctx; g.translateBy(x: 0, y: y)
            let (tip, back): (CGFloat, CGFloat) = down ? (54, 18) : (20, 56), wing = down ? tip - 13 : tip + 13
            stroke(g, lines([CGPoint(x: 37, y: back), CGPoint(x: 37, y: tip)]))
            stroke(g, lines([CGPoint(x: 24, y: wing), CGPoint(x: 37, y: tip), CGPoint(x: 50, y: wing)]))
        case "memory":   // memory: a grid of cells lighting up in turn
            let cells: [CGPoint] = [CGPoint(x: 16, y: 26.5), CGPoint(x: 37, y: 26.5), CGPoint(x: 58, y: 26.5),
                                    CGPoint(x: 16, y: 47.5), CGPoint(x: 37, y: 47.5), CGPoint(x: 58, y: 47.5)]
            for (i, c) in cells.enumerated() {
                let w = wave(t, Double(i) * 0.18)
                fill(turned(ctx, around: c, by: 0, scale: CGSize(width: 0.55 + 0.45 * w, height: 0.55 + 0.45 * w)), dot(c.x, c.y), 0.45 + 0.55 * w)
            }
        case "code":   // writing code: angle brackets tapping in turn like keys
            for (i, side) in [CGFloat(1), -1].enumerated() {
                let p = phase(t, Double(i) * 0.55, 1.1), k = keys(p, [(0, 0), (0.12, 1), (0.42, 0), (1, 0)])
                var g = ctx; g.translateBy(x: 3 * k * side, y: 0)
                let x0: CGFloat = side > 0 ? 26 : 48, x1: CGFloat = side > 0 ? 13 : 61
                stroke(g, lines([CGPoint(x: x0, y: 20), CGPoint(x: x1, y: 37), CGPoint(x: x0, y: 54)]), 0.5 + 0.5 * k)
            }
        case "send":   // handing over: to another agent, a message, a mail
            let w = wave(t), g = turned(ctx, around: center, by: 0, scale: CGSize(width: 0.82 + 0.3 * w, height: 0.82 + 0.3 * w))
            fill(ctx, dot(37, 37))
            stroke(g, lines(Figure.arc(center, 20, 139.5, 220.5)), 0.35 + 0.65 * w); stroke(g, lines(Figure.arc(center, 20, -40.5, 40.5)), 0.35 + 0.65 * w)
        case "question":   // waiting for Tema's answer
            var hook = Path()
            hook.move(to: CGPoint(x: 24, y: 27.5))
            hook.addCurve(to: CGPoint(x: 38, y: 13.5), control1: CGPoint(x: 24, y: 19.5), control2: CGPoint(x: 31, y: 13.5))
            hook.addCurve(to: CGPoint(x: 51, y: 25.5), control1: CGPoint(x: 45, y: 13.5), control2: CGPoint(x: 51, y: 18.5))
            hook.addCurve(to: CGPoint(x: 38, y: 41.5), control1: CGPoint(x: 51, y: 33.5), control2: CGPoint(x: 38, y: 35.5))
            let w = wave(t)
            stroke(turned(ctx, around: CGPoint(x: 38, y: 41.5), by: -10 + 20 * w), hook)
            fill(turned(ctx, around: CGPoint(x: 38, y: 60.5), by: 0, scale: CGSize(width: 0.85 + 0.29 * w, height: 0.85 + 0.29 * w)), dot(38, 60.5))
        case "judge":   // weighing the work: a gavel striking
            fill(ctx, pill(14, 56, 46, 12, 6))
            let a = keys(phase(t), [(0, -26), (0.28, 6), (0.44, -3), (1, -26)]), g = turned(ctx, around: CGPoint(x: 37, y: 46), by: a)
            fill(g, pill(20, 16, 34, 18, 9)); stroke(g, lines([CGPoint(x: 37, y: 34), CGPoint(x: 37, y: 46)]))
        case "count":   // numbers: prices, costs, statistics
            let p = phase(t), digits: CGFloat = p < 0.25 ? 7 : p < 0.5 ? 12 : p < 0.75 ? 17 : p < 0.94 ? 23 : 7
            fill(ctx, pill(17, 10, 40, 54, 12))
            ctx.fill(pill(23, 16.5, 28, 12, 6), with: .color(paper))
            fill(ctx, pill(25.5, 19, digits, 7, 3.5))
            let keysAt: [(CGFloat, CGFloat, Double)] = [(29, 39, 0), (45, 54, 1.65), (45, 39, 1.1), (29, 54, 0.55)]
            for k in keysAt {   // the keys are pressed one after another, in step with the digits
                let r = keys(phase(t, k.2), [(0, 6), (0.08, 2), (0.14, 2), (0.3, 6), (1, 6)])
                ctx.fill(dot(k.0, k.1, r), with: .color(paper))
            }
        case "bell":   // watching: alerts, reminders, schedules
            var bell = Path()
            bell.move(to: CGPoint(x: 37, y: 13))
            bell.addCurve(to: CGPoint(x: 53, y: 35), control1: CGPoint(x: 47, y: 13), control2: CGPoint(x: 53, y: 23))
            bell.addCurve(to: CGPoint(x: 57, y: 43), control1: CGPoint(x: 53, y: 40), control2: CGPoint(x: 57, y: 40))
            bell.addCurve(to: CGPoint(x: 51, y: 49), control1: CGPoint(x: 57, y: 46.3), control2: CGPoint(x: 53.7, y: 49))
            bell.addLine(to: CGPoint(x: 23, y: 49))
            bell.addCurve(to: CGPoint(x: 17, y: 43), control1: CGPoint(x: 19.7, y: 49), control2: CGPoint(x: 17, y: 46.3))
            bell.addCurve(to: CGPoint(x: 21, y: 35), control1: CGPoint(x: 17, y: 40), control2: CGPoint(x: 21, y: 40))
            bell.addCurve(to: CGPoint(x: 37, y: 13), control1: CGPoint(x: 21, y: 23), control2: CGPoint(x: 27, y: 13))
            bell.closeSubpath()
            let w = wave(t)
            fill(turned(ctx, around: CGPoint(x: 37, y: 15.9), by: -11 + 22 * w), bell)
            fill(ctx, dot(37 - 6 + 12 * w, 59))
        case "plus":   // making something new; it beats instead of turning: half-turned it would read as the failure's cross
            let s = 0.94 + 0.16 * wave(t), g = turned(ctx, around: center, by: 0, scale: CGSize(width: s, height: s))
            stroke(g, lines([CGPoint(x: 37, y: 20), CGPoint(x: 37, y: 54)])); stroke(g, lines([CGPoint(x: 20, y: 37), CGPoint(x: 54, y: 37)]))
        case "stop":   // stopped by Tema: a square turning a quarter at a time
            fill(turned(ctx, around: center, by: CGFloat(phase(t, 0, 4.4)) * 90), pill(22, 22, 30, 30, 10))
        case "minus":   // removing: mail, rules, alerts, memories
            let s = 1 - 0.3 * wave(t)
            stroke(ctx, lines([CGPoint(x: 37 - 17 * s, y: 37), CGPoint(x: 37 + 17 * s, y: 37)]))
        case "tap":   // pressing on the screen: clicks and keys, a touch spreading rings
            for delay in [0.0, 1.1] {
                let p = phase(t, delay), r = keys(p, [(0, 13), (0.3, 18), (1, 30)]), o = keys(p, [(0, 0), (0.3, 0.95), (1, 0)])
                stroke(ctx, dot(37, 37, r), o)
            }
            fill(ctx, dot(37, 37))
        case "play":   // running something: a command, a change, a work
            let w = wave(t), g = turned(ctx, around: CGPoint(x: 42, y: 37), by: 0, scale: CGSize(width: 0.94 + 0.16 * w, height: 0.94 + 0.16 * w))
            var tri = lines([CGPoint(x: 29, y: 21), CGPoint(x: 55, y: 37), CGPoint(x: 29, y: 53)]); tri.closeSubpath()
            fill(g, tri); stroke(g, tri)
        case "tune":   // adjusting settings: a highlight running along three lines
            let rows = [(CGFloat(14), CGFloat(18), CGFloat(44)), (28, 37, 58), (14, 56, 38)]
            for (i, r) in rows.enumerated() {
                let row = lines([CGPoint(x: r.0, y: r.1), CGPoint(x: r.2, y: r.1)])
                stroke(ctx, row, 0.3)
                ctx.stroke(row, with: ink, style: StrokeStyle(lineWidth: line, lineCap: .round, dash: [10, 30],
                                                              dashPhase: CGFloat(phase(t, Double(i) * 0.73)) * 40))
            }
        case "signal":   // checking a service or a site is alive
            fill(ctx, dot(37, 56), 0.35 + 0.65 * wave(t))
            stroke(ctx, lines(Figure.arc(CGPoint(x: 37, y: 56), 16, 225, 315)), 0.35 + 0.65 * wave(t, 0.27))
            stroke(ctx, lines(Figure.arc(CGPoint(x: 37, y: 56), 34, 225, 315)), 0.35 + 0.65 * wave(t, 0.55))
        case "media":   // music and video: an equalizer
            for (i, x) in [CGFloat(8.5), 30, 51.5].enumerated() {
                let w = wave(t, Double(i) * 0.73)
                fill(ctx, pill(x, 30 - 14 * w, 14, 14 + 28 * w, 7))
            }
        case "cloud":   // the weather, the cloud drive: a floating cloud
            var g = ctx; g.translateBy(x: 0, y: 6 - 12 * wave(t))
            for p in [dot(26, 38, 11), dot(37, 31, 14), dot(48, 38, 11), Path(CGRect(x: 26, y: 38, width: 22, height: 11))] { fill(g, p) }
        case "horn":   // telling people: a message, Telegram, a mail
            let w = wave(t), s = 1.35 * (0.94 + 0.16 * w)   // the original's horn is the smallest sign in the set: drawn larger to match
            let g = turned(ctx, around: CGPoint(x: 34, y: 37), by: 0, scale: CGSize(width: s, height: s), move: CGVector(dx: 3, dy: 0))
            fill(g, pill(23, 30, 14, 14, 5))
            var cone = lines([CGPoint(x: 37, y: 37), CGPoint(x: 45, y: 23), CGPoint(x: 45, y: 51)]); cone.closeSubpath()
            fill(g, cone); stroke(g, cone)
        case "listen":   // listening to Tema in a voice conversation
            let w = wave(t)
            stroke(ctx, lines([CGPoint(x: 37, y: 16), CGPoint(x: 37, y: 30)]))
            stroke(turned(ctx, around: CGPoint(x: 37, y: 36), by: 0, scale: CGSize(width: 0.94 + 0.18 * w, height: 0.94 + 0.18 * w)),
                   lines(Figure.arc(CGPoint(x: 37, y: 36), 19, 180, 0)), 0.8 + 0.2 * w)
        case "pause":   // free: no task now
            let w = wave(t)
            stroke(ctx, lines([CGPoint(x: 28 - 6 * w, y: 21), CGPoint(x: 28 - 6 * w, y: 53)]))
            stroke(ctx, lines([CGPoint(x: 46 + 6 * w, y: 21), CGPoint(x: 46 + 6 * w, y: 53)]))
        case "spinner":   // waiting for an overloaded model
            let g = turned(ctx, around: center, by: CGFloat(phase(t, 0, 1.1)) * 360)
            g.stroke(dot(37, 37, 22), with: ink, style: StrokeStyle(lineWidth: line, lineCap: .round, dash: [99, 39]))
        case "check":   // done and checked
            let s = keys(phase(t), [(0, 1), (0.12, 1.18), (0.24, 0.94), (0.4, 1), (1, 1)])
            stroke(turned(ctx, around: center, by: 0, scale: CGSize(width: s, height: s)), lines([CGPoint(x: 22, y: 38), CGPoint(x: 32, y: 48), CGPoint(x: 52, y: 26)]))
        case "cross":   // it didn't work out
            let g = turned(ctx, around: center, by: -9 + 18 * wave(t))
            stroke(g, lines([CGPoint(x: 24, y: 24), CGPoint(x: 50, y: 50)])); stroke(g, lines([CGPoint(x: 50, y: 24), CGPoint(x: 24, y: 50)]))
        case "exclaim":   // done only in part: worth a look
            let w = wave(t)
            var bar = ctx; bar.translateBy(x: 0, y: 4 - 9 * w)
            stroke(bar, lines([CGPoint(x: 37, y: 17), CGPoint(x: 37, y: 35)]))
            fill(turned(ctx, around: CGPoint(x: 37, y: 54), by: 0, scale: CGSize(width: 0.88 + 0.26 * w, height: 0.88 + 0.26 * w), move: CGVector(dx: 0, dy: 3 - 6 * w)), dot(37, 54))
        default:
            break
        }
    }
}
