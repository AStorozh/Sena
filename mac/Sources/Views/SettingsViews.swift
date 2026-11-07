// Settings: three tabs in the header. "Behavior" — reply style, learning, channels, money (saved with a button in the header; leaving with
// unsaved changes asks first). Sena's background chores (checks, alerts, cleanup, triage) are edited under "Rules" (RulesViews).
// "System" — process, connections and keys, app theme, maintenance, disk space, technical details (collapsed by default).
// "Self-development" — edits to Sena's own code: status, change rows, details with a report in a sheet, apply or reject with confirmation.
// Everything in blocks, no dividers; styling from Style.
import SwiftUI

/// Labels for setting options (settings.OPTIONS on the server — a fixed, short list): translation for display
private let OPTION_RU = ["кратко": L("кратко"), "подробно": L("подробно"), "быстро": L("быстро"), "глубже": L("глубже"),
                         "голос Сены": L("голос Сены"), "домашний голос": L("домашний голос")]
/// Labels for disk space entries: the server sends them in Russian (data.state), an unknown label is shown as is
private let DISK_RU = ["события": L("события"), "индекс поиска": L("индекс поиска"), "записи": L("записи"),
                       "скачанное": L("скачанное"), "сделанное": L("сделанное"), "присланное": L("присланное"), "заметки": L("заметки"), "копии": L("копии")]
private let DISK_NAME: CGFloat = 112, DISK_SIZE: CGFloat = 72   // columns of the disk space list
let DETAIL_SHEET_HEIGHT: CGFloat = 480                            // height of detail sheets (a change, an event); width is Style.Size.sheet

/// Two stacks of blocks side by side when the window is wide; in a narrow one (one threshold for the whole app — model.narrow) — a single stack: left first, then right
struct SettingsFlow<Left: View, Right: View>: View {
    @Environment(\.narrowWindow) private var narrow
    @ViewBuilder let left: () -> Left
    @ViewBuilder let right: () -> Right
    var body: some View {
        if narrow {
            VStack(spacing: Style.Space.xl) { left(); right() }
        } else {
            HStack(alignment: .top, spacing: Style.Space.xl) {
                VStack(spacing: Style.Space.xl) { left() }.frame(maxWidth: .infinity)
                VStack(spacing: Style.Space.xl) { right() }.frame(maxWidth: .infinity)
            }
        }
    }
}

/// A settings row: title on the left, control on the right. The control itself also gets the title (control(title)): it doubles as its VoiceOver label
struct SettingRow<Control: View>: View {
    let title: String
    let control: (String) -> Control
    init(_ title: String, @ViewBuilder control: @escaping (String) -> Control) { self.title = title; self.control = control }
    var body: some View {
        HStack(alignment: .center, spacing: Style.Space.xl) {
            Text(title).font(Style.Text.body)
            Spacer(minLength: Style.Space.xl)
            control(title)
        }
        .frame(minHeight: Style.Size.controlHeight)   // a settings row is one height whether it has a switch, a list or a field
        .padding(.vertical, Style.Space.xs)
    }
}

// ---- Behavior ----

/// Behavior setting values: compared against the saved ones to know whether there's anything unsaved
struct Behaviour: Equatable {
    var verbosity = "low", effort = "low", voice = ""   // until the server's settings arrive
    var judge = true, reflect = true, voiceReply = true
    var budget = 0.0, taskBudget = 0.0   // the day's budget and one task's (the brain sums up and stops past it; 0 — no limit)
    init() {}
    init(_ s: JSON) {
        guard s.exists else { return }
        verbosity = s["verbosity"].str; effort = s["effort"].str; voice = s["voice"].str
        judge = s["judge"].bool; reflect = s["reflect"].bool; voiceReply = s["voice_reply"].bool
        budget = s["budget_usd_day"].dbl; taskBudget = s["task_budget_usd"].dbl
    }
    var payload: [String: Any] {
        ["verbosity": verbosity, "effort": effort, "voice": voice, "judge": judge, "reflect": reflect, "voice_reply": voiceReply, "budget_usd_day": budget,
         "task_budget_usd": taskBudget]
    }
    /// Only what differs from old: Tema's edits are sent to the server, not every value (otherwise an outside edit, like
    /// "reply shorter" said in chat, would get overwritten by the old value from the screen)
    func changes(from old: Behaviour) -> [String: Any] {
        let was = old.payload
        return payload.filter { key, value in
            guard let before = was[key] else { return true }
            return !(value as AnyObject).isEqual(before as AnyObject)
        }
    }
    /// New data from the server over the screen: fields Tema hasn't touched (they equal old) are taken from the server, edited ones stay
    func takingUntouched(from fresh: Behaviour, was old: Behaviour) -> Behaviour {
        var r = self
        if verbosity == old.verbosity { r.verbosity = fresh.verbosity }
        if effort == old.effort { r.effort = fresh.effort }
        if voice == old.voice { r.voice = fresh.voice }
        if judge == old.judge { r.judge = fresh.judge }
        if reflect == old.reflect { r.reflect = fresh.reflect }
        if voiceReply == old.voiceReply { r.voiceReply = fresh.voiceReply }
        if budget == old.budget { r.budget = fresh.budget }
        if taskBudget == old.taskBudget { r.taskBudget = fresh.taskBudget }
        return r
    }
}

