// Journal: events with filters in one block and a list (details — a sheet on double-click or Enter), a live process log.
// Everything in blocks from the Components vocabulary (Block, Tag, KV, Empty), no dividers; styling from Style, motion from Style.Motion.
import SwiftUI

/// A text string no longer than n characters, then "…": a giant string (a dump, long JSON) in a single Text would hang the window
private func shortened(_ s: String, _ n: Int) -> String { s.count > n ? String(s.prefix(n)) + "…" : s }

/// Loading failed: a reason and "Retry". Such errors used to be swallowed, and the screen would spin forever
struct RetryNote: View {
    private static let height: CGFloat = 200   // how much room for the message: the button sits right below it, not at the bottom of the screen
    let text: String
    let retry: () -> Void
    var body: some View {
        VStack(spacing: Style.Space.l) {
            Empty(title: L("Не загрузилось"), text: text, icon: "exclamationmark.triangle").frame(maxHeight: RetryNote.height)
            Button(L("Повторить"), action: retry)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct JournalView: View {
    @EnvironmentObject var model: AppModel   // the tab is picked in the "Journal" section's header (Costs | Events | Log); costs are drawn by CostsView
    var body: some View {
        Group { if model.tab(.analytics) == 1 { EventsView() } else { LogView() } }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }
}

// ---- events ----

struct EventRow: Identifiable {
    let id: String
    let t: Double       // the time as is: the label (with or without a date) depends on the period and is built when shown
    let kind: String
    let who: String
    let text: String
    let tid: String
    let json: JSON
}

/// Events over a period: one filter block (period, who, how many, search, kinds with counts), a list below it.
/// Dates "from … to …" are in the section header (the shared PeriodPicker). Event details — a sheet on double-click or Enter; arrow keys move through the list.
/// Loading: any filter change cancels the previous request, a reply to a stale request is discarded; kind counts are requested on a
/// filter change and when new events arrive, not every 5 s
struct EventsView: View {
    @EnvironmentObject var model: AppModel
    @State private var kind = ""
    @State private var who = ""
    @State private var tid = ""                    // only events for one task (from the row's menu)
    @State private var query = ""                  // what's typed in the search field
    @State private var appliedQuery = ""           // what's sent to the server: search fires on Enter, not on every keystroke or a timer
    @State private var limit = 100                 // default number of rows: a long list used to open noticeably slower
    @State private var live = true                 // pull in new events every 5 s
    @State private var loaded = false
    @State private var failure: String? = nil      // the last load failed
    @State private var lastRaw = JSON(nil)
    @State private var counts: [String: Int] = [:]
    @State private var rows: [EventRow] = []
    @State private var selected: EventRow.ID? = nil
    @State private var detail: EventRow? = nil
    @State private var current: Task<Void, Never>? = nil   // load triggered by a filter change: a new one cancels the previous
    @State private var serial = 0                          // the latest request's number: a reply to an older one changes nothing
    @State private var needCounts = true                   // kind counts need to be requested again (the period or filter changed)

    var total: Int { kind.isEmpty ? counts.values.reduce(0, +) : (counts[kind] ?? 0) }
    /// Kinds that have events for the period: errors first, then by count
    var kinds: [String] {
        var ks = counts.filter { $0.value > 0 }.map { $0.key }
        if !kind.isEmpty && !ks.contains(kind) { ks.append(kind) }
        ks.sort { a, b in let ca = counts[a] ?? 0, cb = counts[b] ?? 0; return ca != cb ? ca > cb : a < b }
        if let i = ks.firstIndex(of: "error") { ks.remove(at: i); ks.insert("error", at: 0) }
        return ks
    }
    var shownText: String {
        guard loaded else { return L("загружаю…") }
        return rows.count < total ? String(format: L("показано %d из %d"), rows.count, total) : String(format: L("показано %d"), rows.count)
    }
    /// Everything the query depends on: if it changed, load again
    var filterKey: String { [model.period, kind, who, tid, appliedQuery, "\(limit)"].joined(separator: "|") }

    var body: some View {
        VStack(spacing: Style.Space.xl) {
            filters
            table.frame(maxHeight: .infinity)
        }
        .sheet(item: $detail) { e in EventDetail(event: e) }
        .onChange(of: filterKey) { reload() }
        .onChange(of: query) { if query.isEmpty && !appliedQuery.isEmpty { appliedQuery = "" } }
        .task { while !Task.isCancelled { if live || !loaded { await load() }; try? await Task.sleep(nanoseconds: 5_000_000_000) } }
        .actions { PeriodPicker() }
    }

    var whoSelect: some View {
        Select(selection: $who, options: [("", L("Все агенты"))] + AGENT_RU.keys.sorted().map { ($0, agentName($0)) })
            .accessibilityLabel(L("Агент")).accessibilityValue(who.isEmpty ? L("Все агенты") : agentName(who))
    }
    var limitSelect: some View {
        Choice(value: $limit, options: [100, 300, 1000], label: { String(format: L("%d строк"), $0) }).accessibilityLabel(L("Сколько строк")).accessibilityValue("\(limit)")
    }
    var searchField: some View {
        TextField(L("Поиск по событиям"), text: $query).onSubmit { appliedQuery = query }   // word search is here, the header only has dates
    }
    var refreshButton: some View { Button(L("Обновить"), action: reload) }
    @ViewBuilder var taskChip: some View {
        if !tid.isEmpty {
            Button { tid = "" } label: { Label(String(format: L("задача %@"), tid), systemImage: "xmark.circle.fill") }
                .help(L("Только события этой задачи. Нажми, чтобы снять"))
                .transition(Style.Motion.appear)
        }
    }

    var filters: some View {
        Block(L("Фильтры"), lead: true, content: {
            VStack(alignment: .leading, spacing: Style.Space.m) {
                ViewThatFits(in: .horizontal) {   // in a narrow window the filters wrap to a second row instead of overflowing the block
                    HStack(spacing: Style.Space.m) {
                        whoSelect; limitSelect
                        searchField.frame(width: Style.Size.field)
                        refreshButton; taskChip
                        Spacer(minLength: 0)
                    }
                    VStack(alignment: .leading, spacing: Style.Space.m) {
                        HStack(spacing: Style.Space.m) { whoSelect; limitSelect; refreshButton; Spacer(minLength: 0) }
                        HStack(spacing: Style.Space.m) { searchField.frame(maxWidth: Style.Size.search); taskChip; Spacer(minLength: 0) }
                    }
                }
                ScrollView(.horizontal) {
                    HStack(spacing: Style.Space.s) {
                        KindChip(title: L("все"), count: counts.values.reduce(0, +), color: Style.Colors.accent, on: kind.isEmpty) { kind = "" }
                        ForEach(kinds, id: \.self) { k in
                            KindChip(title: KIND_RU[k] ?? k, count: counts[k] ?? 0, color: kindColor(k), on: kind == k) { kind = kind == k ? "" : k }
                                .transition(Style.Motion.appear)
                        }
                    }
                    .padding(.vertical, Style.Space.xxs)
                    .motion(value: kinds)
                }
            }
            .motion(value: tid.isEmpty)
        }, trailing: {
            Text(shownText).font(Style.Text.caption.monospacedDigit()).foregroundStyle(Style.Colors.secondaryText).rolling(shownText)
        })
    }

    var table: some View {
        Block(L("Журнал событий"), content: {
            EventList(rows: rows, selected: $selected, open: { detail = $0 }, openTask: { model.openTask($0) }, onlyTask: { tid = $0 }, onlyKind: { kind = $0 })
                .overlay {
                    if !loaded { if let e = failure { RetryNote(text: e, retry: reload) } else { Loading() } }
                    else if rows.isEmpty { Empty(title: L("Событий нет"), icon: "doc.text.magnifyingglass") }
                }
                .frame(maxHeight: .infinity)
        }, trailing: {
            if loaded, let e = failure { Tag(text: L("не обновилось"), color: Style.Colors.bad).help(e) }
            Toggle(L("Автообновление"), isOn: $live).help(L("Подтягивать новые события каждые 5 секунд"))
        })
    }

    /// A new query after a filter change or a button press: the previous request is canceled, kind counts are recomputed
    func reload() {
        needCounts = true
        current?.cancel()
        current = Task { await load() }
    }

    /// A list row: for a model event — which one, for a tool — which one, for a verdict — which one; private content has already been replaced by its size by the server
    static func rowText(_ x: JSON) -> String {
        let text = (x["text"].string ?? "").replacingOccurrences(of: "\n", with: " ")
        switch x["kind"].str {
        case "model": return [x["model"].str, text].filter { !$0.isEmpty }.joined(separator: " · ")
        case "tool": return x["name"].str.isEmpty ? text : "\(x["name"].str) → \(text)"
        case "verdict": return [VERDICT_RU[x["verdict"].str] ?? x["verdict"].str, text].filter { !$0.isEmpty }.joined(separator: " — ")
        default: return text.isEmpty ? x["name"].str : text
        }
    }
    /// Newest on top. A row's id is its time, kind and turn; duplicates get a suffix: the id doesn't shift when new events appear on top
    static func makeRows(_ events: JSON) -> [EventRow] {
        var seen: [String: Int] = [:]
        return events.array.reversed().map { x in
            let base = "\(x["t"].dbl)|\(x["kind"].str)|\(x["turn"].int)"
            let n = seen[base, default: 0]
            seen[base] = n + 1
            return EventRow(id: n == 0 ? base : "\(base)#\(n)", t: x["t"].dbl, kind: x["kind"].str, who: agentName(x["who"].str), text: rowText(x), tid: x["tid"].str, json: x)
        }
    }

    func load() async {
        serial += 1
        let mine = serial, period = model.period, counting = needCounts || !loaded
        let params = ["period": period, "kind": kind, "who": who, "tid": tid, "q": appliedQuery, "limit": "\(limit)"]
        let countParams = ["period": period, "who": who, "tid": tid, "q": appliedQuery]   // kind counts respect the same agent, task and words as the list, but not the kind itself
        do {
            async let e = model.backend.get("events", params)
            async let k = counting ? model.backend.get("kinds", countParams) : JSON(nil)
            let events = try await e
            var kindsReply = try await k
            guard mine == serial, !Task.isCancelled else { return }
            let changed = !(loaded && events.isSame(lastRaw))
            if !changed && !counting { failure = nil; return }   // no new events — leave both the list and the counts alone
            if !kindsReply.exists {   // new events arrived: the per-kind numbers are stale
                kindsReply = try await model.backend.get("kinds", countParams)
                guard mine == serial, !Task.isCancelled else { return }
            }
            let newCounts = kindsReply["counts"].dict.mapValues { $0.int }
            if newCounts != counts { counts = newCounts }
            if changed { lastRaw = events; rows = EventsView.makeRows(events) }
            needCounts = false; loaded = true; failure = nil
        } catch {
            guard mine == serial, !Task.isCancelled else { return }
            failure = error.localizedDescription
        }
    }
}

/// Event list: rows of one height, only the visible ones are drawn. The system Table used to measure every row's height on each
/// update via Auto Layout (hundreds of milliseconds), and the "Events" tab would hang on opening and on every refresh.
/// Keyboard: up and down arrows select a row, Enter opens details
struct EventList: View {
    @EnvironmentObject var model: AppModel
    @FocusState private var focused: Bool
    let rows: [EventRow]
    @Binding var selected: EventRow.ID?
    let open: (EventRow) -> Void
    let openTask: (String) -> Void
    let onlyTask: (String) -> Void
    let onlyKind: (String) -> Void
    static let kindWidth: CGFloat = 118, whoWidth: CGFloat = 96, taskWidth: CGFloat = 88
    static let timeShort: CGFloat = 76, timeLong: CGFloat = 124   // for a single day, the time is enough; for several, a date is needed too
    var timeWidth: CGFloat { model.singleDay ? EventList.timeShort : EventList.timeLong }

    /// A row's time: for a single day — hours, minutes, seconds; for several — a date as well
    func timeText(_ t: Double) -> String { model.singleDay ? Fmt.times(JSON(t)) : Fmt.when(JSON(t)) + String(format: ":%02d", Int(t) % 60) }

    var body: some View {
        VStack(spacing: Style.Space.xs) {
            HStack(spacing: Style.Space.m) {   // column headers use the same widths as the rows
                Text(L("Время")).frame(width: timeWidth, alignment: .leading)
                Text(L("Тип")).frame(width: EventList.kindWidth, alignment: .leading)
                Text(L("Кто")).frame(width: EventList.whoWidth, alignment: .leading)
                Text(L("Текст")).frame(maxWidth: .infinity, alignment: .leading)
                Text(L("Задача")).frame(width: EventList.taskWidth, alignment: .leading)
            }
            .font(Style.Text.caption.weight(.medium)).foregroundStyle(Style.Colors.secondaryText).padding(.horizontal, Style.Space.gap)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: Style.Space.xxs) { ForEach(rows) { r in line(r) } }
                }
                .focusable().focused($focused).focusEffectDisabled()
                .onKeyPress(.return) {
                    guard let r = rows.first(where: { $0.id == selected }) else { return .ignored }
                    open(r); return .handled
                }
                .onKeyPress(.upArrow) { move(-1, proxy); return .handled }
                .onKeyPress(.downArrow) { move(1, proxy); return .handled }
            }
        }
    }

