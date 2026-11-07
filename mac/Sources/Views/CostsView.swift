// Costs: how much Sena spent over a period and on what. Summary tiles (with a monthly forecast or the remaining daily budget),
// charts (Graphs.swift): bars by hour (a single day), by day or by month (a long period), and a running total with a forecast
// to the end of the month, share rings by model, purpose and source, the most expensive tasks (a click opens the task), the Claude
// subscription and free limits, a table of the latest log entries (double-click or Enter opens the entry's task). In the section
// header — dates "from … to …" (the shared PeriodPicker, no preset periods) and "Recalculate by rates". Data: desk.costs_view → sena/core/costs.py,
// the period is sent as YYYY-MM-DD..YYYY-MM-DD. The screen listens to the model only for the period and budget: the overview poll every two seconds doesn't touch it.
import SwiftUI

struct CostRow: Identifiable { let id: Int; let time: String; let who: String; let service: String; let model: String; let units: String; let usd: String; let purpose: String; let task: String; let tid: String }

private let USAGE_RU = ["maps": L("запросов к картам сегодня"), "image": L("картинок сегодня"), "video": L("видео сегодня")]

/// A daily budget row: a label, "spent out of the limit" with a share and a bar; no limit — just the spent amount. Shared by settings and costs
struct BudgetLine: View {
    let title: String
    let spent: Double
    let limit: Double
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.xs) {
            HStack {
                Text(title).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
                Spacer()
                Text(text).font(Style.Text.small.monospacedDigit()).foregroundStyle(limit > 0 && spent >= limit ? Style.Colors.badText : Color.primary)
                    .contentTransition(.numericText())
            }
            if limit > 0 { Bar(value: spent / limit, limit: true) }
        }
        .accessibilityElement(children: .combine)
    }
    var text: String {
        limit > 0 ? String(format: L("%@ из %@ · %@"), Fmt.usd(spent), Fmt.usd(limit), Fmt.percent(min(9.99, spent / limit)))
                  : String(format: L("%@ · предела нет"), Fmt.usd(spent))
    }
}

/// What the costs screen needs to know about the period and the budget. Assembled from the model on every change to it, but the
/// screen only redraws when this value differs (previously the overview poll every two seconds rebuilt the charts and the 300-row table)
struct CostsPeriod: Equatable {
    let key: String        // the period as the server understands it
    let since: Date
    let until: Date
    let days: Int
    let singleDay: Bool
    let today: Bool
    let limit: Double      // daily budget, 0 — no limit
    let revision: Int      // increments after "Recalculate by rates": figures get reread right away, not through polling
}

struct CostsView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.narrowWindow) private var narrow
    var period: CostsPeriod {
        CostsPeriod(key: model.period, since: model.since, until: model.until, days: model.days, singleDay: model.singleDay, today: model.today,
                    limit: model.overview["budget"]["limit"].dbl, revision: model.costsRevision)
    }

    var body: some View {
        let p = period
        CostsContent(model: model, p: p).equatable()
            .id("\(p.key)#\(p.revision)")   // a different period — a fresh screen: loads from scratch, old figures for other dates don't linger
            .transition(Style.Motion.appear)
            .motion(value: p.key)
            .actions {
                PeriodPicker()
                if narrow { IconButton(icon: "arrow.triangle.2.circlepath") { CostsContent.recost(model) }.help(L("Пересчитать по тарифам: заново посчитать все записи по текущей таблице цен")).accessibilityLabel(L("Пересчитать по тарифам")) }
                else { Button(L("Пересчитать по тарифам")) { CostsContent.recost(model) }.help(L("Заново посчитать все записи по текущей таблице цен (тариф поменялся или был неточным)")) }
            }
    }
}

struct CostsContent: View, Equatable {
    let model: AppModel      // not @EnvironmentObject: subscribing to the model would rebuild the screen every two seconds
    let p: CostsPeriod
    static func == (a: CostsContent, b: CostsContent) -> Bool { a.p == b.p }

    static let rowsLimit = 300
    static let monthlyAfter = 92   // a period longer than this (in days) is drawn as bars by month
    // layout of the records table: row and header height, height cap
    static let tableRow: CGFloat = 24, tableHead: CGFloat = 28, tableMax: CGFloat = 520
    @State private var picked = Set<Int>()   // the selected row of the records table