struct BehaviourView: View {
    @EnvironmentObject var model: AppModel
    @State private var s = Behaviour()        // what's on screen
    @State private var saved = Behaviour()    // what's on the server
    @State private var c = JSON(nil)          // config: style and voice options, number ranges
    @State private var configFailed = false
    @State private var budgetText = ""        // budget as it's typed: the number is picked up live, not only on Enter
    @State private var taskBudgetText = ""    // one task's budget, the same way
    @State private var saving = false
    @FocusState private var budgetFocused: Bool
    @FocusState private var taskBudgetFocused: Bool
    var dirty: Bool { s != saved }
    /// Budget bounds from the server (settings.RANGES); until they arrive — the same ones already here
    var budgetRange: ClosedRange<Double> { range("budget_usd_day", upTo: 1000) }
    var taskBudgetRange: ClosedRange<Double> { range("task_budget_usd", upTo: 100) }
    func range(_ key: String, upTo fallback: Double) -> ClosedRange<Double> {
        let r = c["ranges"][key].array
        let lo = r.first?.dbl ?? 0, hi = r.count > 1 ? r[1].dbl : fallback
        return lo <= hi ? lo...hi : 0...fallback
    }

    var body: some View {
        ScrollView {
            SettingsFlow { style; learning } right: { channels; money }
        }
        .task { await loadConfig() }
        .onAppear { let now = Behaviour(model.settings); s = now; saved = now; budgetText = BehaviourView.text(now.budget); taskBudgetText = BehaviourView.text(now.taskBudget) }
        .onChange(of: Behaviour(model.settings)) { _, fresh in s = s.takingUntouched(from: fresh, was: saved); saved = fresh }   // Sena changed the style herself ("reply shorter") — the screen picks it up
        .onChange(of: s.budget) { _, v in if !budgetFocused { budgetText = BehaviourView.text(v) } }
        .onChange(of: budgetFocused) { _, on in if !on { budgetText = BehaviourView.text(s.budget) } }
        .onChange(of: s.taskBudget) { _, v in if !taskBudgetFocused { taskBudgetText = BehaviourView.text(v) } }
        .onChange(of: taskBudgetFocused) { _, on in if !on { taskBudgetText = BehaviourView.text(s.taskBudget) } }
        .onChange(of: dirty) { _, d in model.setUnsaved("settings", d) }
        .onDisappear { model.setUnsaved("settings", false) }
        .actions {
            if dirty { Text(L("есть несохранённое")).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText) }
            Button(L("Сохранить"), action: save).buttonStyle(.primary).disabled(!dirty || saving).keyboardShortcut("s", modifiers: .command)
        }
    }

    var style: some View {
        Block(L("Стиль ответов"), lead: true) {
            SettingRow(L("Длина ответов")) { picker($0, "verbosity", $s.verbosity) }
            SettingRow(L("Глубина мышления")) { picker($0, "effort", $s.effort) }
            SettingRow(L("Голос")) { picker($0, "voice", $s.voice) }
            if configFailed { Text(L("Список вариантов не загрузился, пробую ещё раз")).font(Style.Text.caption).foregroundStyle(Style.Colors.badText) }
        }
    }
    var learning: some View {
        Block(L("Учёба и характер")) {
            SettingRow(L("Разбор после задач")) { toggle($0, $s.judge) }
            SettingRow(L("Размышления о характере")) { toggle($0, $s.reflect) }
        }
    }
    var channels: some View {
        Block(L("Каналы")) { SettingRow(L("Отвечать голосом в Телеграме")) { toggle($0, $s.voiceReply) } }
    }
    var money: some View {
        let b = model.overview["budget"]
        return Block(L("Деньги"), lead: true) {
            SettingRow(L("Дневной бюджет, $")) { title in
                TextField("0", text: $budgetText)
                    .accessibilityLabel(title)
                    .focused($budgetFocused)
                    .multilineTextAlignment(.trailing).frame(width: Style.Size.selectShort)
                    .onChange(of: budgetText) { _, t in if let v = parsed(t, budgetRange) { s.budget = v } }
                    .onSubmit { budgetText = BehaviourView.text(s.budget) }
                    .help(L("0 — без предела"))
            }
            SettingRow(L("Предел одной задачи, $")) { title in   // past it the brain sums up what's done and stops (a long work has its own budget)
                TextField("0", text: $taskBudgetText)
                    .accessibilityLabel(title)
                    .focused($taskBudgetFocused)
                    .multilineTextAlignment(.trailing).frame(width: Style.Size.selectShort)
                    .onChange(of: taskBudgetText) { _, t in if let v = parsed(t, taskBudgetRange) { s.taskBudget = v } }
                    .onSubmit { taskBudgetText = BehaviourView.text(s.taskBudget) }
                    .help(L("Одна задача тратит не больше: дальше Сена подводит итог и останавливается. 0 — без предела"))
            }
            BudgetLine(title: L("Расход сегодня"), spent: b["spent"].dbl, limit: b["limit"].dbl)
        }
    }

    /// A switch: its title is hidden from the screen (it's to the left in the row), but VoiceOver reads it
    func toggle(_ title: String, _ v: Binding<Bool>) -> some View { Toggle(title, isOn: v).labelsHidden().toggleStyle(.senaBare) }
    func picker(_ title: String, _ key: String, _ sel: Binding<String>) -> some View {
        let opts = options(key, current: sel.wrappedValue)
        return Select(selection: sel, options: opts).accessibilityLabel(title).accessibilityValue(opts.first { $0.0 == sel.wrappedValue }?.1 ?? "")
    }
    /// Options from the server (code — label in Russian; the label's translation is OPTION_RU, the code is left untouched), sorted by code: for length and
    /// depth that gives "short — detailed", "fast — deeper". The server's own order never reaches the screen (a parsed JSON dictionary has no order). Until options arrive — just the current value
    func options(_ key: String, current: String) -> [(String, String)] {
        let o = c["options"][key].dict.map { ($0.key, OPTION_RU[$0.value.str] ?? $0.value.str) }.sorted { $0.0 < $1.0 }
        return o.isEmpty ? [(current, current)] : o
    }

    /// A number from the typed text: comma or dot, within the server's bounds; empty — 0 (no limit); not a number (including nan and inf) — nothing
    func parsed(_ t: String, _ range: ClosedRange<Double>) -> Double? {
        let clean = t.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: ",", with: ".")
        if clean.isEmpty { return range.lowerBound }
        guard let v = Double(clean), v.isFinite else { return nil }
        return min(range.upperBound, max(range.lowerBound, v))
    }
    static func text(_ v: Double) -> String { v == v.rounded() ? String(format: "%.0f", v) : String(format: "%.2f", v) }

    /// Style options are loaded until it succeeds: without them the lists show just the current value
    func loadConfig() async {
        while !Task.isCancelled {
            do { c = try await model.backend.get("config"); configFailed = false; return }
            catch { if Task.isCancelled { return }; configFailed = true }
            try? await Task.sleep(nanoseconds: 3_000_000_000)
        }
    }

    /// Save the edits and show what the server actually accepted: it silently drops a value outside the bounds
    func save() {
        guard dirty, !saving else { return }
        budgetText = BehaviourView.text(s.budget); taskBudgetText = BehaviourView.text(s.taskBudget)
        let now = s, sent = now.changes(from: saved)
        saving = true
        Task {
            defer { saving = false }
            do {
                let r = try await model.backend.act("settings", sent)
                let kept = r["settings"].exists ? Behaviour(r["settings"]) : now
                saved = kept
                if s == now { s = kept; budgetText = BehaviourView.text(kept.budget); taskBudgetText = BehaviourView.text(kept.taskBudget) }   // nothing changed while the request was in flight: show what was accepted
                let refused = now.changes(from: kept).keys.contains { sent[$0] != nil }
                if refused { model.say(L("Сервер принял не все значения, показаны сохранённые"), hold: 7) }
                else { model.say(String(format: L("%@: готово"), L("Настройки"))) }
                await model.poll()
            } catch { model.say(String(format: L("%@: %@"), L("Настройки"), error.localizedDescription), hold: 7) }
        }
    }
}

