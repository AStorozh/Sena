// Rules: any of Sena's recurring or conditional chores — in one list, with no split into tabs (Artemiy's request on
// 2026-10-18: "whatever the task, it should all be one single thing"). A row is either one of Tema's rules (without a
// time it always applies, with a time Sena schedules it herself), or a scheduled job with no rule, or a price alert.
// Each one can be turned off without losing it (a switch on the rule, job or alert; the text stays), removed for good — with a
// button on hover and confirmation. Data in one request — /desk/api/rules (sena/phone/desk.py: rules_view → rules.rows(), the
// rest from scheduler.jobs(), alerts.active()); background chore state comes from model.overview["scheduler"] and settings.
import SwiftUI

private let REPEAT_RU = ["once": L("один раз"), "daily": L("каждый день"), "weekdays": L("по будням"), "weekly": L("раз в неделю")]
private let ALERT_KIND_RU = ["crypto": L("криптовалюта"), "stock": L("акции"), "fx": L("валюта"), "flight": L("билеты")]
private let JOB_COLOR = Style.Colors.palette[4]   // teal: scheduled jobs with no rule

/// Scheduled job time as "DD.MM.YYYY HH:MM" — that's how scheduler.py writes and reads it; background chore days are "YYYY-MM-DD" (Fmt.dayKey)
private enum JobTime {
    static let job: DateFormatter = { let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "dd.MM.yyyy HH:mm"; return f }()
    static func date(_ s: String) -> Date? { job.date(from: s.trimmingCharacters(in: .whitespaces)) }
    /// "today 18:00", "tomorrow 09:00", otherwise "25 Oct, 09:00"
    static func label(_ d: Date) -> String {
        if Calendar.current.isDateInToday(d) { return L("сегодня") + " " + Fmt.hm.string(from: d) }
        if Calendar.current.isDateInTomorrow(d) { return L("завтра") + " " + Fmt.hm.string(from: d) }
        return Fmt.dt.string(from: d)
    }
    /// A day from a "YYYY-MM-DD" stamp: "today", "yesterday", "October 12"
    static func dayLabel(_ s: String) -> String? {
        guard let d = Fmt.dayKey.date(from: s) else { return nil }
        if Calendar.current.isDateInToday(d) { return L("сегодня") }
        if Calendar.current.isDateInYesterday(d) { return L("вчера") }
        return Fmt.dayName.string(from: d)
    }
}

/// A price like in finance.fmt: a space between thousands, a comma (a dot in English) for the fraction, as many digits as the price needs
private let priceFormatter: NumberFormatter = {
    let f = NumberFormatter(); f.locale = Fmt.dateLocale; f.numberStyle = .decimal; f.groupingSeparator = " "
    return f
}()
private func priceText(_ kind: String, _ x: Double) -> String {
    let digits = kind == "flight" ? 0 : abs(x) >= 1000 ? 0 : abs(x) >= 1 ? 2 : abs(x) >= 0.01 ? 4 : 6
    priceFormatter.minimumFractionDigits = digits; priceFormatter.maximumFractionDigits = digits
    return priceFormatter.string(from: NSNumber(value: x)) ?? "\(x)"
}

/// An on/off switch: responds right away, and if the server refuses, reverts back (set gets the new value and a way to roll back)
private struct EnableSwitch: View {
    let title: String      // for reading aloud: the switch itself has no label
    let on: Bool           // as on the server
    let help: String
    let set: (Bool, @escaping () -> Void) -> Void
    @State private var shown: Bool
    init(title: String, on: Bool, help: String, set: @escaping (Bool, @escaping () -> Void) -> Void) {
        self.title = title; self.on = on; self.help = help; self.set = set
        _shown = State(initialValue: on)
    }
    var body: some View {
        Toggle(title, isOn: Binding(get: { shown }, set: { v in shown = v; set(v) { shown = on } }))
            .labelsHidden().toggleStyle(.senaBare).help(help)
            .onChange(of: on) { _, v in shown = v }
    }
}