    func move(_ step: Int, _ proxy: ScrollViewProxy) {
        guard !rows.isEmpty else { return }
        let next = rows.firstIndex { $0.id == selected }.map { min(rows.count - 1, max(0, $0 + step)) } ?? 0
        selected = rows[next].id
        proxy.scrollTo(rows[next].id)
    }

    func line(_ r: EventRow) -> some View {
        HStack(spacing: Style.Space.m) {
            Text(timeText(r.t)).font(Style.Text.small.monospacedDigit()).foregroundStyle(Style.Colors.secondaryText).lineLimit(1).frame(width: timeWidth, alignment: .leading)
            Tag(text: KIND_RU[r.kind] ?? r.kind, color: kindColor(r.kind)).frame(width: EventList.kindWidth, alignment: .leading)
            Text(r.who).font(Style.Text.small).lineLimit(1).frame(width: EventList.whoWidth, alignment: .leading)
            Text(r.text).font(Style.Text.small).lineLimit(1).frame(maxWidth: .infinity, alignment: .leading).help(shortened(r.text, 600))
            Group {
                if !r.tid.isEmpty {
                    Button(r.tid) { openTask(r.tid) }.buttonStyle(.plain).font(Style.Text.small.monospacedDigit()).foregroundStyle(Style.Colors.accent).help(L("Открыть задачу"))
                }
            }
            .frame(width: EventList.taskWidth, alignment: .leading)
        }
        .row(selected: selected == r.id)
        .onTapGesture(count: 2) { selected = r.id; focused = true; open(r) }
        .onTapGesture { selected = r.id; focused = true }
        .contextMenu {
            Button(L("Подробности")) { open(r) }
            Button(L("Открыть задачу")) { openTask(r.tid) }.disabled(r.tid.isEmpty)
            Button(L("Только эта задача")) { onlyTask(r.tid) }.disabled(r.tid.isEmpty)
            Button(L("Только этот вид")) { onlyKind(r.kind) }
            Button(L("Скопировать текст")) { model.copy(r.json["text"].string ?? r.text) }
        }
    }
}

