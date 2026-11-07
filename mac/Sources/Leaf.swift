// Sena's leaf: the same outlines as in android/logo.svg. The app icon and menu bar icon are drawn by the app itself.
import Cocoa
import SwiftUI

/// The yellow leaf — mustard from the brand palette (Style.Colors.palette[0]): one color for the leaf everywhere
let YELLOW = NSColor(Style.Colors.palette[0])
let leafBox = NSRect(x: 0, y: 0, width: 686, height: 602)
let leafPaths = [
    "M318 24 C296 25 270 33 265 40 C262 44 262 51 266 57 C269 62 275 68 288 76 C294 79 304 87 311 92 C331 108 332 108 338 108 C348 108 352 103 358 80 C360 71 362 61 364 57 C370 42 368 34 361 28 C356 24 340 22 318 24 Z",
    "M403 34 C392 38 389 43 381 74 C373 102 372 111 376 115 C380 120 384 122 395 121 C415 121 432 115 466 96 C483 86 488 82 488 76 C488 73 487 70 486 68 C482 61 462 50 438 40 C419 33 411 32 403 34 Z",
    "M211 73 C179 77 144 103 118 142 C102 167 94 198 98 226 C102 260 122 295 153 324 C164 335 214 379 244 406 C302 456 305 458 318 458 C325 458 326 458 330 456 C334 453 339 445 342 434 C346 420 343 364 337 318 C333 284 333 282 334 245 C334 204 334 194 333 183 C327 135 288 90 242 76 C236 74 222 72 220 72 C219 72 215 73 211 73 Z",
    "M500 107 C489 109 482 112 462 121 C432 135 432 135 409 142 C371 152 363 162 362 200 C361 208 360 224 359 236 C357 263 359 291 364 326 C369 365 375 377 388 377 C399 377 402 375 457 330 C489 304 523 272 537 254 C571 211 580 178 566 142 C554 112 532 100 500 107 Z",
    "M591 255 C582 257 567 266 550 282 C524 305 492 333 434 380 C384 421 373 435 366 466 C364 475 364 479 364 494 C364 516 366 525 373 537 C389 561 417 562 460 542 C557 496 616 401 616 294 C616 261 610 251 591 255 Z",
    "M80 323 C73 328 74 348 82 376 C109 463 174 528 263 556 C300 568 325 569 330 559 C336 548 324 521 301 495 C269 458 158 362 115 334 C98 322 86 318 80 323 Z",
]

func leafPath(in rect: NSRect, only piece: Int? = nil) -> NSBezierPath {
    // fit the SVG outlines into rect; SVG's Y axis points down, AppKit's points up
    let scale = min(rect.width / leafBox.width, rect.height / leafBox.height)
    let dx = rect.midX - leafBox.midX * scale, dy = rect.midY + leafBox.midY * scale
    let path = NSBezierPath()
    for (index, d) in leafPaths.enumerated() where piece == nil || piece == index {
        var numbers: [CGFloat] = []
        var command: Character = "M"
        func point(_ i: Int) -> NSPoint { NSPoint(x: numbers[i] * scale + dx, y: dy - numbers[i + 1] * scale) }
        func flush() {
            switch command {
            case "M": if numbers.count >= 2 { path.move(to: point(0)) }
            case "L": if numbers.count >= 2 { path.line(to: point(0)) }
            case "C": var i = 0; while i + 5 < numbers.count { path.curve(to: point(i + 4), controlPoint1: point(i), controlPoint2: point(i + 2)); i += 6 }
            default: break
            }
            numbers = []
        }
        for t in d.replacingOccurrences(of: ",", with: " ").split(separator: " ") {
            if let first = t.first, "MCLZ".contains(first) {
                flush(); command = first
                if first == "Z" { path.close() }
                if let n = Double(t.dropFirst()) { numbers.append(CGFloat(n)) }
            } else if let n = Double(t) { numbers.append(CGFloat(n)) }
        }
        flush()
    }
    return path
}

/// Leaf image. Identical ones (size, color) are served from the cache: the agent icon sits in every list row and used to be recreated on every redraw
func leafImage(size: CGFloat, color: NSColor, template: Bool) -> NSImage {
    let key = LeafImageKey(size: size, color: color, template: template)
    if let hit = leafImages[key] { return hit }
    let image = NSImage(size: NSSize(width: size, height: size), flipped: false) { rect in
        color.setFill(); leafPath(in: rect.insetBy(dx: size * 0.04, dy: size * 0.04)).fill(); return true
    }
    image.isTemplate = template
    if leafImages.count > 64 { leafImages.removeAll() }   // there aren't many sizes; the limit guards against growth while resizing the window
    leafImages[key] = image
    return image
}
private struct LeafImageKey: Hashable { let size: CGFloat; let color: NSColor; let template: Bool }
private var leafImages: [LeafImageKey: NSImage] = [:]

