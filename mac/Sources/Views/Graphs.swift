// Spending charts: bars (BarGraph), a running total with a forecast (AreaGraph), and a share ring (Donut).
// One look from Style.Chart: a dashed grid with no axis lines, capsule bars with an accent gradient, the peak in mustard,
// hover shows a dark tooltip and dims the rest, appearing grows from zero. The screen supplies points and labels, these draw them.
import SwiftUI
import Charts

/// A chart point: a moment (hour, day or month), money and number of calls
struct GraphPoint: Identifiable {
    let date: Date
    let usd: Double
    let calls: Int
    var id: Date { date }
}

/// Chart step: by hour (a single day), by day, or by month (a long period — there would be hundreds of daily bars)
enum GraphUnit {
    case hour, day, month
    var component: Calendar.Component { self == .hour ? .hour : self == .day ? .day : .month }
    /// Default axis label: "14" for hours, "12 Oct" for days, "Oct 26" for months
    var axis: Date.FormatStyle {
        switch self {
        case .hour: return .dateTime.hour(.twoDigits(amPM: .omitted))
        case .day: return .dateTime.day().month(.abbreviated)
        case .month: return .dateTime.month(.abbreviated).year(.twoDigits)
        }
    }
    /// Tooltip title: "14:00", "Sat, 12 Oct" or "October 2026"
    func title(_ d: Date) -> String {
        switch self {
        case .hour: return d.formatted(.dateTime.hour(.twoDigits(amPM: .omitted)).minute())
        case .day: return d.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))
        case .month: return d.formatted(.dateTime.month(.wide).year())
        }
    }
}

/// Shared strokes and fills for the charts
enum Graph {
    static var grid: StrokeStyle { StrokeStyle(lineWidth: Style.Size.line, dash: Style.Chart.gridDash) }
    static var dash: StrokeStyle { StrokeStyle(lineWidth: Style.Size.line, dash: Style.Chart.ruleDash) }
    static var stroke: StrokeStyle { StrokeStyle(lineWidth: Style.Chart.line, lineCap: .round, lineJoin: .round) }
    static func bar(_ c: Color) -> LinearGradient { LinearGradient(colors: [c, c.opacity(Style.Chart.barFade)], startPoint: .top, endPoint: .bottom) }
    static func area(_ c: Color) -> LinearGradient { LinearGradient(colors: [c.opacity(Style.Chart.areaTop), c.opacity(Style.Chart.areaBottom)], startPoint: .top, endPoint: .bottom) }
    static let gridColor = Style.Colors.ink.opacity(Style.Chart.gridOpacity)
    static let ruleColor = Style.Colors.ink.opacity(Style.Chart.ruleOpacity)
    static let moneyWidth = Style.Size.moneyWidth   // the same value, under a short name — used most often here
    /// Money on the axis, short: like everywhere else (Fmt.usd — fractions of a cent aren't lost), but from ten dollars up — no cents
    static func usd(_ x: Double) -> String { x == 0 ? "0" : x >= 10 ? "$" + String(format: "%.0f", x) : Fmt.usd(x) }
    /// How many labels to skip on the X axis, so there are no more than Style.Chart.xLabels
    static func step(_ count: Int) -> Int { max(1, Int((Double(count) / Double(Style.Chart.xLabels)).rounded(.up))) }
    /// The series point under the cursor: matches by hour, day or month
    static func pick(_ points: [GraphPoint], at date: Date?, unit: GraphUnit) -> GraphPoint? {
        guard let d = date else { return nil }
        return points.first { Calendar.current.isDate($0.date, equalTo: d, toGranularity: unit.component) }
    }
    /// The most expensive point (the peak); empty points don't count
    static func peak(_ points: [GraphPoint]) -> GraphPoint? { points.filter { $0.usd > 0 }.max { $0.usd < $1.usd } }
    /// Top of the scale: a bit above the largest value, proportional to it (fractions of a cent aren't hidden under a one-cent floor); empty — an arbitrary unit
    static func top(_ biggest: Double, margin: Double) -> Double { biggest > 0 ? biggest * margin : 0.01 }
}