/// An event kind with a count: tapping turns the filter on, tapping again turns it off
struct KindChip: View {
    @Environment(\.colorScheme) private var scheme
    let title: String
    let count: Int
    let color: Color
    let on: Bool
    let act: () -> Void
    @State private var hover = false
    var body: some View {
        let shape = RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous)
        let ink = on ? Style.Colors.onFill(color) : Style.Colors.pillText(scheme)   // unselected — a contrasting "pill", selected — the kind's bright color
        Button(action: act) {
            HStack(spacing: Style.Space.xs) {
                Text(title).font(Style.Text.caption.weight(.medium))
                Text("\(count)").font(Style.Text.caption.monospacedDigit()).foregroundStyle(ink.opacity(Style.Chart.tipMuted)).contentTransition(.numericText())
            }
            .foregroundStyle(ink)
            .padding(.horizontal, Style.Space.l).frame(height: Style.Size.controlHeight)
            .background(on ? color : Style.Colors.pill(scheme), in: shape)
            .overlay(shape.fill(Color.white.opacity(hover ? Style.Motion.hoverLight : 0)))
            .contentShape(shape)
        }
        .buttonStyle(PressStyle())
        .onHover { hover = $0 }
        .motion(Style.Motion.quick, value: hover)
        .motion(Style.Motion.quick, value: on)
        .accessibilityLabel("\(title), \(count)")
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// A sheet with event details: text, parsed fields, an error trace, and the whole raw entry
struct EventDetail: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let event: EventRow
    var text: String { event.json["text"].string ?? "" }
    var trace: String { event.json["trace"].string ?? "" }
    var facts: [(String, String)] {
        let j = event.json
        var out: [(String, String)] = []
        if !event.tid.isEmpty { out.append((L("Задача"), event.tid)) }
        if j["turn"].exists { out.append((L("Ход"), "\(j["turn"].int)")) }
        if let m = j["model"].string { out.append((L("Модель"), m)) }
        if let p = j["purpose"].string { out.append((L("Назначение"), p)) }
        if j["tokens"].exists { out.append((L("Токены"), String(format: L("%d на входе, %d на выходе"), j["tokens"]["input"].int, j["tokens"]["output"].int))) }
        let calls = j["calls"].array.map { $0["name"].str }.filter { !$0.isEmpty }
        if !calls.isEmpty { out.append((L("Инструменты"), calls.joined(separator: ", "))) }
        if let n = j["name"].string { out.append((event.kind == "tool" ? L("Инструмент") : L("Имя"), n)) }
        if j["args"].exists { out.append((L("Аргументы"), shortened(j["args"].string ?? j["args"].pretty, 300))) }
        if let s = j["seconds"].double { out.append((L("Длилось"), Fmt.dur(s))) }
        if let p = j["place"].string { out.append((L("Где"), p)) }
        if let e = j["error"].string { out.append((L("Ошибка"), e)) }
        if let v = j["verdict"].string { out.append((L("Оценка"), VERDICT_RU[v] ?? v)) }
        if let s = j["source"].string { out.append((L("Источник"), s)) }
        return out
    }
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.xl) {
            HStack(spacing: Style.Space.m) {
                Tag(text: KIND_RU[event.kind] ?? event.kind, color: kindColor(event.kind))
                Text(Fmt.whenFull(JSON(event.t))).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
                Text(event.who).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
                Spacer()
                if !event.tid.isEmpty { Button(L("Открыть задачу")) { model.openTask(event.tid); dismiss() } }
                Button(L("Скопировать")) { model.copy(event.json.pretty) }
                Button(L("Закрыть")) { dismiss() }.keyboardShortcut(.cancelAction)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: Style.Space.xl) {
                    if !text.isEmpty {
                        Block(L("Текст")) { Text(text).font(Style.Text.body).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                    }
                    if !facts.isEmpty { Block(L("Подробности")) { KV(rows: facts) } }
                    if !trace.isEmpty {
                        Block(L("Трасса")) { Text(trace).font(Style.Text.monoSmall).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                    }
                    Block(L("Запись целиком")) { Text(event.json.pretty).font(Style.Text.monoSmall).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                }
            }
        }
        .sheetStyle()
        .padding(Style.Space.xxl)
        .frame(width: Style.Size.sheet, height: DETAIL_SHEET_HEIGHT)
    }
}