    var body: some View {
        Polling(seconds: 30, load: { try await model.backend.get("costs", ["period": p.key, "limit": "\(CostsContent.rowsLimit)"]) }) { r in
            let s = r["summary"]
            let rows = r["rows"].array
            let total = s["total"].dbl
            let paid = s["by_provider"].array.reduce(0) { $0 + ($1[1]["usd"].dbl > 0 ? $1[1]["calls"].int : 0) }   // paid: free ones (maps) have a zero price
            ScrollView {
                VStack(alignment: .leading, spacing: Style.Space.xl) {
                    summary(s, paid: paid, pricesAsOf: r["prices_as_of"].str)
                    HomeBlock(model: model)
                    if rows.isEmpty {
                        Empty(title: L("Платных вызовов не было"), icon: "creditcard")
                            .frame(maxWidth: .infinity).padding(.top, Style.Space.huge)
                    } else {
                        if paid > 0 {
                            charts(s, rows: rows)
                            AdaptiveRow {
                                ShareBlock(title: L("По моделям"), rows: s["by_model"].array, total: total)
                                ShareBlock(title: L("По назначению"), rows: s["by_purpose"].array, total: total)
                                ShareBlock(title: L("По источникам"), rows: s["by_source"].array, total: total, agents: true)
                            }
                            if !s["top_tasks"].array.isEmpty { topTasks(s["top_tasks"].array, total: total) }
                        }
                        limits(s, usage: r["usage"])
                        records(rows)
                    }
                }
                .motion(value: total)
            }
        }
    }

    // ---- summary for the period: tiles, daily budget, notes ----
    func summary(_ s: JSON, paid: Int, pricesAsOf: String) -> some View {
        let total = s["total"].dbl
        let limit = p.limit
        let unpriced = s["unpriced"].dict
        let unpricedCount = unpriced.values.reduce(0) { $0 + $1.int }
        let hasNotes = s["estimated"].bool || unpricedCount > 0 || s["claude"]["calls"].int > 0
        return Block(s["title"].string ?? L("Итог"), lead: true, content: {
            VStack(alignment: .leading, spacing: Style.Space.l) {
                HStack(spacing: Style.Space.m) {
                    Stat(value: Fmt.usd(total), label: L("потрачено"), bad: p.today && limit > 0 && total >= limit)
                    Stat(value: "\(paid)", label: L("платных вызовов"))
                    Stat(value: paid > 0 ? Fmt.usd(total / Double(paid)) : "—", label: L("в среднем за вызов"))
                    extraTile(total: total, limit: limit)
                }
                if p.today && limit > 0 { BudgetLine(title: L("Расход сегодня"), spent: total, limit: limit) }
                if hasNotes {
                    ViewThatFits(in: .horizontal) {   // three tags in a row, and one under another in a narrow window
                        HStack(spacing: Style.Space.m) { notes(s, unpriced: unpriced, unpricedCount: unpricedCount) }
                        VStack(alignment: .leading, spacing: Style.Space.s) { notes(s, unpriced: unpriced, unpricedCount: unpricedCount) }
                    }
                }
            }
        }, trailing: {
            Text(String(format: L("тарифы на %@"), pricesAsOf)).font(Style.Text.caption).foregroundStyle(Style.Colors.tertiaryText)
        })
    }

    @ViewBuilder func notes(_ s: JSON, unpriced: [String: JSON], unpricedCount: Int) -> some View {
        if s["estimated"].bool { Tag(text: L("часть — оценка по длительности звука"), color: Style.Colors.warn) }
        if unpricedCount > 0 {
            Tag(text: String(format: L("без цены: %@, в итог не вошли"), Fmt.calls(unpricedCount)), color: Style.Colors.bad)
                .help(String(format: L("Нет тарифа:\n%@"), unpriced.map { "\($0.key) — \($0.value.int)" }.sorted().joined(separator: "\n")))
        }
        if s["claude"]["calls"].int > 0 {
            Tag(text: String(format: L("Claude по подписке: %d · деньги не списываются"), s["claude"]["calls"].int), color: Style.Colors.info)
        }
    }

    /// Fourth tile: over several days — the daily average, for today — the remaining daily budget (if one is set)
    @ViewBuilder func extraTile(total: Double, limit: Double) -> some View {
        if !p.singleDay { Stat(value: Fmt.usd(total / Double(p.days)), label: L("в среднем за день")) }
        else if p.today && limit > 0 { Stat(value: Fmt.usd(max(0, limit - total)), label: L("осталось из бюджета дня"), bad: total >= limit) }
    }