// ---- System ----

/// A connection light: dot, name, state — a contrasting tile (dark on a light block)
struct SystemLight: View {
    let title: String
    let on: Bool
    var text = ""
    var mono = false
    var bad = false
    var body: some View {
        HStack(spacing: Style.Space.m) {
            Circle().fill(on ? Style.Colors.ok : bad ? Style.Colors.bad : Style.Colors.off).frame(width: Style.Size.dot, height: Style.Size.dot)
            VStack(alignment: .leading, spacing: 0) {
                Text(title).font(mono ? Style.Text.mono : Style.Text.small.weight(.medium)).lineLimit(1)
                if !text.isEmpty { Text(text).font(Style.Text.caption).foregroundStyle(bad ? Style.Colors.badText : Style.Colors.secondaryText).lineLimit(1) }
            }
            Spacer(minLength: 0)
        }
        .padding(Style.Space.m)
        .darkIsland()
        .accessibilityElement(children: .combine)
    }
}

/// A maintenance row: a card — what it will do, and a button on the right
struct MaintenanceRow: View {
    let title: String
    let text: String
    let button: String
    let enabled: Bool
    let act: () -> Void
    init(_ title: String, _ text: String, button: String = L("Запустить"), enabled: Bool = true, act: @escaping () -> Void) {
        self.title = title; self.text = text; self.button = button; self.enabled = enabled; self.act = act
    }
    var body: some View {
        RecordCard(icon: "wrench.and.screwdriver", tint: Style.Colors.tint(title), title: title, lines: 1, meta: {
            Text(text).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(1)
        }, trailing: { _ in
            Button(button, action: act).disabled(!enabled)
        })
        .help(text)
    }
}