/// A rules-list entry card: a regular RecordCard plus a trash can (on hover, deletion after confirmation) and a switch
private struct EntryCard<Meta: View>: View {
    let icon: String
    let tint: Color     // color for the entry's kind: icon and tags
    let title: String
    let on: Bool
    let toggle: (Bool, @escaping () -> Void) -> Void
    let toggleHelp: String
    let askTitle: String   // the question in the confirmation window
    let askNote: String    // what exactly is being deleted
    let remove: () -> Void
    @ViewBuilder let meta: () -> Meta
    var body: some View {
        RecordCard(icon: icon, tint: tint, title: title, dim: !on, meta: meta) { hover in
            Button(action: askRemove) {
                Glyph("trash").font(Style.Icon.inline).foregroundStyle(Style.Colors.secondaryText)
                    .frame(width: Style.Size.controlHeight, height: Style.Size.controlHeight).contentShape(Rectangle())
            }
            .buttonStyle(.plain).help(L("Удалить")).accessibilityLabel(L("Удалить")).opacity(hover ? 1 : 0).allowsHitTesting(hover)
            EnableSwitch(title: title, on: on, help: toggleHelp, set: toggle)
        }
        .help(title)   // long text in the card gets truncated: the full text is in the tooltip
    }
    func askRemove() { if confirm(askTitle, askNote, button: L("Удалить"), destructive: true) { remove() } }
}

/// A hint placeholder inside a block while the list is empty: one or two lines instead of a big empty screen
private struct Hint: View {
    let text: String
    var body: some View { Text(text).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText).padding(.horizontal, Style.Space.l).padding(.vertical, Style.Space.m) }
}

// ---------- data from three sources, gathered into one list ----------

private struct JobItem: Identifiable {
    let id: String, text: String, at: String, repeatKind: String, attempts: Int, enabled: Bool
    let fails: Int   // a repeating job's failures in a row: past scheduler.FAILS_OFF the scheduler switches it off itself
    init(_ j: JSON) {
        id = j["id"].str; text = j["text"].str; at = j["at"].str; repeatKind = j["repeat"].str; attempts = j["attempts"].int
        enabled = j["enabled"].bool || !j["enabled"].exists; fails = j["fails"].int
    }
    /// A failing job, in words: one that just looks "off" may have been switched off by its own failures
    var failing: Tag? {
        guard fails > 0 else { return nil }
        return enabled ? Tag(text: String(format: L("сбоев подряд: %d"), fails), color: Style.Colors.warn)
                       : Tag(text: String(format: L("выключено · сбоев подряд: %d"), fails), color: Style.Colors.bad)
    }
    var date: Date? { JobTime.date(at) }
    var when: String { date.map(JobTime.label) ?? at }
    var repeatRU: String { REPEAT_RU[repeatKind] ?? repeatKind }
}

private struct RuleItem: Identifiable {
    let id: String, text: String, once: Bool, planned: Bool, enabled: Bool, note: String
    let jobs: [JobItem]   // scheduled jobs that Sena set up from this rule
    init(_ j: JSON) {
        id = j["id"].str; text = j["text"].str; once = j["once"].bool; planned = j["planned"].bool
        enabled = j["enabled"].bool || !j["enabled"].exists; note = j["note"].str   // a new field: in old entries, treated as "enabled"
        jobs = j["jobs"].array.map(JobItem.init)
    }
    var timed: Bool { !jobs.isEmpty }
    var kind: String { timed ? (once ? L("один раз") : L("по расписанию")) : planned ? L("постоянное") : L("планируется…") }
    var color: Color { timed ? (once ? Style.Colors.warn : Style.Colors.info) : planned ? Style.Colors.ok : Style.Colors.ink }
    var icon: String { !planned && !timed ? "hourglass" : timed ? (once ? "alarm" : "repeat") : "infinity" }
}

