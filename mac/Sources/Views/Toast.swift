// Notification about an action's result: a round badge pops in with a spring, the checkmark is drawn as a line, and on failure the exclamation
// mark shakes. One per app window and mini panel. With "reduce motion" the badge appears already finished.
import SwiftUI

struct ToastBubble: View {
    let text: String
    var failed = false
    var body: some View {
        HStack(spacing: Style.Space.m) {
            ToastBadge(failed: failed)
            Text(text).font(Style.Text.small)
        }
        .padding(.horizontal, Style.Space.xl).padding(.vertical, Style.Space.m)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
        .shadow(radius: Style.Space.s).padding(.top, Style.Space.l)
        .allowsHitTesting(false)   // the notification over the header doesn't intercept taps on search and chips
    }
}

/// Notification badge: success (checkmark is drawn) or failure (mark shakes)
struct ToastBadge: View {
    let failed: Bool
    @Environment(\.accessibilityReduceMotion) private var still
    @State private var popped = false
    @State private var drawn = false
    @State private var shakes = 0

    var body: some View {
        let size = Style.Motion.toastIcon
        let fill = failed ? Style.Colors.bad : Style.Colors.ok
        ZStack {
            Circle().fill(fill).scaleEffect(popped || still ? 1 : Style.Motion.toastPop)
            ToastMark(failed: failed)
                .trim(from: 0, to: drawn || still ? 1 : 0)
                .stroke(Style.Colors.onFill(fill), style: StrokeStyle(lineWidth: Style.Size.line * 2, lineCap: .round, lineJoin: .round))
                .padding(size * Style.Motion.toastInset)
                .keyframeAnimator(initialValue: CGFloat(0), trigger: shakes) { mark, dx in mark.offset(x: dx) } keyframes: { _ in
                    KeyframeTrack {
                        CubicKeyframe(-2.5, duration: 0.05); CubicKeyframe(2.5, duration: 0.07); CubicKeyframe(-1.5, duration: 0.07)
                        CubicKeyframe(1, duration: 0.06); SpringKeyframe(0, duration: 0.2)
                    }
                }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)   // the text itself is read aloud
        .onAppear {
            guard !still else { return }
            withAnimation(Style.Motion.switchSpring) { popped = true }
            withAnimation(Style.Motion.toastDraw.delay(Style.Motion.toastDrawDelay)) { drawn = true }
            if failed { DispatchQueue.main.asyncAfter(deadline: .now() + Style.Motion.toastDrawDelay + 0.2) { shakes += 1 } }
        }
    }
}

/// Badge's line art: the checkmark as two strokes, or the exclamation mark as a bar and a dot
private struct ToastMark: Shape {
    let failed: Bool
    func path(in r: CGRect) -> Path {
        var p = Path()
        if failed {
            p.move(to: CGPoint(x: r.midX, y: r.minY + r.height * 0.05)); p.addLine(to: CGPoint(x: r.midX, y: r.minY + r.height * 0.62))
            p.move(to: CGPoint(x: r.midX, y: r.maxY - r.height * 0.04)); p.addLine(to: CGPoint(x: r.midX, y: r.maxY - r.height * 0.04))
        } else {
            p.move(to: CGPoint(x: r.minX + r.width * 0.04, y: r.midY + r.height * 0.05))
            p.addLine(to: CGPoint(x: r.minX + r.width * 0.36, y: r.maxY - r.height * 0.06))
            p.addLine(to: CGPoint(x: r.maxX - r.width * 0.02, y: r.minY + r.height * 0.12))
        }
        return p
    }
}