struct SystemView: View {
    @EnvironmentObject var model: AppModel
    @State private var tech = false   // technical details are collapsed by default
    @AppStorage("appTheme") private var theme = "system"
    /// Theme of the app itself (not Sena's): system, light or dark; applies to all windows right away
    var appearance: some View {
        Block(L("Приложение")) {
            SettingRow(L("Тема")) { _ in
                Segments(selection: $theme, options: [("system", L("Системная")), ("light", L("Светлая")), ("dark", L("Тёмная"))])
            }
        }
    }
    var body: some View {
        Polling(seconds: 60, load: { (try await model.backend.get("config"), try await model.backend.get("state")) }) { (c, st) in
            ScrollView {
                SettingsFlow { appearance; process; maintenance(c) } right: { connections(c); disk(st); technical(c) }
            }
        }
        .onAppear { model.watchProcess = true }      // the process numbers (memory, threads, load) need a fresh overview, other screens don't
        .onDisappear { model.watchProcess = false }
    }

    var process: some View {
        let o = model.overview, c = o["core"], p = o["process"], g = p["git"]
        let alive = model.alive
        let load = p["load"].array.map { String(format: "%.2f", $0.dbl) }
        let git = "\(g["branch"].str) @ \(g["commit"].str)"
        let mode = c["is_core"].bool ? String(format: L("ядро, pid %d"), c["pid"].int)
            : c["pid"].exists ? String(format: L("экран без ядра, pid %d · ядро pid %d"), c["this_pid"].int, c["pid"].int)
            : String(format: L("экран без ядра, pid %d"), c["this_pid"].int)
        return Block(L("Процесс"), lead: true, content: {
            HStack(spacing: Style.Space.m) {
                StatusDot(ok: alive)
                Text(alive ? L("Сена работает") : L("Сена выключена")).font(Style.Text.heading)
                TimelineView(.periodic(from: .now, by: 1)) { ctx in   // the uptime runs on its own: an idle overview isn't republished
                    Text(alive ? String(format: L("с %@ · %@"), Fmt.whenFull(c["started"]), Fmt.dur(ctx.date.timeIntervalSince1970 - c["started"].dbl)) : "")
                        .font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(1)
                }
            }
            HStack(spacing: Style.Space.m) {
                Stat(value: Fmt.bytes(p["rss"].dbl), label: L("память процесса, пик"))
                Stat(value: "\(p["threads"].int)", label: L("потоков"))
                Stat(value: load.first ?? "—", label: L("нагрузка Mac, 1 мин"))
            }
            KV(rows: [(L("Режим"), mode),
                      (L("Нагрузка 1 / 5 / 15 мин"), load.isEmpty ? "—" : load.joined(separator: " / ")),
                      ("Python", p["python"].str), (L("Операционная система"), p["platform"].str),
                      ("Git", g["dirty"].bool ? String(format: L("%@ · есть несохранённые изменения"), git) : git),
                      (L("Последний коммит"), g["last"].str.isEmpty ? "—" : g["last"].str)])
        }, trailing: {
            Button(L("Перезапустить")) { model.restartAsking() }.disabled(!alive)   // turning on and off is only in the block at the bottom of the menu
        })
    }