private struct AlertItem: Identifiable {
    let id: String, kind: String, symbol: String, condition: String, value: Double, once: Bool, fired: Bool, enabled: Bool
    init(_ j: JSON) {
        id = j["id"].str; kind = j["kind"].str; symbol = j["symbol"].str; condition = j["condition"].str; value = j["value"].dbl
        once = j["once"].bool; fired = j["fired"].bool
        enabled = j["enabled"].bool || !j["enabled"].exists
    }
    var title: String { "\(symbol) \(condition == "above" ? L("выше") : L("ниже")) \(priceText(kind, value))" }
    var icon: String {
        switch kind { case "crypto": return "bitcoinsign"; case "stock": return "chart.line.uptrend.xyaxis"; case "fx": return "dollarsign"; case "flight": return "airplane"; default: return "bell" }
    }
}

/// One row of the shared list — whichever source it came from
private enum Entry: Identifiable {
    case rule(RuleItem), job(JobItem), alert(AlertItem)
    var id: String {
        switch self { case .rule(let r): return "r" + r.id; case .job(let j): return "j" + j.id; case .alert(let a): return "a" + a.id }
    }
    /// The soonest time it will fire; no time (a permanent rule, an alert) — goes to the end of the list
    var due: Date? {
        switch self { case .rule(let r): return r.jobs.compactMap(\.date).min(); case .job(let j): return j.date; case .alert: return nil }
    }
}

// ---------- the unified section ----------

struct RulesView: View {
    @EnvironmentObject var model: AppModel
    @State private var ruleRows: [RuleItem] = []
    @State private var jobRows: [JobItem] = []       // jobs with no rule: left over from before, or set up by Sena directly
    @State private var alertRows: [AlertItem] = []
    @State private var loaded = false
    @State private var failed: String? = nil
    @State private var lastRaw = JSON(nil)
    @State private var input = ""
    @State private var adding = false
    @State private var editing: BackgroundJob?   // the card whose schedule editor is open
    var trimmed: String { input.trimmingCharacters(in: .whitespaces) }
    private var entries: [Entry] {
        (ruleRows.map(Entry.rule) + jobRows.map(Entry.job) + alertRows.map(Entry.alert))
            .sorted { ($0.due ?? .distantFuture) < ($1.due ?? .distantFuture) }   // soonest in time — on top; no time — at the bottom
    }

    var body: some View {
        let all = entries   // sorted once per redraw, not once per access
        ScrollView {
            VStack(alignment: .leading, spacing: Style.Space.xl) {
                composer
                Block(L("Что Сена делает сама"), content: {
                    if !loaded {
                        if let f = failed { loadFailed(f) } else { SkeletonList(rows: 4) }
                    }
                    else if all.isEmpty { Hint(text: L("Пока пусто: напиши правило выше, например про расписание")) }
                    else { list(all) }
                }, trailing: { listCount("\(all.count) \(Fmt.plural(all.count, "запись", "записи", "записей"))") })
                background
            }
            .motion(value: all.map(\.id))
        }
        .sheet(item: $editing) { BackgroundEditor(job: $0, settings: model.settings) }
        .task { while !Task.isCancelled { await load(); try? await Task.sleep(nanoseconds: 10_000_000_000) } }
    }

    var composer: some View {
        Block(L("Новое правило"), lead: true) {
            HStack(spacing: Style.Space.m) {
                TextField(L("Например: буди меня каждый день в 8"), text: $input).onSubmit(add)
                Button(L("Добавить"), action: add).buttonStyle(.primary).disabled(trimmed.count < 3 || adding)
            }
        }
    }