/// Chart axes with a time axis: X labels at a step, a dashed grid with money on the left, no axis lines or ticks
struct GraphAxes: ViewModifier {
    let unit: GraphUnit
    let count: Int
    let labels: Date.FormatStyle
    func body(content: Content) -> some View {
        content
            .chartXAxis {
                AxisMarks(values: .stride(by: unit.component, count: Graph.step(count))) { _ in
                    AxisValueLabel(format: labels, centered: true).font(Style.Text.tiny).foregroundStyle(Style.Colors.tertiaryText)
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading, values: .automatic(desiredCount: Style.Chart.yLabels)) { v in
                    AxisGridLine(stroke: Graph.grid).foregroundStyle(Graph.gridColor)
                    AxisValueLabel { if let d = v.as(Double.self) { Text(Graph.usd(d)).font(Style.Text.tiny).foregroundStyle(Style.Colors.tertiaryText) } }
                }
            }
            .frame(height: Style.Chart.height)
    }
}
extension View { func graphAxes(unit: GraphUnit, count: Int, labels: Date.FormatStyle) -> some View { modifier(GraphAxes(unit: unit, count: count, labels: labels)) } }

/// Tooltip above a point: a dark chip with a title, a number and a note
struct GraphTip: View {
    @Environment(\.colorScheme) private var scheme
    let title: String
    let value: String
    var note = ""
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.xxs) {
            Text(title).font(Style.Text.tiny).opacity(Style.Chart.tipMuted)
            HStack(alignment: .firstTextBaseline, spacing: Style.Space.s) {
                Text(value).font(Style.Text.heading.monospacedDigit())
                if !note.isEmpty { Text(note).font(Style.Text.tiny).opacity(Style.Chart.tipMuted) }
            }
        }
        .foregroundStyle(Style.Colors.pillText(scheme))
        .padding(.horizontal, Style.Space.gapSmall).padding(.vertical, Style.Space.s)
        .background(Style.Colors.pill(scheme), in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
        .shadow(color: .black.opacity(Style.Size.shadowOpacity), radius: Style.Size.shadowRadius, y: Style.Size.shadowY)
    }
}

/// Bars by hour, day or month: capsules with an accent gradient, the peak in mustard, a dashed average line, a tooltip on hover
struct BarGraph: View {
    @Environment(\.accessibilityReduceMotion) private var still
    let points: [GraphPoint]
    let unit: GraphUnit
    var title = ""                        // name for VoiceOver
    var labels: Date.FormatStyle? = nil   // X axis labels, by the chart's step by default
    @State private var picked: Date? = nil
    @State private var grown = false

    var average: Double { points.isEmpty ? 0 : points.reduce(0) { $0 + $1.usd } / Double(points.count) }
    var body: some View {
        let selected = Graph.pick(points, at: picked, unit: unit)
        let peak = Graph.peak(points)
        let top = Graph.top(peak?.usd ?? 0, margin: 1.2)   // headroom at the top for the average label and the tooltip
        Chart {
            ForEach(points) { p in
                BarMark(x: .value(L("когда"), p.date, unit: unit.component), y: .value("$", grown ? p.usd : 0), width: .ratio(Style.Chart.barWidth))
                    .foregroundStyle(Graph.bar(p.date == peak?.date ? Style.Colors.warn : Style.Colors.accent))
                    .cornerRadius(Style.Radius.all)
                    .opacity(selected == nil || selected?.date == p.date ? 1 : Style.Chart.dim)
            }
            if average > 0 {
                RuleMark(y: .value(L("среднее"), average))
                    .lineStyle(Graph.dash)
                    .foregroundStyle(Graph.ruleColor)
                    .annotation(position: .top, alignment: .trailing, spacing: Style.Space.xxs) {
                        Text(String(format: L("в среднем %@"), Fmt.usd(average))).font(Style.Text.tiny).foregroundStyle(Style.Colors.tertiaryText)
                    }
            }
            if let s = selected {
                RuleMark(x: .value(L("когда"), s.date, unit: unit.component))
                    .foregroundStyle(Graph.ruleColor)
                    .annotation(position: .top, spacing: Style.Space.xs, overflowResolution: .init(x: .fit(to: .chart), y: .fit(to: .chart))) {
                        GraphTip(title: unit.title(s.date), value: Fmt.usd(s.usd), note: Fmt.calls(s.calls))
                    }
            }
        }
        .chartYScale(domain: 0...top)
        .chartXSelection(value: $picked)
        .graphAxes(unit: unit, count: points.count, labels: labels ?? unit.axis)
        .accessibilityLabel(title)
        .onAppear { withAnimation(still ? nil : Style.Motion.spring) { grown = true } }
    }
}