    // ---- charts: bars (hours, days or months) and a running total with a forecast to the end of the month ----
    func charts(_ s: JSON, rows: [JSON]) -> some View {
        let hourly = p.singleDay   // a single day — by hour
        let unit: GraphUnit = hourly ? .hour : p.days > CostsContent.monthlyAfter ? .month : .day
        let points = hourly ? byHour(rows) : byPeriod(s["by_day"].array, unit: unit)
        let running = cumulative(points)
        let peak = Graph.peak(points)
        let perDay = (running.last?.usd ?? 0) / Double(p.days)
        let forecast = unit == .day ? monthForecast(running, perDay: perDay) : []
        let labels: Date.FormatStyle? = unit == .day && p.days <= 7 ? .dateTime.weekday(.abbreviated) : nil   // up to a week — by weekday
        let barTitle = hourly ? L("По часам") : unit == .month ? L("По месяцам") : L("По дням")
        return AdaptiveRow {
            Block(barTitle, content: {
                BarGraph(points: points, unit: unit, title: barTitle, labels: labels)
            }, trailing: {
                if let pk = peak {
                    let peakText = String(format: L("пик %@ — %@, %@"), unit.title(pk.date), Fmt.usd(pk.usd), Fmt.calls(pk.calls))
                    note(hourly && rows.count >= CostsContent.rowsLimit ? String(format: L("%@ · по последним %d записям"), peakText, CostsContent.rowsLimit) : peakText)
                }
            })
            Block(L("Нарастающим итогом"), content: {
                AreaGraph(points: running, unit: unit, title: L("Нарастающим итогом"), forecast: forecast, limit: hourly ? p.limit : 0, labels: labels)
            }, trailing: {
                if !hourly {
                    let avg = Fmt.usd(perDay)
                    note(forecast.last.map { String(format: L("в среднем %@ в день · к концу месяца около %@"), avg, Fmt.usd($0.usd)) } ?? String(format: L("в среднем %@ в день"), avg))
                }
            })
        }
    }
    func note(_ text: String) -> some View { Text(text).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText) }

    static func recost(_ model: AppModel) {
        guard confirm(L("Пересчитать расходы?"), L("Все записи журнала будут пересчитаны по текущим тарифам. Прежние суммы вернуть не получится."), button: L("Пересчитать"), destructive: true) else { return }
        model.run(L("Пересчёт"), key: "recost", { try await model.backend.act("recost") }, then: { model.costsRevision += 1 })
    }

    /// A point for every day (or month) of the chosen period; no spending — zero
    func byPeriod(_ days: [JSON], unit: GraphUnit) -> [GraphPoint] {
        let cal = Calendar.current
        func bucket(_ d: Date) -> Date { unit == .month ? (cal.dateInterval(of: .month, for: d)?.start ?? d) : cal.startOfDay(for: d) }
        var spent: [Date: (usd: Double, calls: Int)] = [:]
        for d in days {
            guard let date = Fmt.dayKey.date(from: d[0].str) else { continue }
            let b = bucket(date), old = spent[b] ?? (usd: 0, calls: 0)
            spent[b] = (old.usd + d[1]["usd"].dbl, old.calls + d[1]["calls"].int)
        }
        var out: [GraphPoint] = [], d = bucket(p.since)
        let last = bucket(p.until)
        while d <= last {
            out.append(GraphPoint(date: d, usd: spent[d]?.usd ?? 0, calls: spent[d]?.calls ?? 0))
            guard let n = cal.date(byAdding: unit.component, value: 1, to: d) else { break }
            d = n
        }
        return out
    }

    /// Paid entries for a single day by hour: from the first hour with spending to the current one (today) or to the last one with spending
    func byHour(_ rows: [JSON]) -> [GraphPoint] {
        let cal = Calendar.current
        var acc: [Date: (usd: Double, calls: Int)] = [:]
        for r in rows {
            guard r["usd"].exists, r["provider"].str != "claude-code", let d = Fmt.date(r["t"]), let h = cal.dateInterval(of: .hour, for: d)?.start else { continue }
            acc[h] = ((acc[h]?.usd ?? 0) + r["usd"].dbl, (acc[h]?.calls ?? 0) + 1)
        }
        guard let first = acc.keys.min(), let last = acc.keys.max() else { return [] }
        let end = p.today ? (cal.dateInterval(of: .hour, for: Date())?.start ?? last) : last
        var out: [GraphPoint] = [], h = first
        while h <= end {
            out.append(GraphPoint(date: h, usd: acc[h]?.usd ?? 0, calls: acc[h]?.calls ?? 0))
            guard let n = cal.date(byAdding: .hour, value: 1, to: h) else { break }
            h = n
        }
        return out
    }
    func cumulative(_ points: [GraphPoint]) -> [GraphPoint] {
        var usd = 0.0, calls = 0
        return points.map { pt -> GraphPoint in usd += pt.usd; calls += pt.calls; return GraphPoint(date: pt.date, usd: usd, calls: calls) }
    }
    /// A dashed line from today's point to the last day of the month, by the average over the days so far: when the current month is chosen from the 1st to today
    func monthForecast(_ running: [GraphPoint], perDay: Double) -> [GraphPoint] {
        let cal = Calendar.current
        guard let last = running.last, perDay > 0, cal.isDateInToday(last.date), cal.component(.day, from: p.since) == 1,
              cal.isDate(p.since, equalTo: last.date, toGranularity: .month),
              let end = cal.dateInterval(of: .month, for: last.date)?.end, let lastDay = cal.date(byAdding: .day, value: -1, to: end),
              let left = cal.dateComponents([.day], from: last.date, to: lastDay).day, left > 0 else { return [] }
        return [last, GraphPoint(date: lastDay, usd: last.usd + perDay * Double(left), calls: last.calls)]
    }

    // ---- most expensive tasks: a click opens the task ----
    func topTasks(_ tasks: [JSON], total: Double) -> some View {
        Block(L("Самые дорогие задачи")) {
            VStack(spacing: Style.Space.s) {
                ForEach(tasks, id: \.[0].str) { t in
                    let usd = t[1]["usd"].dbl, calls = t[1]["calls"].int
                    let title = t[1]["task"].str.isEmpty ? t[0].str : t[1]["task"].str
                    let open = { model.openTask(t[0].str) }
                    RecordCard(icon: "dollarsign", tint: Style.Colors.tint(title), title: title, lines: 1, action: open, meta: {
                        Bar(value: share(usd, of: total))
                        Text(Fmt.calls(calls)).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
                    }, trailing: { _ in
                        Text(Fmt.usd(usd)).font(Style.Text.body.monospacedDigit()).frame(width: Graph.moneyWidth, alignment: .trailing).rolling(usd)
                        Glyph("chevron.right").font(Style.Icon.tiny).foregroundStyle(Style.Colors.tertiaryText)
                    })
                    .help(L("Открыть задачу"))
                }
            }
        }
    }

    // ---- subscription and free limits: Claude Code, TomTom for the month, daily counters ----
    @ViewBuilder func limits(_ s: JSON, usage: JSON) -> some View {
        let claude = s["claude"]
        let tomtom = s["tomtom_month"].dict.sorted { $0.key < $1.key }
        let counters = usage.dict.sorted { $0.key < $1.key }
        if claude["calls"].int > 0 || !tomtom.isEmpty || !counters.isEmpty {
            Block(L("Подписка и бесплатные лимиты"), lead: true) {
                HStack(alignment: .top, spacing: Style.Space.m) {
                    if claude["calls"].int > 0 {
                        Stat(value: "\(claude["calls"].int)", label: String(format: L("вызовов Claude по подписке, %d мин"), Int(claude["seconds"].dbl / 60)))
                    }
                    ForEach(tomtom, id: \.key) { item in
                        let used = item.value["used"].int, free = item.value["free"].int
                        VStack(spacing: Style.Space.xs) {
                            Stat(value: free > 0 ? String(format: L("%d из %d"), used, free) : "\(used)", label: String(format: L("TomTom %@: запросов за месяц"), item.key), bad: free > 0 && used > free)
                            if free > 0 { Bar(value: Double(used) / Double(free), limit: true) }
                        }
                    }
                    ForEach(counters, id: \.key) { item in
                        Stat(value: "\(item.value.int)", label: USAGE_RU[item.key] ?? String(format: L("%@ сегодня"), item.key))
                    }
                }
            }
        }
    }

    // ---- log: latest entries, newest on top ----
    func records(_ rows: [JSON]) -> some View {
        let items = rows.enumerated().map { i, x in
            CostRow(id: i, time: Fmt.when(x["t"]), who: agentName(x["who"].str), service: [x["provider"].str, x["service"].str].filter { !$0.isEmpty }.joined(separator: " · "),
                    model: x["model"].str, units: x["units"].dict.map { "\($0.key) \(num($0.value))" }.sorted().joined(separator: ", "),
                    usd: x["unknown"].bool || !x["usd"].exists ? L("без цены") : money(x["usd"].dbl) + (x["estimate"].bool ? "~" : ""),
                    purpose: x["purpose"].string ?? L("задача"), task: x["task"].str, tid: x["tid"].str)
        }
        let count = "\(items.count) \(Fmt.plural(items.count, "запись", "записи", "записей"))"
        return Block(L("Записи расходов"), content: {
            Table(items, selection: $picked) {
                TableColumn(L("Время"), value: \.time).width(110)
                TableColumn(L("Кто"), value: \.who).width(90)
                TableColumn(L("Сервис"), value: \.service).width(140)
                TableColumn(L("Модель"), value: \.model).width(160)
                TableColumn(L("Единицы"), value: \.units)
                TableColumn("$") { Text($0.usd).monospacedDigit().frame(maxWidth: .infinity, alignment: .trailing) }.width(Graph.moneyWidth + Style.Space.xl)
                TableColumn(L("Назначение"), value: \.purpose).width(130)
                TableColumn(L("Задача"), value: \.task)
            }
            .contextMenu(forSelectionType: CostRow.ID.self) { ids in
                if let r = items.first(where: { ids.contains($0.id) }), !r.tid.isEmpty { Button(L("Открыть задачу")) { model.openTask(r.tid) } }
            } primaryAction: { ids in   // double click or Enter
                if let r = items.first(where: { ids.contains($0.id) }), !r.tid.isEmpty { model.openTask(r.tid) }
            }
            .frame(height: min(CostsContent.tableMax, CGFloat(items.count) * CostsContent.tableRow + CostsContent.tableHead))
        }, trailing: {
            note(items.count >= CostsContent.rowsLimit ? String(format: L("%@ · новые сверху · показаны последние"), count) : String(format: L("%@ · новые сверху"), count))
        })
    }

    /// Share of the total for the bar (a share, not a limit: a single item's full share stays the accent)
    func share(_ usd: Double, of total: Double) -> Double { total > 0 ? usd / total : 0 }
    func num(_ j: JSON) -> String { if let d = j.double { return d == d.rounded() ? "\(Int(d))" : String(format: "%.2f", d) }; return j.str }
    /// Money in a log row: embeddings cost fractions of a cent, we show them more precisely than Fmt.usd
    func money(_ x: Double) -> String { x > 0 && x < 0.0001 ? String(format: "$%.6f", x) : Fmt.usd(x) }
}