    func connections(_ c: JSON) -> some View {
        let a = model.overview["accounts"]
        let required = Set(c["required"].array.map { $0.str })
        let keys = a["keys"].dict.keys.sorted()
        let columns = [GridItem(.adaptive(minimum: Style.Size.field), spacing: Style.Space.m)]
        return Block(L("Подключения")) {
            LazyVGrid(columns: columns, alignment: .leading, spacing: Style.Space.m) {
                SystemLight(title: L("Телеграм-бот"), on: a["telegram_bot"].bool, text: a["telegram_bot"].bool ? L("ключ есть") : L("нет ключа"))
                SystemLight(title: L("Аккаунт в Телеграме"), on: a["telegram_account"].bool, text: a["telegram_account"].bool ? L("вход есть") : L("не подключён"))
                SystemLight(title: "Google", on: a["google"].str == "ok", text: a["google"].str == "ok" ? L("вход есть") : a["google"].str == "expired" ? L("нужен повторный вход") : L("нет входа"))
                SystemLight(title: L("Браузер"), on: a["browser"].bool, text: a["browser"].bool ? L("открыт") : L("закрыт"))
            }
            LazyVGrid(columns: columns, alignment: .leading, spacing: Style.Space.m) {
                ForEach(keys, id: \.self) { k in
                    let set = a["keys"][k].bool, must = required.contains(k)
                    SystemLight(title: k, on: set, text: set ? L("задан") : must ? L("нет, обязательный") : L("нет"), mono: true, bad: !set && must)
                }
            }
        }
    }

    /// Maintenance. Paid and irreversible actions ask for confirmation; a second tap while a request is in flight does nothing (model.run, key).
    /// "Recalculate costs" lives in the costs header, "Restart" in the "Process" block: no action has two places.
    /// Fingerprints are counted where the server says (config models.embed_place): at home free, a paid call only if the role was moved to the cloud
    func maintenance(_ c: JSON) -> some View {
        let pending = model.overview["index"]["pending"].int
        let cloud = c["models"]["embed_place"].str == "cloud", embedModel = c["models"]["embed"].str
        return Block(L("Обслуживание")) {
            VStack(spacing: Style.Space.s) {
                MaintenanceRow(L("Резервная копия"), L("память, навыки, люди, правила, задачи, переписка, журнал")) { run(L("Резервная копия"), "backup") }
                MaintenanceRow(L("Пересобрать индекс поиска"), L("добавить в индекс всё пропущенное")) { run(L("Пересборка индекса"), "sync_index") }
                MaintenanceRow(L("Обновить заметки"), L("выгрузить людей, память, навыки и правила в заметки Obsidian")) { run(L("Обновление заметок"), "sync_vault") }
                MaintenanceRow(L("Проиндексировать новое"), String(format: cloud ? L("не проиндексировано: %d · платно, облачная модель") : L("не проиндексировано: %d · дома, бесплатно"), pending),
                               enabled: pending > 0) {
                    if !cloud || confirm(L("Проиндексировать новое?"), String(format: L("Записей без отпечатка: %d. Каждая — платный запрос к модели %@."), pending, embedModel), button: L("Проиндексировать")) {
                        run(L("Индексация"), "embed")
                    }
                }
                MaintenanceRow(L("Записать журнал на диск"), L("дозаписать журнал событий и индекс поиска в основные файлы")) { run(L("Запись журнала"), "checkpoint") }
            }
        }
    }