/// Running total: a fill under a smooth line, a point with the sum at the end, a dashed forecast, and a limit line
struct AreaGraph: View {
    @Environment(\.accessibilityReduceMotion) private var still
    let points: [GraphPoint]          // already a running total
    let unit: GraphUnit
    var title = ""                    // name for VoiceOver
    var forecast: [GraphPoint] = []   // dashed line from the last point: a forecast toward the end of the period
    var limit: Double = 0             // horizontal limit line (budget), 0 — none
    var labels: Date.FormatStyle? = nil
    @State private var picked: Date? = nil
    @State private var grown = false

    var body: some View {
        let selected = Graph.pick(points, at: picked, unit: unit)
        let top = Graph.top(max(points.last?.usd ?? 0, forecast.last?.usd ?? 0, limit), margin: 1.15)
        Chart {
            ForEach(points) { p in
                AreaMark(x: .value(L("когда"), p.date), y: .value("$", grown ? p.usd : 0))
                    .interpolationMethod(.monotone)
                    .foregroundStyle(Graph.area(Style.Colors.accent))
                LineMark(x: .value(L("когда"), p.date), y: .value("$", grown ? p.usd : 0), series: .value(L("ряд"), L("факт")))
                    .interpolationMethod(.monotone)
                    .lineStyle(Graph.stroke)
                    .foregroundStyle(Style.Colors.accent)
            }
            ForEach(forecast) { p in
                LineMark(x: .value(L("когда"), p.date), y: .value("$", p.usd), series: .value(L("ряд"), L("прогноз")))
                    .lineStyle(Graph.dash)
                    .foregroundStyle(Style.Colors.warn)
            }
            if let last = points.last {
                PointMark(x: .value(L("когда"), last.date), y: .value("$", grown ? last.usd : 0))
                    .symbolSize(Style.Chart.pointArea)
                    .foregroundStyle(Style.Colors.accent)
                    .annotation(position: forecast.isEmpty ? .topTrailing : .top, spacing: Style.Space.xs, overflowResolution: .init(x: .fit(to: .chart), y: .fit(to: .chart))) {
                        if selected == nil { Text(Fmt.usd(last.usd)).font(Style.Text.small.weight(.semibold).monospacedDigit()).foregroundStyle(Style.Colors.accent) }
                    }
            }
            if let end = forecast.last, forecast.count > 1 {
                PointMark(x: .value(L("когда"), end.date), y: .value("$", end.usd))
                    .symbolSize(Style.Chart.pointArea)
                    .foregroundStyle(Style.Colors.warn)
                    .annotation(position: .topTrailing, spacing: Style.Space.xs, overflowResolution: .init(x: .fit(to: .chart), y: .fit(to: .chart))) {
                        Text("≈ " + Fmt.usd(end.usd)).font(Style.Text.small.weight(.semibold).monospacedDigit()).foregroundStyle(Style.Colors.warnText)
                    }
            }
            if limit > 0 {
                RuleMark(y: .value(L("предел"), limit))
                    .lineStyle(Graph.dash)
                    .foregroundStyle(Style.Colors.bad)
                    .annotation(position: .top, alignment: .leading, spacing: Style.Space.xxs) {
                        Text(String(format: L("бюджет дня %@"), Fmt.usd(limit))).font(Style.Text.tiny).foregroundStyle(Style.Colors.badText)
                    }
            }
            if let s = selected {
                RuleMark(x: .value(L("когда"), s.date))
                    .foregroundStyle(Graph.ruleColor)
                    .annotation(position: .top, spacing: Style.Space.xs, overflowResolution: .init(x: .fit(to: .chart), y: .fit(to: .chart))) {
                        GraphTip(title: String(format: L("к %@"), unit.title(s.date)), value: Fmt.usd(s.usd), note: Fmt.calls(s.calls))
                    }
                PointMark(x: .value(L("когда"), s.date), y: .value("$", s.usd))
                    .symbolSize(Style.Chart.pointArea)
                    .foregroundStyle(Style.Colors.accent)
            }
        }
        .chartYScale(domain: 0...top)
        .chartXSelection(value: $picked)
        .graphAxes(unit: unit, count: points.count + max(0, forecast.count - 1), labels: labels ?? unit.axis)
        .accessibilityLabel(title)
        .onAppear { withAnimation(still ? nil : Style.Motion.spring) { grown = true } }
    }
}