    private func list(_ items: [Entry]) -> some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: Style.Size.entry), spacing: Style.Space.gap, alignment: .top)], spacing: Style.Space.gap) {
            ForEach(items) { e in row(e).transition(Style.Motion.remove) }
        }
    }

    @ViewBuilder private func row(_ e: Entry) -> some View {
        switch e {
        case .rule(let r):
            EntryCard(icon: r.icon, tint: r.color, title: r.text, on: r.enabled, toggle: { toggleRule(r.id, $0, $1) },
                      toggleHelp: r.enabled ? L("Выключить: текст останется, действовать перестанет") : L("Включить обратно"),
                      askTitle: L("Удалить правило?"), askNote: r.text + (r.timed ? "\n" + L("Вместе с ним уйдут его задания из расписания.") : ""), remove: { removeRule(r.id) }) {
                Tag(text: r.kind, color: r.enabled ? r.color : Style.Colors.off)
                if !r.planned { ArcSpinner() }
                ForEach(r.jobs) { j in
                    Text(j.when + (j.repeatKind == "once" ? "" : ", " + j.repeatRU)).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
                    if let f = j.failing { f }
                }
                if r.planned && !r.timed && !r.note.isEmpty && r.note != "без расписания" {
                    Text(r.note).font(Style.Text.caption).foregroundStyle(Style.Colors.tertiaryText).lineLimit(1)   // the scheduler's reply if it couldn't set up the chore
                }
            }
        case .job(let j):
            let overdue = (j.date ?? .distantFuture) < Date()
            EntryCard(icon: "calendar", tint: JOB_COLOR, title: j.text, on: j.enabled, toggle: { toggleJob(j.id, $0, $1) },
                      toggleHelp: j.enabled ? L("Выключить, не удаляя") : L("Включить обратно"),
                      askTitle: L("Удалить задание?"), askNote: j.when + " — " + j.text, remove: { act(L("Задание"), "job_remove", ["id": j.id]) }) {
                Tag(text: j.repeatRU, color: j.enabled ? JOB_COLOR : Style.Colors.off)
                // for a disabled job, "time's up" would be misleading: it won't fire
                Text(j.when + " · " + (!j.enabled ? L("выключено") : j.date.map { overdue ? L("время пришло") : L("через ") + Fmt.dur($0.timeIntervalSinceNow) } ?? L("время не распознано")))
                    .font(Style.Text.caption).foregroundStyle(overdue && j.enabled ? Style.Colors.warn : Style.Colors.secondaryText)
                if j.attempts > 0 { Tag(text: String(format: L("повтор после сбоя: %d"), j.attempts), color: Style.Colors.warn) }
                if let f = j.failing { f }
                if overdue && j.enabled && !model.alive { Tag(text: L("ждёт, пока Сену включат"), color: Style.Colors.warn) }
            }
        case .alert(let a):
            EntryCard(icon: a.icon, tint: Style.Colors.model, title: a.title, on: a.enabled, toggle: { toggleAlert(a.id, $0, $1) },
                      toggleHelp: a.enabled ? L("Выключить, не удаляя") : L("Включить обратно"),
                      askTitle: L("Удалить сигнал?"), askNote: a.title, remove: { act(L("Сигнал"), "alert_remove", ["id": a.id]) }) {
                Tag(text: L("сигнал · ") + (ALERT_KIND_RU[a.kind] ?? a.kind), color: a.enabled ? Style.Colors.model : Style.Colors.off)
                Text(a.once ? L("сообщит один раз и удалится") : L("сообщает каждый раз, когда цена пересечёт порог")).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
                if a.fired { Tag(text: L("сработал, ждёт, пока цена отойдёт"), color: Style.Colors.warn) }
            }
        }
    }

    // Background chores: what's on, how often, when it last ran; "Run" starts it manually.
    // AdaptiveRow — the same trick as the costs blocks: evenly spaced in a row, stacked in a narrow window
    var background: some View {
        Block(L("Фоновые дела"), lead: true) {
            if model.overview.exists {
                AdaptiveRow { ForEach(backgroundJobs()) { b in BackgroundCard(job: b, edit: { editing = b }) } }
            } else {
                SkeletonList(rows: 2)   // settings haven't arrived yet: "off" on the cards would be a lie
            }
        }
    }

    private func backgroundJobs() -> [BackgroundJob] {
        let s = model.settings, st = model.overview["scheduler"]["state"], now = Date().timeIntervalSince1970
        func was(_ t: Double) -> String { t > 0 ? L("последний раз: ") + Fmt.ago(t) : L("пока не было") }
        func retry(_ key: String) -> String? {
            let t = st[key + "_retry"].dbl, n = st[key + "_retry_attempts"].int   // this is how the scheduler names the retry counter: the retry key plus "_attempts"
            if t > now { return L("сбой, повтор в ") + Fmt.time(JSON(t)) + (n > 0 ? String(format: L(" (попытка %d)"), n + 1) : "") }
            return nil
        }
        func name(_ key: String, _ on: String, _ off: String) -> String { s[key].bool ? on : off }
        func day(_ key: String) -> String { st[key].string.flatMap(JobTime.dayLabel).map { L("последний раз: ") + $0 } ?? L("пока не было") }
        return [
            BackgroundJob(id: "heartbeat", name: L("Фоновая проверка"), rawName: "Фоновая проверка", on: s["heartbeat"].bool,
                          about: L("Смотрит чаты, почту и ближайшие дела"),
                          detail: name("heartbeat", String(format: L("каждые %d мин · %d:00–%d:00"), s["heartbeat_every_min"].int, s["active_from"].int, s["active_to"].int), L("выключена")),
                          last: was(st["heartbeat"].dbl), warn: nil),
            // one switch for all of her watching (app.py on_watch): price alerts, the news on the topics asked for, pages, the site's uptime
            BackgroundJob(id: "watch", name: L("Слежка"), rawName: "Сигналы по цене", on: s["watch"].bool,
                          about: L("Цены, новости по твоим темам, страницы и твой сайт: пишет, когда что-то изменилось"),
                          detail: name("watch", String(format: L("каждые %d мин"), s["watch_every_min"].int), L("выключены")),
                          last: was(st["watch"].dbl), warn: nil),
            BackgroundJob(id: "tidy", name: L("Ночная уборка"), rawName: "Ночная уборка", on: s["tidy"].bool,
                          about: L("Чистит память и навыки, делает копию"),
                          detail: name("tidy", String(format: L("раз в сутки · после %d:00"), s["tidy_hour"].int), L("выключена")),
                          last: day("tidy"), warn: retry("tidy")),
            BackgroundJob(id: "weekly", name: L("Недельный разбор"), rawName: "Недельный разбор", on: s["weekly"].bool,
                          about: L("Оценивает качество задач за неделю"),
                          detail: name("weekly", "\(Fmt.weekday(s["weekly_day"].int)) · \(s["weekly_hour"].int):00", L("выключен")),
                          last: day("weekly"), warn: retry("weekly")),
        ]
    }

    func load() async {
        do {
            let r = try await model.backend.get("rules")
            failed = nil
            if loaded && r.isSame(lastRaw) { return }
            lastRaw = r
            ruleRows = r["rules"].array.map(RuleItem.init)
            jobRows = r["jobs"].array.map(JobItem.init)
            alertRows = r["alerts"].array.map(AlertItem.init)
            loaded = true
        } catch { if !Task.isCancelled && !loaded { failed = error.localizedDescription } }
    }
    /// An action plus reloading the list; fail — revert to what the screen already showed ahead of time (the toggle)
    private func act(_ what: String, _ name: String, _ body: [String: Any], fail: (() -> Void)? = nil) {
        model.runAction(what, name, body, done: { _ in Task { await load() } }, fail: fail)
    }
    /// A rule's schedule is put together in the background: load right away and once more a bit later, once the scheduler has set it up
    private func reloadSoon() { Task { await load(); try? await Task.sleep(nanoseconds: 2_500_000_000); await load() } }
    func add() {
        let t = trimmed
        guard t.count >= 3, !adding else { return }
        adding = true; input = ""   // the field clears right away, on failure the text is put back: a second tap won't create a duplicate
        model.runAction(L("Правило"), "rule_add", ["text": t], done: { _ in adding = false; reloadSoon() },
                        fail: { adding = false; if input.isEmpty { input = t } })
    }
    private func removeRule(_ id: String) { act(L("Правило"), "rule_remove", ["id": id]) }
    private func toggleRule(_ id: String, _ on: Bool, _ revert: @escaping () -> Void) {
        model.runAction(L("Правило"), "rule_toggle", ["id": id, "enabled": on], done: { _ in reloadSoon() }, fail: revert)   // a turned-on rule gets scheduled again
    }
    private func toggleJob(_ id: String, _ on: Bool, _ revert: @escaping () -> Void) { act(L("Задание"), "job_toggle", ["id": id, "enabled": on], fail: revert) }
    private func toggleAlert(_ id: String, _ on: Bool, _ revert: @escaping () -> Void) { act(L("Сигнал"), "alert_toggle", ["id": id, "enabled": on], fail: revert) }
}