    func disk(_ st: JSON) -> some View {
        let rows = st["disk"].dict.map { ($0.key, $0.value.dbl) }.sorted { $0.1 > $1.1 }
        let total = rows.reduce(0) { $0 + $1.1 }, top = rows.first?.1 ?? 0
        return Block(L("Место на диске"), content: {
            if rows.isEmpty { Text("—").font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText) }
            ForEach(rows, id: \.0) { name, size in
                HStack(spacing: Style.Space.l) {
                    Text(DISK_RU[name] ?? name).font(Style.Text.small).lineLimit(1).frame(width: DISK_NAME, alignment: .leading)
                    Bar(value: top > 0 ? size / top : 0)
                    Text(Fmt.bytes(size)).font(Style.Text.small.monospacedDigit()).frame(width: DISK_SIZE, alignment: .trailing)
                }
                .accessibilityElement(children: .combine)
            }
        }, trailing: { Text(Fmt.bytes(total)).font(Style.Text.small.monospacedDigit()).foregroundStyle(Style.Colors.secondaryText) })
    }

    func technical(_ c: JSON) -> some View {
        let m = c["models"], sch = c["scheduler"], mem = c["memory"]
        return Block(L("Техническое"), content: {
            if tech {
                VStack(alignment: .leading, spacing: Style.Space.l) {
                    group(L("Модели"), [(L("Сильная"), m["main"].str), (L("Быстрая"), m["fast"].str), (L("Голосовые команды"), m["voice"].str),
                                     (L("Речь → текст"), m["transcribe"].str), (L("Текст → речь"), m["tts"].str),
                                     (L("Индексация"), "\(m["embed"].str) · \(m["embed_dims"].int) · " + (m["embed_place"].str == "cloud" ? L("в облаке") : L("дома"))),
                                     (L("Тарифы на"), c["prices_as_of"].str), ("Claude Code", L("подписка (claude -p)"))])
                    group(L("Пути"), c["paths"].dict.keys.sorted().map { ($0, c["paths"][$0].str) })
                    group(L("Константы"), [(L("Порт"), "\(c["app_port"].int)"), (L("Попыток фонового дела"), "\(sch["max_attempts"].int)"), (L("Сообщений в контекст"), "\(mem["history_recent"].int)"),
                                        (L("Максимум переписки"), "\(mem["history_max"].int)"), (L("Максимум журнала задач"), "\(mem["tasks_max"].int)"),
                                        (L("Размышление раз в"), String(format: L("%d сообщений"), mem["reflect_every"].int)), (L("Копии хранятся"), String(format: L("%d дн"), mem["backup_days"].int)),
                                        (L("Браузер без окна"), c["headless"].bool ? L("да") : L("нет")), (L("Владелец в .env"), c["owner_id"].bool ? L("задан") : L("нет"))])
                }
                .transition(Style.Motion.appear)
            } else {
                Text(String(format: L("модели %@ и %@ · порт %d · тарифы на %@"), m["main"].str, m["fast"].str, c["app_port"].int, c["prices_as_of"].str))
                    .font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText).lineLimit(1)
            }
        }, trailing: { Button(tech ? L("Скрыть") : L("Показать")) { tech.toggle() } })
        .motion(value: tech)
    }
    func group(_ title: String, _ rows: [(String, String)]) -> some View {
        VStack(alignment: .leading, spacing: Style.Space.s) { Text(title).font(Style.Text.small.weight(.semibold)); KV(rows: rows) }
    }
    func run(_ what: String, _ action: String) { model.run(what, key: action, { try await self.model.backend.act(action) }) }
}

// ---- Self-development ----

struct ChangeRow: Identifiable { let id: String; let json: JSON }

/// A change: a card — status icon, task, test and status tags, when and which branch. Whether it can be applied at all and what to
/// look at first come from the server (ag_code.verdict): refused — it touched git, executable settings or Sena's own data;
/// notes — tests changed, checks loosened, many lines deleted… (the same lines the Telegram report spells out)
struct ChangeLine: View {
    let change: JSON
    let pending: Bool
    let open: () -> Void
    var tint: Color {
        if change["applied"].exists { return pending ? Style.Colors.warn : Style.Colors.ok }
        if change["refused"].string != nil { return Style.Colors.bad }
        return change["tests"].bool ? (change["notes"].array.isEmpty ? Style.Colors.info : Style.Colors.warn) : Style.Colors.bad
    }
    /// Can be applied: tests passed, nothing in it refused it, not applied yet
    static func applicable(_ ch: JSON) -> Bool { ch["tests"].bool && ch["refused"].string == nil && !ch["applied"].exists }
    var body: some View {
        RecordCard(icon: "arrow.triangle.branch", tint: tint, title: change["task"].str, lines: 2, action: open, meta: {
            ChangeLine.tests(change)
            ChangeLine.state(change, pending: pending)
            if !change["notes"].array.isEmpty && change["refused"].string == nil && !change["applied"].exists {
                Tag(text: String(format: L("посмотри: %d"), change["notes"].array.count), color: Style.Colors.warn).help(change["notes"].array.map { $0.str }.joined(separator: "\n"))
            }
            Text("\(Fmt.when(change["created"])) · \(change["branch"].str)").font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(1)
        })
    }
    static func tests(_ ch: JSON) -> Tag {
        ch["tests"].exists ? (ch["tests"].bool ? Tag(text: L("тесты прошли"), color: Style.Colors.ok) : Tag(text: L("тесты упали"), color: Style.Colors.bad)) : Tag(text: L("без тестов"))
    }
    static func state(_ ch: JSON, pending: Bool) -> Tag {
        if ch["applied"].exists { return pending ? Tag(text: L("приживается"), color: Style.Colors.warn) : Tag(text: L("применена"), color: Style.Colors.ok) }
        if !ch["tests"].bool { return Tag(text: L("нельзя применять"), color: Style.Colors.bad) }
        return ch["refused"].string != nil ? Tag(text: L("не принята"), color: Style.Colors.bad) : Tag(text: L("готова"), color: Style.Colors.info)
    }
}