/// The home and the cloud: how many calls stayed on this Mac today and how many went out, where each switchable job works
/// (the switch moves it: on — at home, on the Mac), and the fixed jobs for the full picture. A place is a word on a tag, never just
/// a color. Data: desk.home_view → sena/core/roles.py; a move — the role_move action, then the block reloads
struct HomeBlock: View {
    let model: AppModel
    @State private var revision = 0
    var body: some View {
        Polling(seconds: 30, load: { try await model.backend.get("home") }) { h in content(h) }
            .id(revision)
    }

    /// The home engines (local.installed on the server), for the line that says what is missing on this Mac
    static let ENGINE_RU = ["embed": L("отпечатки"), "chat": L("суждения"), "eyes": L("зрение"), "hearing": L("слух"), "reading": L("чтение"), "voice": L("голос")]

    func content(_ h: JSON) -> some View {
        let today = h["today"]
        let roles = h["roles"].array
        let need = h["learning"]["min_examples"].int
        let st = h["status"], missing = st["installed"].dict.filter { HomeBlock.ENGINE_RU[$0.key] != nil && !$0.value.bool }.keys.sorted()
        return Block(L("Дом и облако"), content: {
            HStack(spacing: Style.Space.m) {
                Stat(value: "\(today["home"].int)", label: L("вызовов дома сегодня"))
                Stat(value: "\(today["out"].int)", label: L("ушло наружу"))
                Stat(value: Fmt.usd(today["usd"].dbl), label: L("за облако"))
            }
            if !missing.isEmpty {   // what isn't on this Mac yet: those jobs can't stay at home until it is
                Text(String(format: L("Дома не установлено: %@ — python -m sena.home setup"), missing.map { HomeBlock.ENGINE_RU[$0] ?? $0 }.joined(separator: ", ")))
                    .font(Style.Text.caption).foregroundStyle(Style.Colors.warnText).textSelection(.enabled)
            } else if st.exists {
                Text(st["server"].bool ? String(format: L("Домашний сервер работает · моделей проснулось: %d"), st["models"].dict.values.filter { $0.str == "loaded" }.count)
                                       : L("Домашний сервер спит: проснётся с первым делом"))
                    .font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
            }
            VStack(spacing: Style.Space.xxs) {
                ForEach(roles.filter { $0["switchable"].bool }, id: \.["role"].str) { r in row(r, need: need) }
            }
            Text(L("Остальное — постоянно на своём месте")).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
            VStack(spacing: Style.Space.xxs) {
                ForEach(roles.filter { !$0["switchable"].bool }, id: \.["title"].str) { r in row(r, need: need) }
            }
        }, trailing: {
            Text(L("включено — дома, на Mac")).font(Style.Text.caption).foregroundStyle(Style.Colors.tertiaryText)
        })
    }