// ---- process log ----

/// Tail of the process log: the server only returns what's been appended since last time, lines accumulate here.
/// One block: a line filter, "follow the end", "Reread", a line and error count; monospaced text
struct LogView: View {
    @EnvironmentObject var model: AppModel
    @State private var lines: [String] = []
    @State private var tail = ""              // the unfinished last line: waiting for a newline
    @State private var offset = -1
    @State private var size = 0.0
    @State private var path = ""
    @State private var updated: Date? = nil
    @State private var errors = 0
    @State private var filter = ""
    @State private var follow = true
    @State private var loaded = false
    @State private var failure: String? = nil
    @State private var epoch = 0              // "Reread" starts a new epoch: a reply to the previous one's request no longer appends anything
    static let keep = 4000                    // this many lines are kept in memory; older ones are dropped
    static let lineLimit = 2000               // a line longer than this isn't drawn on screen (it still copies in full)

    var shown: [String] {
        let all = tail.isEmpty ? lines : lines + [tail]
        return filter.isEmpty ? all : all.filter { $0.localizedCaseInsensitiveContains(filter) }
    }

    var body: some View {
        let visible = shown
        Block(L("Лог процесса"), lead: true, content: {
            controls
            text(visible)
        }, trailing: { summary(visible) })
            .frame(maxHeight: .infinity)
            .task { while !Task.isCancelled { await pull(); try? await Task.sleep(nanoseconds: 2_000_000_000) } }
            .actions {
                Button(L("Скопировать")) { model.copy(visible.joined(separator: "\n"), toast: String(format: L("Скопировано: %@"), "\(visible.count) \(Fmt.plural(visible.count, "строка", "строки", "строк"))")) }
                    .disabled(visible.isEmpty).help(L("Скопировать показанные строки"))
                Button(L("Показать в Finder")) { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }.disabled(path.isEmpty).help(path)
            }
    }