/// A background chore: the settings key matches its id (heartbeat/watch/tidy/weekly), how it's named in scheduler._busy, whether it's on, its schedule, when it last ran
private struct BackgroundJob: Identifiable {
    let id: String
    let name: String       // for display and toasts (translated)
    let rawName: String    // how the chore is named in scheduler._spawn — compared against the server's "busy", not translated
    let on: Bool
    let about: String      // what the chore does, in one line
    let detail: String
    let last: String
    let warn: String?     // retry after a failure
}

/// A background chore card: name and switch, schedule, last run, "Run" (the watching has none: it comes every few minutes anyway,
/// and the server has no manual run for it). Tapping the card opens the schedule editor
private struct BackgroundCard: View {
    @EnvironmentObject var model: AppModel
    let job: BackgroundJob
    let edit: () -> Void
    @State private var starting = false   // "Run" was tapped, no reply yet
    var busy: Bool { model.overview["scheduler"]["busy"].array.contains { $0.str == job.rawName } }
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.s) {
            HStack(spacing: Style.Space.m) {
                Text(job.name).font(Style.Text.heading)
                Spacer()
                if busy { Tag(text: L("идёт сейчас"), color: Style.Colors.warn).transition(.opacity) }
                EnableSwitch(title: job.name, on: job.on, help: "") { v, revert in
                    model.runAction(job.name, "settings", [job.id: v], fail: revert)
                }
            }
            Text(job.about).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText).lineLimit(2, reservesSpace: true)   // two lines for everyone: the schedule sits on a consistent line below the description
            Text(job.detail).font(Style.Text.small.weight(.medium)).lineLimit(1)
            Spacer(minLength: 0)   // the bottom of the card always stays at the bottom: cards in a row are the same height
            HStack(spacing: Style.Space.m) {
                Text(job.warn ?? job.last).font(Style.Text.caption).foregroundStyle(job.warn == nil ? Style.Colors.secondaryText : Style.Colors.warnText).lineLimit(1)
                Spacer()
                if job.id != "watch" {
                    Button(L("Запустить"), action: run).disabled(busy || starting).help(L("Запустить, не дожидаясь срока"))
                }
            }
            .frame(height: Style.Size.controlHeight)   // the labels at the bottom sit on one line, whether there's a button or not
        }
        .padding(Style.Space.l)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .island()   // a white card on the dark "Background chores" block
        .lift()
        .contentShape(RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
        .onTapGesture(perform: edit)
        .help(L("Настроить расписание"))
        .accessibilityAction(named: L("Настроить расписание"), edit)
        .motion(Style.Motion.quick, value: busy)
    }
    /// While the request is in flight, the button is disabled: running the same chore a second time is a second paid run
    func run() {
        guard model.alive else { model.say(L("Сена выключена — включи её кнопкой в меню")); return }
        guard confirmPaidRun(job.name) else { return }
        starting = true
        model.runAction(job.name, job.id, done: { _ in starting = false }, fail: { starting = false })
    }
}