    func row(_ r: JSON, need: Int) -> some View {
        let home = r["place"].str == "home"
        let note = r["learning"].bool && !home ? String(format: L("учится: примеров %d из %d, потом проверка и переезд домой"), r["examples"].int, need) : r["why"].str
        return HStack(spacing: Style.Space.m) {
            VStack(alignment: .leading, spacing: Style.Space.xxs) {
                Text(r["title"].str).font(Style.Text.body).lineLimit(1)
                Text(note).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(2)
            }
            Spacer()
            Tag(text: home ? L("дома") : L("облако"), color: home ? Style.Colors.palette[4] : Style.Colors.info)
            if r["switchable"].bool {
                Toggle(L("Дома"), isOn: Binding(get: { home }, set: { on in move(r["role"].str, home: on) })).labelsHidden().toggleStyle(.senaBare)
            }
        }
        .row()
        .help(r["model"].str)
    }

    func move(_ role: String, home: Bool) {
        model.run(L("Перенос дела"), key: "role_move", { try await model.backend.act("role_move", ["role": role, "place": home ? "home" : "cloud"]) },
                  then: { revision += 1 })
    }
}

/// A "who spent how much" layout: a share ring with the total in the center and a list of the top five, the rest as one "others" slice
struct ShareBlock: View {
    let title: String
    let rows: [JSON]
    let total: Double
    var agents = false   // sources: agent names and colors
    static let shown = 5
    var body: some View {
        Block(title) {
            if rows.isEmpty { Text("—").font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText) }
            else { Donut(slices: slices, total: total) }
        }
    }
    /// No repeated colors: an agent takes its own, the rest take the first free one from the palette in order
    var slices: [DonutSlice] {
        var used = Set<Int>(), out: [DonutSlice] = []
        for x in rows.prefix(ShareBlock.shown) {
            let key = x[0].str
            let wanted = agents && key.hasPrefix("ag_") ? Style.Colors.agentIndex(key) : nil
            let i = wanted.flatMap { used.contains($0) ? nil : $0 } ?? (0..<Style.Colors.palette.count).first { !used.contains($0) } ?? out.count % Style.Colors.palette.count
            used.insert(i)
            out.append(DonutSlice(id: key, label: label(key), usd: x[1]["usd"].dbl, calls: x[1]["calls"].int, color: Style.Colors.palette[i]))
        }
        let rest = rows.dropFirst(ShareBlock.shown)
        if !rest.isEmpty {
            out.append(DonutSlice(id: "…", label: String(format: L("другие · %d"), rest.count), usd: rest.reduce(0) { $0 + $1[1]["usd"].dbl }, calls: rest.reduce(0) { $0 + $1[1]["calls"].int },
                                  color: Style.Colors.ink.opacity(Style.Chart.otherOpacity)))
        }
        return out
    }
    func label(_ key: String) -> String { key.isEmpty ? "—" : agents ? agentName(key) : key }
}