struct ChangesView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var still
    @State private var r = JSON(nil)
    @State private var loaded = false
    @State private var failure: String? = nil   // the last load failed
    @State private var detail: ChangeRow? = nil
    var rows: [ChangeRow] { r["changes"].array.map { ChangeRow(id: $0["id"].str, json: $0) } }
    /// The update_pending file: "previous commit, change id, new commit" — waiting to see if it sticks
    var pendingId: String? { r["pending"].string.map { let p = $0.split(separator: " "); return p.count > 1 ? String(p[1]) : String(p.first ?? "") } }

    var body: some View {
        Group {
            if loaded { content }
            else if let e = failure { RetryNote(text: e, retry: reload) }
            else { Loading() }
        }
        .sheet(item: $detail) { ch in ChangeDetail(change: ch.json, pending: pendingId == ch.id, reload: load) }
        .task { while !Task.isCancelled { await load(); try? await Task.sleep(nanoseconds: 10_000_000_000) } }
        .actions { Button(L("Обновить"), action: reload) }
    }

    var content: some View {
        let list = rows, pid = pendingId
        let changes = list.map(\.json)
        let dirtyFiles = r["dirty"].str.split(separator: "\n").count
        let ready = changes.filter(ChangeLine.applicable).count
        let applied = changes.filter { $0["applied"].exists }.count
        let failed = changes.filter { $0["tests"].exists && !$0["tests"].bool }.count
        let branches = r["branches"].array.map { $0.str }
        return ScrollView {
            VStack(spacing: Style.Space.xl) {
                Block(L("Состояние кода"), lead: true) {
                    HStack(spacing: Style.Space.m) {
                        Stat(value: "\(list.count)", label: L("правок"))
                        Stat(value: "\(ready)", label: L("готовы к применению"))
                        Stat(value: "\(applied)", label: L("применено"))
                        Stat(value: "\(failed)", label: L("упали на тестах"), bad: failed > 0)
                    }
                    KV(rows: [("HEAD", r["head"].str.isEmpty ? "—" : r["head"].str), (L("Ветка"), model.overview["process"]["git"]["branch"].str),
                              (L("Несохранённые изменения"), dirtyFiles == 0 ? L("нет") : "\(dirtyFiles) \(Fmt.plural(dirtyFiles, "файл", "файла", "файлов"))"),
                              (L("Ветки правок"), branches.isEmpty ? L("нет") : branches.joined(separator: ", ")),
                              (L("Ждёт подтверждения"), pid ?? L("нет")), (L("Последняя рабочая версия"), r["good"].string.map { String($0.prefix(7)) } ?? "—")])
                    if dirtyFiles > 0 {
                        Label(L("В рабочем коде есть несохранённые изменения: сначала сделай коммит, иначе правки применить нельзя"), systemImage: "exclamationmark.triangle")
                            .symbolEffect(.pulse, options: .repeating, isActive: !still)
                            .font(Style.Text.caption).foregroundStyle(Style.Colors.warnText)
                    }
                    if let e = failure { Text(String(format: L("Не удалось обновить: %@"), e)).font(Style.Text.caption).foregroundStyle(Style.Colors.badText) }
                }
                Block(L("Правки")) {
                    if list.isEmpty {
                        Empty(title: L("Правок своего кода ещё не было"), icon: "arrow.triangle.branch")
                            .frame(maxWidth: .infinity).padding(.vertical, Style.Space.huge)
                    } else {
                        VStack(spacing: Style.Space.s) {
                            ForEach(list) { ch in
                                ChangeLine(change: ch.json, pending: pid == ch.id) { detail = ch }
                                    .transition(Style.Motion.appear)
                            }
                        }
                        .motion(value: list.count)
                    }
                }
            }
        }
    }

    func reload() { Task { await load() } }
    func load() async {
        do { r = try await model.backend.get("changes"); loaded = true; failure = nil }
        catch { if !Task.isCancelled { failure = error.localizedDescription } }
    }
}