    var controls: some View {
        HStack(spacing: Style.Space.m) {
            TextField(L("Поиск по логу"), text: $filter).frame(maxWidth: Style.Size.search)
            Toggle(L("Следить за концом"), isOn: $follow)
            Spacer(minLength: 0)
            Button(L("Перечитать")) { reread() }.help(L("Прочитать конец лога заново"))
        }
    }
    func summary(_ visible: [String]) -> some View {
        HStack(spacing: Style.Space.m) {
            if loaded, let e = failure { Tag(text: L("не обновилось"), color: Style.Colors.bad).help(e) }
            if errors > 0 {
                Tag(text: "\(errors) \(Fmt.plural(errors, "ошибка", "ошибки", "ошибок"))", color: Style.Colors.bad)
                    .transition(Style.Motion.appear)
            }
            Text(meta(visible)).font(Style.Text.caption.monospacedDigit()).foregroundStyle(Style.Colors.secondaryText).lineLimit(1).contentTransition(.numericText())
        }
        .motion(value: errors > 0)
    }
    func meta(_ visible: [String]) -> String {
        let count = filter.isEmpty ? "\(visible.count) \(Fmt.plural(visible.count, "строка", "строки", "строк"))" : String(format: L("%d из %d"), visible.count, lines.count)
        var parts = [count, Fmt.bytes(size)]
        if let u = updated { parts.append(String(format: L("обновлено %@"), Fmt.hms.string(from: u))) }
        return parts.joined(separator: " · ")
    }