/// Editing a background chore's schedule: one sentence with dropdown values ("Every 60 min from 9:00 to 23:00"), no extra labels.
/// Turning it on and off happens on the card itself
private struct BackgroundEditor: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let job: BackgroundJob
    @State private var every: Int
    @State private var from: Int
    @State private var to: Int
    @State private var watch: Int
    @State private var tidyHour: Int
    @State private var day: Int
    @State private var hour: Int
    @State private var saving = false
    init(job: BackgroundJob, settings s: JSON) {
        self.job = job
        let start = s["active_from"].int
        _every = State(initialValue: s["heartbeat_every_min"].int); _from = State(initialValue: start); _to = State(initialValue: max(s["active_to"].int, min(start + 1, 24)))
        _watch = State(initialValue: s["watch_every_min"].int); _tidyHour = State(initialValue: s["tidy_hour"].int)
        _day = State(initialValue: s["weekly_day"].int); _hour = State(initialValue: s["weekly_hour"].int)
    }
    var payload: [String: Any] {
        switch job.id {
        case "heartbeat": return ["heartbeat_every_min": every, "active_from": from, "active_to": to]
        case "watch": return ["watch_every_min": watch]
        case "tidy": return ["tidy_hour": tidyHour]
        default: return ["weekly_day": day, "weekly_hour": hour]
        }
    }
    func hours(_ n: Int) -> String { String(format: L("%d:00"), n) }
    func minutes(_ n: Int) -> String { String(format: L("%d мин"), n) }
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.xl) {
            VStack(alignment: .leading, spacing: Style.Space.xs) {
                Text(job.name).font(Style.Text.title)
                Text(job.about).font(Style.Text.body).foregroundStyle(Style.Colors.secondaryText)
            }
            HStack(spacing: Style.Space.m) {
                switch job.id {
                case "heartbeat":
                    Text(L("Каждые"))
                    Choice(value: $every, options: Array(stride(from: 15, through: 240, by: 15)), label: minutes)
                    Text(L("с"))
                    Choice(value: $from, options: Array(0...23), label: hours)
                    Text(L("до"))
                    Choice(value: $to, options: Array(min(from + 1, 24)...24), label: hours)   // the end is after the start: otherwise there are no active hours and the check never fires
                case "watch":
                    Text(L("Каждые"))
                    Choice(value: $watch, options: [1, 2, 5, 10, 15, 30, 60], label: minutes)
                case "tidy":
                    Text(L("Раз в сутки, после"))
                    Choice(value: $tidyHour, options: Array(0...23), label: hours)
                default:
                    Text(L("Раз в неделю:"))
                    Choice(value: $day, options: Array(0...6), label: Fmt.weekday, width: Style.Size.select)
                    Text(L("в"))
                    Choice(value: $hour, options: Array(0...23), label: hours)
                }
                Spacer(minLength: 0)
            }
            .font(Style.Text.body)
            .onChange(of: from) { _, f in if to <= f { to = min(f + 1, 24) } }
            HStack(spacing: Style.Space.m) {
                Spacer()
                Button(L("Отмена")) { dismiss() }.buttonStyle(.secondary).keyboardShortcut(.cancelAction)
                Button(L("Сохранить"), action: save).buttonStyle(.primary).keyboardShortcut(.defaultAction).disabled(saving)
            }
        }
        .sheetStyle()
        .padding(Style.Space.xxl).frame(width: Style.Size.sheet)
    }
    func save() {
        saving = true
        model.runAction(job.name, "settings", payload, done: { _ in dismiss() }, fail: { saving = false })
    }
}