struct ChangeDetail: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let change: JSON
    let pending: Bool
    let reload: () async -> Void
    var id: String { change["id"].str }
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.xl) {
            HStack(spacing: Style.Space.m) {
                Text(String(format: L("Правка %@"), id)).font(Style.Text.title)
                ChangeLine.tests(change)
                ChangeLine.state(change, pending: pending)
                Spacer()
                Button(L("Закрыть")) { dismiss() }.keyboardShortcut(.cancelAction)
            }
            KV(rows: [(L("Задача"), change["task"].str), (L("Ветка"), change["branch"].str), (L("От коммита"), String(change["base"].str.prefix(7))),
                      (L("Создана"), Fmt.whenFull(change["created"])),
                      (L("Применена"), change["applied"].exists ? "\(Fmt.whenFull(change["applied"])) · \(String(change["commit"].str.prefix(7)))" : L("нет"))])
            ScrollView {
                VStack(alignment: .leading, spacing: Style.Space.xl) {
                    if let why = change["refused"].string, change["tests"].bool {   // can never be applied: what it touched (failed tests are said below)
                        Block(L("Не принята")) { Text(why).font(Style.Text.small).foregroundStyle(Style.Colors.badText).textSelection(.enabled) }
                    }
                    if !change["notes"].array.isEmpty {   // what to look at before "apply": tests changed, checks loosened, much deleted…
                        Block(L("Посмотри до применения")) {
                            ForEach(Array(change["notes"].array.enumerated()), id: \.offset) { _, n in
                                Text(n.str).font(Style.Text.small).foregroundStyle(Style.Colors.warnText).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                    }
                    Block(L("Отчёт")) { Text(change["report"].str.isEmpty ? "—" : change["report"].str).font(Style.Text.small).textSelection(.enabled) }
                    if !change["tests_tail"].str.isEmpty { Block(L("Тесты")) { Text(change["tests_tail"].str).font(Style.Text.monoSmall).textSelection(.enabled) } }
                    Block(L("Изменения")) { Text(change["diff"].str.isEmpty ? "—" : change["diff"].str).font(Style.Text.monoSmall).textSelection(.enabled) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            if !change["applied"].exists {
                VStack(alignment: .leading, spacing: Style.Space.m) {
                    if !change["tests"].bool {
                        Text(change["tests"].exists ? L("не прошла тесты — применять нельзя") : L("тесты не запускались — применять нельзя"))
                            .font(Style.Text.caption).foregroundStyle(Style.Colors.badText)
                    }
                    HStack(spacing: Style.Space.m) {
                        Button(L("Применить и перезапустить"), action: apply).buttonStyle(.primary).disabled(!ChangeLine.applicable(change))
                        Button(L("Отклонить"), role: .destructive, action: drop)
                        Spacer()
                    }
                }
            }
        }
        .sheetStyle()
        .padding(Style.Space.xxl).frame(width: Style.Size.sheet, height: DETAIL_SHEET_HEIGHT)
    }
    // The sheet closes only on success: when the server refuses ("other tasks running", "there are unsaved changes") it replies with an error, and that stays visible
    func apply() {
        guard confirm(String(format: L("Применить правку %@?"), id), L("Ветка вольётся в рабочий код, Сена перезапустится. Если не поднимется за несколько минут, откатится сама."), button: L("Применить"), destructive: true) else { return }
        model.run(L("Применение"), key: "change-\(id)", { try await self.model.backend.act("change_apply", ["id": id]) }, then: { dismiss(); Task { await reload() } })
    }
    func drop() {
        guard confirm(String(format: L("Отклонить правку %@?"), id), L("Ветка правки удалится, вернуть её не получится."), button: L("Отклонить"), destructive: true) else { return }
        model.run(L("Отклонение"), key: "change-\(id)", { try await self.model.backend.act("change_drop", ["id": id]) }, then: { dismiss(); Task { await reload() } })
    }
}