    func text(_ visible: [String]) -> some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(visible.enumerated()), id: \.offset) { _, l in LogLine(line: l) }
                    Color.clear.frame(height: Style.Size.line).id("end")
                }
                .padding(.vertical, Style.Space.xs)
            }
            .overlay {
                if !loaded { if let e = failure { RetryNote(text: e, retry: reread) } else { Loading() } }
                else if visible.isEmpty {
                    Empty(title: lines.isEmpty ? L("Лог пуст") : L("Ничего не нашлось"), icon: "terminal")
                }
            }
            .onChange(of: updated) { if follow { proxy.scrollTo("end", anchor: .bottom) } }
            .onChange(of: follow) { if follow { proxy.scrollTo("end", anchor: .bottom) } }
            .onChange(of: filter) { if follow { proxy.scrollTo("end", anchor: .bottom) } }
        }
        .frame(maxHeight: .infinity)
    }

    func pull() async {
        let mine = epoch
        do {
            let r = try await model.backend.get("log", offset >= 0 ? ["offset": "\(offset)"] : [:])
            guard mine == epoch else { return }   // "Reread" was pressed in the meantime: this reply is about the previous read
            failure = nil
            if r["fresh"].bool { lines = []; tail = ""; errors = 0 }
            offset = r["offset"].int; size = r["size"].dbl; path = r["path"].str; loaded = true
            let chunk = r["text"].str
            guard !chunk.isEmpty else { return }
            var parts = (tail + chunk).components(separatedBy: "\n")
            tail = parts.removeLast()
            errors += parts.filter(LogLine.isError).count
            lines += parts
            if lines.count > LogView.keep {
                let extra = lines.count - LogView.keep
                errors -= lines.prefix(extra).filter(LogLine.isError).count
                lines.removeFirst(extra)
            }
            updated = Date()
        } catch {
            if mine == epoch, !Task.isCancelled { failure = error.localizedDescription }
        }
    }
    func reread() { epoch += 1; offset = -1; lines = []; tail = ""; errors = 0; loaded = false; failure = nil; Task { await pull() } }
}

/// A log line: monospaced, errors and warnings are tinted, selectable and copyable
struct LogLine: View {
    let line: String
    var body: some View {
        Text(shortened(line, LogView.lineLimit)).font(Style.Text.mono).foregroundStyle(tone)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .fixedSize(horizontal: false, vertical: true)
    }
    var tone: Color { LogLine.isError(line) ? Style.Colors.badText : LogLine.isWarning(line) ? Style.Colors.warnText : .primary }
    /// Severity by the start of the line (the log is free-form prints, it has no levels): a "Traceback" trace, an exception line
    /// "ValueError: …", "Ошибка: …", "Error …". Words in the middle of the text ("errors: 0", "no error") don't count as an error
    static func isError(_ line: String) -> Bool {
        let head = line.prefix(120).lowercased().drop { $0 == " " }
        if head.hasPrefix("traceback") || head.hasPrefix("ошибка") || head.hasPrefix("error") || head.hasPrefix("exception") { return true }
        guard let colon = head.firstIndex(of: ":") else { return false }
        let word = head[..<colon]
        return !word.contains(" ") && (word.hasSuffix("error") || word.hasSuffix("exception"))
    }
    static func isWarning(_ line: String) -> Bool {
        let head = line.prefix(200).lowercased().drop { $0 == " " }
        return head.hasPrefix("warning") || head.hasPrefix("предупреждение") || head.contains("warning:")
    }
}