/// A share in the ring: key, label, money, calls and its own color
struct DonutSlice: Identifiable {
    let id: String
    let label: String
    let usd: Double
    let calls: Int
    let color: Color
}

/// A ring of shares with a total in the center and a list: hovering over a slice or a row highlights both together.
/// Side by side if the column is wide; in a narrow column the list moves under the ring (otherwise names would shrink to "…")
struct Donut: View {
    let slices: [DonutSlice]
    let total: Double
    var center = L("всего")
    @State private var angle: Double? = nil
    @State private var hovered: String? = nil

    var current: DonutSlice? { hovered.flatMap { h in slices.first { $0.id == h } } ?? angle.flatMap(slice(at:)) }
    func slice(at a: Double) -> DonutSlice? {
        var acc = 0.0
        for s in slices { acc += s.usd; if a <= acc { return s } }
        return slices.last
    }
    func share(_ s: DonutSlice) -> String { total > 0 ? Fmt.percent(s.usd / total) : "—" }

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: Style.Space.gap) { ring; list }
            VStack(alignment: .center, spacing: Style.Space.gap) { ring; list }
        }
    }

    var ring: some View {
        Chart(slices) { s in
            SectorMark(angle: .value("$", s.usd), innerRadius: .ratio(Style.Chart.ring), angularInset: Style.Chart.sectorGap)
                .cornerRadius(Style.Chart.sectorRadius)
                .foregroundStyle(s.color)
                .opacity(current == nil || current?.id == s.id ? 1 : Style.Chart.dim)
        }
        .chartAngleSelection(value: $angle)
        .chartLegend(.hidden)
        .chartBackground { proxy in
            GeometryReader { g in
                let f = proxy.plotFrame.map { g[$0] } ?? .zero
                VStack(spacing: Style.Space.xxs) {
                    Text(Fmt.usd(current?.usd ?? total)).font(Style.Text.title.monospacedDigit()).contentTransition(.numericText())
                    Text(current?.label ?? center).font(Style.Text.tiny).foregroundStyle(Style.Colors.tertiaryText).lineLimit(1)
                }
                .frame(width: f.width * Style.Chart.ring)
                .position(x: f.midX, y: f.midY)
            }
        }
        .frame(width: Style.Chart.donut, height: Style.Chart.donut)
        .motion(Style.Motion.quick, value: current?.id)
        .accessibilityHidden(true)   // the same numbers are read by the list next to it
    }

    var list: some View {
        VStack(spacing: 0) {
            ForEach(slices) { s in
                HStack(spacing: Style.Space.m) {
                    Circle().fill(s.color).frame(width: Style.Size.dot, height: Style.Size.dot)
                    Text(s.label).font(Style.Text.small).lineLimit(1).truncationMode(.middle)
                    Spacer(minLength: Style.Space.m)
                    Text(share(s)).font(Style.Text.caption.monospacedDigit()).foregroundStyle(Style.Colors.secondaryText)
                    Text(Fmt.usd(s.usd)).font(Style.Text.small.monospacedDigit()).frame(width: Graph.moneyWidth, alignment: .trailing)
                }
                .row(selected: current?.id == s.id)
                .onHover { on in if on { hovered = s.id } else if hovered == s.id { hovered = nil } }
                .help("\(s.label): \(Fmt.calls(s.calls))")
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(s.label), \(share(s)), \(Fmt.usd(s.usd)), \(Fmt.calls(s.calls))")
            }
        }
    }
}