/// Dock icon as a set of PNGs for iconutil: an almost-white square filling the whole canvas (no tile or corner radius of its own)
/// and a dark leaf at 600 out of 1024. Colors carry a barely noticeable tint (not 255/255/255 and not flat gray): a pure
/// black-and-white image is taken by actool on this macOS as a monochrome/tinted icon, and it substitutes its own generic
/// gray placeholder instead — verified 2026-10-18.
func makeIcons(dir: URL) {
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    for (name, px) in [("icon_16x16", 16), ("icon_16x16@2x", 32), ("icon_32x32", 32), ("icon_32x32@2x", 64), ("icon_128x128", 128), ("icon_128x128@2x", 256),
                       ("icon_256x256", 256), ("icon_256x256@2x", 512), ("icon_512x512", 512), ("icon_512x512@2x", 1024)] {
        guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                         isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { continue }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        let s = CGFloat(px) / 1024
        NSColor(red: 1, green: 0.996, blue: 1, alpha: 1).setFill()
        NSBezierPath(rect: NSRect(x: 0, y: 0, width: px, height: px)).fill()
        NSColor(red: 0.11, green: 0.108, blue: 0.113, alpha: 1).setFill()
        leafPath(in: NSRect(x: 102 * s, y: 102 * s, width: 820 * s, height: 820 * s)).fill()
        NSGraphicsContext.restoreGraphicsState()
        try? rep.representation(using: .png, properties: [:])?.write(to: dir.appendingPathComponent(name + ".png"))
    }
}


// ---------- leaf made of petals: drawn and animated piece by piece ----------

/// One leaf petal (0…5) as a SwiftUI shape: the same outlines as the icon's
struct LeafPetal: Shape {
    let piece: Int
    /// Petal outlines in a unit square (SwiftUI: Y axis points down) — parsed from the strings once, not on every frame
    private static let unit: [Path] = leafPaths.indices.map { i in
        let box = NSRect(x: 0, y: 0, width: 1, height: 1).insetBy(dx: 0.04, dy: 0.04)
        return Path(leafPath(in: box, only: i).cgPath).applying(CGAffineTransform(scaleX: 1, y: -1).translatedBy(x: 0, y: -1))
    }
    func path(in rect: CGRect) -> Path {
        LeafPetal.unit[piece].applying(CGAffineTransform(scaleX: rect.width, y: rect.height)).offsetBy(dx: rect.minX, dy: rect.minY)
    }
}

/// Sena's animated leaf. assemble — petals fly in from different sides and come together (on opening); wave — a breathing
/// wave runs across the petals (while loading); alive — slow breathing (Sena is working); still — just the leaf, it appears already assembled.
/// replay reassembles the leaf in any mode. With "reduce motion" the leaf is always assembled and motionless
struct LeafMark: View {
    enum Mode { case still, assemble, wave, alive }
    var size: CGFloat
    var color: Color = Style.Colors.palette[0]
    var mode: Mode = .still
    var replay = 0   // when it changes, petals fly apart and reassemble
    @Environment(\.accessibilityReduceMotion) private var reduce
    @State private var assembled: Bool
    @State private var start = Date()

    init(size: CGFloat, color: Color = Style.Colors.palette[0], mode: Mode = .still, replay: Int = 0) {
        self.size = size; self.color = color; self.mode = mode; self.replay = replay
        _assembled = State(initialValue: mode == .still)   // still doesn't assemble on appearing: it's the leaf right away
    }

    /// Each petal's center as a fraction of the square: the petal grows from it and flies away toward it from the middle
    private static let centers: [CGPoint] = leafPaths.indices.map { i in
        let r = leafPath(in: NSRect(x: 0, y: 0, width: 1, height: 1).insetBy(dx: 0.04, dy: 0.04), only: i).bounds
        return CGPoint(x: r.midX, y: 1 - r.midY)
    }
    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: mode == .still || mode == .assemble || reduce)) { ctx in
            let t = ctx.date.timeIntervalSince(start)
            ZStack {
                ForEach(leafPaths.indices, id: \.self) { i in
                    let c = LeafMark.centers[i]
                    let anchor = UnitPoint(x: c.x, y: c.y)
                    let built = assembled || reduce
                    LeafPetal(piece: i).fill(color)
                        .scaleEffect((built ? 1 : Style.Motion.leafStart) * pulseValue(i, t), anchor: anchor)
                        .rotationEffect(.degrees(built ? 0 : (i.isMultiple(of: 2) ? -Style.Motion.leafTilt : Style.Motion.leafTilt)), anchor: anchor)
                        .offset(x: built ? 0 : (c.x - 0.5) * size * Style.Motion.leafFly, y: built ? 0 : (c.y - 0.5) * size * Style.Motion.leafFly)
                        .opacity(built ? 1 : 0)
                        .animation(reduce ? nil : Style.Motion.leafAssemble.delay(Double(i) * Style.Motion.leafStagger), value: assembled)
                }
            }
            .frame(width: size, height: size)
            .rotationEffect(.degrees(mode == .wave && !reduce ? sin(t * Style.Motion.leafSway.speed) * Style.Motion.leafSway.degrees : 0))
        }
        .onChange(of: replay) {
            assembled = false
            DispatchQueue.main.asyncAfter(deadline: .now() + Style.Motion.leafReplay) { assembled = true }
        }
        .onAppear {
            start = Date()
            if mode != .still { DispatchQueue.main.async { assembled = true } }
        }
    }
    private func pulseValue(_ i: Int, _ t: Double) -> CGFloat {
        guard !reduce, assembled else { return 1 }
        let breath: (amplitude: Double, speed: Double, shift: Double)
        switch mode {
        case .wave: breath = Style.Motion.leafWave
        case .alive: breath = Style.Motion.leafBreath
        default: return 1
        }
        return 1 + breath.amplitude * sin(t * breath.speed - Double(i) * breath.shift)
    }
}
