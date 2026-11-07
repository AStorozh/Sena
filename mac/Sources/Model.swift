// App state: an overview from the server every couple of seconds, turning Sena on and off, actions, notifications.
import Cocoa
import SwiftUI
import UserNotifications

enum Screen: String, CaseIterable, Identifiable {
    case chat, tasks, rules, panels, memory, secrets, integrations, analytics, settings   // this is also the order they appear in the menu (Screen.allCases)
    var id: String { rawValue }
    var title: String {
        switch self {
        case .chat: return L("Чат"); case .tasks: return L("Задачи"); case .rules: return L("Правила"); case .panels: return L("Сена ведёт"); case .memory: return L("Память")
        case .secrets: return L("Секреты"); case .integrations: return L("Интеграции"); case .analytics: return L("Журнал"); case .settings: return L("Настройки")
        }
    }
    var icon: String {   // thin outline SF Symbols icons
        switch self {
        case .chat: return "bubble.left.and.bubble.right"; case .tasks: return "list.clipboard"; case .rules: return "text.badge.checkmark"; case .panels: return "target"
        case .memory: return "archivebox"; case .secrets: return "key"; case .integrations: return "point.3.filled.connected.trianglepath.dotted"
        case .analytics: return "creditcard"; case .settings: return "gearshape"
        }
    }
    /// Tabs: a row of pills above a section's content (not in the menu). Order matters: screens read model.tab(.section)
    var tabs: [String] {
        switch self {
        case .chat: return [L("Переписка"), L("Файлы")]   // files are the conversation's attachments
        case .tasks: return [L("Список"), L("Схема")]
        case .memory: return [L("Факты"), L("Люди"), L("Заметки"), L("Навыки")]
        case .settings: return [L("Поведение"), L("Система"), L("Саморазвитие")]
        case .analytics: return [L("Расходы"), L("События"), L("Лог")]
        default: return []
        }
    }
}

@MainActor
final class AppModel: ObservableObject {
    @Published var section: Screen = .chat {
        didSet {
            let a = Screen.allCases.firstIndex(of: oldValue) ?? 0, b = Screen.allCases.firstIndex(of: section) ?? 0
            slide = b == a ? 0 : (b > a ? 1 : -1)
            openedAt = Date()
        }
    }
    /// Which way was "flipped": 1 — forward (the section or tab is further right in the list), −1 — back, 0 — no flip. New blocks slide in from the matching side based on this
    @Published private(set) var slide: CGFloat = 0
    @Published var openedAt = Date()   // when the section, tab, or the whole app opened: blocks cascade in starting from this moment
    @Published private(set) var narrow = false   // the window is past the threshold: the layout shrinks. Only "narrow or not" itself is published, not every pixel of width
    func setWidth(_ w: CGFloat) { let n = w < Style.Size.compactWindow; if n != narrow { narrow = n } }
    @Published var tabs: [Screen: Int] = [:] {
        didSet {
            let a = oldValue[section] ?? 0, b = tab(section)
            slide = b == a ? 0 : (b > a ? 1 : -1)
            openedAt = Date()
        }
    }   // the selected tab for every section
    func tab(_ s: Screen) -> Int { tabs[s] ?? 0 }
    /// Open a section (with the given tab). false — didn't open: Tema declined to leave an unsaved edit
    @discardableResult func open(_ s: Screen) -> Bool { guard leaveOK(to: s, tab: nil) else { return false }; section = s; return true }
    @discardableResult func open(_ s: Screen, tab: Int) -> Bool { guard leaveOK(to: s, tab: tab) else { return false }; tabs[s] = tab; section = s; return true }
    /// Switch the current section's tab (the pills in the header)
    func switchTab(_ s: Screen, to tab: Int) { guard leaveOK(to: s, tab: tab) else { return }; tabs[s] = tab }

    // Unsaved edits: the screen gets rebuilt from scratch on a section or tab change, and a draft would be lost. Screens with an edit (memory, a skill, settings…)
    // report it through setUnsaved, and a navigation that would lose it asks first
    private var unsaved: Set<String> = []
    func setUnsaved(_ key: String, _ on: Bool) { if on { unsaved.insert(key) } else { unsaved.remove(key) } }
    private func leaveOK(to s: Screen, tab: Int?) -> Bool {
        guard !unsaved.isEmpty, s != section || (tab != nil && tab != self.tab(s)) else { return true }
        return discardUnsaved()
    }
    /// Ask whether it's OK to lose unsaved edits (navigating away, closing the window, quitting): true — OK, or there's nothing to lose
    func discardUnsaved() -> Bool {
        guard !unsaved.isEmpty else { return true }
        guard confirm(L("Есть несохранённые правки"), L("Если уйти, они пропадут."), button: L("Уйти без сохранения"), destructive: true) else { return false }
        unsaved.removeAll()
        return true
    }
    var chatTarget: String? = nil   // who the next chat message is addressed to ("Assign in chat" from an agent card): chat reads it and clears it
    var chatDraft = ""   // what's typed in the chat field: the screen gets rebuilt on a section change, and the typed text shouldn't be lost
    // the data period shared across the app (costs, journal): from … to …, Tema picks the dates himself — there are no ready-made "today / week / month" presets
    @Published var since: Date = Calendar.current.dateInterval(of: .month, for: Date())?.start ?? Date()
    @Published var until: Date = Calendar.current.startOfDay(for: Date()) { didSet { untilIsToday = Calendar.current.isDateInToday(until) } }
    private var untilIsToday = true   // "to" is today: the period follows the day instead of getting stuck on yesterday (Tema might have had the app open for days)
    var period: String { "\(Fmt.dayKey.string(from: since))..\(Fmt.dayKey.string(from: until))" }   // the way the server understands it (costs.span): both dates inclusive
    var singleDay: Bool { Calendar.current.isDate(since, inSameDayAs: until) }
    var today: Bool { singleDay && Calendar.current.isDateInToday(until) }
    var days: Int {   // days in the period, including both ends
        let cal = Calendar.current
        return max(1, (cal.dateComponents([.day], from: cal.startOfDay(for: since), to: cal.startOfDay(for: until)).day ?? 0) + 1)
    }
    /// A new day has started: "to" (was today) and a single-day "from" roll forward to today, "start of the month" rolls to the start of the new month.
    /// Called both at midnight and after sleep (the overview poll checks this every time: it's cheap)
    func rollDay() {
        let cal = Calendar.current, today = cal.startOfDay(for: Date())
        guard untilIsToday, until < today else { return }
        let monthStart = { (d: Date) in cal.dateInterval(of: .month, for: d)?.start ?? d }
        if cal.isDate(since, inSameDayAs: until) { since = today }
        else if cal.isDate(since, inSameDayAs: monthStart(until)) { since = monthStart(today) }
        until = today
    }
    @Published var overview = JSON(nil)
    @Published var up = false { didSet { if up && !oldValue { openedAt = Date() } } }   // the server is responding; once it answers, the app opens as a cascade
    @Published var status = L("Открываю…") // what to show while the server isn't up
    @Published var accessProblem: String? = nil
    private(set) var accessNeedsKey = false   // what's missing: the APP_TOKEN key in .env (as opposed to folder access)
    @Published var handover = ""              // "Starting Sena…" — the server disappears for a few seconds, that's expected
    @Published private(set) var coreStarting = false   // the core is coming up (started from the phone, Telegram or run.sh): also expected
    @Published var toast: String? = nil
    private(set) var toastFailed = false   // an error notification: a different badge
    @Published var selectedTask: String? = nil
    @Published var pendingAttachment: JSON? = nil   // "Send to Sena" from Files
    /// What a found search result points at — a person's id, a note's or a file's path, a skill, a panel, a message: the screen it
    /// opens selects it and clears this (take)
    @Published var focus: String? = nil
    func take() -> String? { guard let f = focus else { return nil }; focus = nil; return f }

    let backend = Backend.shared
    private var failures = 0, lastUp: Double = 0
    private var handoverUntil = Date.distantPast   // the server is about to go down and come back on its own before this moment (start, stop, restart): we wait for it to disappear and return
    private var handoverSeenDown = false             // the server has actually disappeared during this time: from now on its replies belong to the new server
    private var handoverFrom = 0                     // the process that served the app when the switch began: a reply from another one is the new server
    private var busy: Set<String> = []             // actions already in flight: a second tap does nothing
    private var toastID = 0
    private var dayObserver: NSObjectProtocol?
    private var knownActive: [String: String] = [:]
    private var knownCore = 0
    private var overviewSignature = NSDictionary()
    private var notifiedQuestion = ""
    private var loop: Task<Void, Never>?

    static weak var current: AppModel?   // for the app delegate (⌘Q asks about unsaved edits)
    init() {
        AppModel.current = self
        dayObserver = NotificationCenter.default.addObserver(forName: .NSCalendarDayChanged, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.rollDay() }
        }
    }

    var screenKey: String { "\(section.rawValue)-\(tab(section))" }   // which screen is open (section and tab): the screen is rebuilt from scratch and fades based on this
    var waiting: Bool { !up || accessProblem != nil }                 // the server isn't up yet: a waiting splash instead of the section
    @Published private(set) var launchFailed = false   // the screen-without-core server didn't come up: the splash shows the launch log and an "Open log" button
    var alive: Bool { up && overview["core"]["alive"].bool }   // no connection — the overview is stale, its "running" would be a lie
    /// "3 tasks running" as one phrase: the status line in the menu and in the macOS menu bar
    var inWorkText: String { "\(ownTasks.count) \(Fmt.plural(ownTasks.count, "задача", "задачи", "задач")) " + L("в работе") }
    var active: [JSON] { overview["active"].array }
    /// The owner's own running tasks: without Sena's service ones (a task's review, the night, overheard speech, a news check) —
    /// "Stop all" leaves those running, so they are no counter, badge or "Stop" of his. A long work's turn ("work") is a service
    /// task too, but his "Stop all" stops it and pauses the work (tools.stop_all): it counts as his
    var ownTasks: [JSON] { active.filter { !$0["service"].bool || $0["work"].exists } }
    var question: JSON? { overview["question"].exists ? overview["question"] : nil }
    var pendingSends: [JSON] { overview["pending_sends"].array }
    var settings: JSON { overview["settings"] }
    /// The server is away on purpose — Sena is starting, stopping or restarting: the window says so instead of "no connection"
    var switching: Bool { !up && (!handover.isEmpty || coreStarting) }
    var lost: Bool { !up && !switching }   // the server is away and nobody asked for that
    /// "Starting Sena…": the first words of the server's own answer to the button, or the launch flag
    var switchingText: String { handover.isEmpty ? L("Запускаю Сену…") : (handover.split(separator: ":").first.map { String($0) } ?? handover) + "…" }
    var stateText: String { switching ? switchingText : !up ? L("Нет связи") : alive ? L("Сена работает") : L("Сена выключена") }   // no connection — the overview is stale, its "running" would be a lie
    /// The line under the state when she isn't working: switching, asleep, or not answering
    var offText: String { switching ? L("это займёт несколько секунд") : up ? L("бот, расписание и фон спят") : L("сервер не отвечает") }

    /// Launch (and "Retry"): finding the project folder and reading .env — not on the main thread, macOS may wait for an answer about the "Desktop" permission
    func start() {
        let backend = self.backend
        Task.detached {
            backend.reload()
            let problem = backend.problem
            await MainActor.run { self.begin(problem) }
        }
    }
    private func begin(_ problem: Backend.Problem?) {
        switch problem {
        case .folder?:
            accessNeedsKey = false
            accessProblem = String(format: L("Проект Сены лежит в %@. Разреши приложению папку «Рабочий стол»: Системные настройки → Конфиденциальность и безопасность → Файлы и папки → Сена, потом нажми «Повторить»."), backend.root.path)
            return
        case .token?:
            accessNeedsKey = true
            accessProblem = String(format: L("В файле .env проекта (%@) нет APP_TOKEN: без него приложение не может говорить с сервером Сены. Добавь строку APP_TOKEN=… и нажми «Повторить»."), backend.root.path)
            return
        case nil: break
        }
        accessProblem = nil
        loop?.cancel()
        loop = Task { [weak self] in
            var first = true, round = 0
            while !Task.isCancelled {
                await self?.poll(first: first)
                if round % 8 == 0 { await self?.pollPanels() }   // the panels' "needs you" every ~16 s: a failed check or a waiting work shows in the menu
                first = false; round += 1
                do { try await Task.sleep(nanoseconds: 2_000_000_000) } catch { return }
            }
        }
    }

    /// What the server's panels hold for the owner ("Sena keeps": what waits for a "yes", a failed check, a work waiting for him,
    /// lesson drafts, goals due…): the sum of their badges is the menu's counter on that section
    @Published private(set) var panelBadge = 0
    func pollPanels() async {
        guard up, let l = try? await backend.get("panels") else { return }
        let n = l["items"].array.reduce(0) { $0 + $1["badge"].int }
        if n != panelBadge { panelBadge = n }
    }

    @Published var costsRevision = 0   // increments after recalculating costs: the costs screen reloads right away
    var watchProcess = false   // the "System" screen is open: it needs the changing process numbers (memory, threads, load)
    /// An overview without the fields that change on every poll ("now"; process numbers — while nobody's watching them): used to decide whether to redraw the screen
    static func signature(_ o: JSON, withProcess: Bool = true) -> NSDictionary {
        var d = o.raw as? [String: Any] ?? [:]
        d["now"] = nil
        if !withProcess, var p = d["process"] as? [String: Any] { for k in ["rss", "threads", "load"] { p[k] = nil }; d["process"] = p }
        return NSDictionary(dictionary: d)
    }

    func poll(first: Bool = false) async {
        do {
            let o = try await backend.get("overview")
            // the old server still answers and is about to go down: wait for it, don't flash the window. A reply from another process is
            // the new server already — the core now comes up in a second, faster than a poll could see the old one gone (36 s of waiting)
            if Date() < handoverUntil && !handoverSeenDown && o["core"]["this_pid"].int == handoverFrom { return }
            // "now" and the process numbers change every time; without them the overview is often the same — then the screen doesn't redraw (the whole app used to flicker every 2 s)
            let sig = AppModel.signature(o, withProcess: watchProcess)
            if sig != overviewSignature || !active.isEmpty || !overview.exists { overview = o; overviewSignature = sig }
            if !up { up = true }   // @Published announces every write, even an identical one: the whole window would redraw every 2 s
            if !handover.isEmpty { handover = "" }
            if coreStarting { coreStarting = false }
            if launchFailed { launchFailed = false }
            failures = 0; lastUp = Date().timeIntervalSince1970; handoverSeenDown = false; handoverUntil = .distantPast
            rollDay()
            notice()
        } catch {
            if Task.isCancelled || (error as? URLError)?.code == .cancelled { return }   // the poll was canceled on purpose ("Retry", a new window): this isn't a connection failure
            failures += 1
            if let e = error as? APIError {   // the server is alive but replies with a refusal (wrong token, internal error): we don't put up a second screen over it
                if failures >= 2 { up = false; status = e.message == "нет доступа" ? L("Сервер не принял ключ приложения: проверь APP_TOKEN в .env, приложение подхватит правку само") : e.message }
                if e.message == "нет доступа" { await Task.detached { [backend] in backend.reload() }.value }   // the server replies in Russian, this isn't a translation choice; the token may have been fixed in .env
                return
            }
            if failures >= 2 || first { up = false }   // a single hiccup (a busy server, sleep) doesn't put up the loading splash
            if Date() < handoverUntil { handoverSeenDown = true }
            NSApp.dockTile.badgeLabel = nil
            let now = Date().timeIntervalSince1970
            let handingOver = Date() < handoverUntil.addingTimeInterval(10)   // "Start/Stop/Restart": run.sh or the core bring the server up themselves
            // coreStarting and lastLogLines read files in the project folder — not on the main thread (same reason as reload: the "Desktop" permission may be pending)
            let starting = await Task.detached { [backend] in backend.coreStarting }.value
            if coreStarting != starting { coreStarting = starting }
            if !handingOver && !handover.isEmpty { handover = "" }   // the switch took longer than it may: from now on it's a real loss of connection
            if starting { status = L("Запускаю Сену…") }
            else if handingOver { status = handover.isEmpty ? L("Подключаюсь…") : handover }
            else if first || (failures >= 2 && !backend.screenRunning) {
                if now - backend.screenStartedAt < 20 {
                    let tail = await Task.detached { [backend] in backend.lastLogLines() }.value
                    status = L("Экран не запустился. Лог запуска:\n") + tail; launchFailed = true
                }
                else { await Task.detached { [backend] in backend.startScreen() }.value; status = L("Открываю…"); launchFailed = false }   // the launch log is on disk: not on the main thread
            } else if backend.screenRunning { status = L("Открываю…") }
            else if now - lastUp < 15 { status = handover.isEmpty ? L("Подключаюсь…") : handover }
        }
    }

    // ---- notifications: a task finished, Sena is asking something; the number on the Dock icon ----
    private func notice() {
        let now = Dictionary(ownTasks.map { ($0["tid"].str, $0["text"].str) }, uniquingKeysWith: { _, last in last })
        let core = overview["core"]["pid"].int
        let restarted = knownCore != 0 && core != knownCore   // another core answers now: the tasks that vanished were cut off by the restart, not finished
        knownCore = core
        for (tid, text) in knownActive where alive && !restarted && now[tid] == nil && !tid.isEmpty {   // Sena was stopped — tasks weren't "finished" either
            Task { [backend] in
                guard let t = try? await backend.get("task", ["id": tid]), t["done"].bool else { return }   // only a task the log holds as finished
                guard AppModel.worthTelling(t) else { return }
                let answer = t["answer"].str.prefix(180)
                if !NSApp.isActive { AppModel.notify(title: String(format: L("Сена: %@"), String(text.prefix(60))), body: answer.isEmpty ? L("готово") : String(answer), id: "task-\(tid)") }
            }
        }
        knownActive = now
        let q = question?["question"].str ?? ""
        if !q.isEmpty && q != notifiedQuestion { notifiedQuestion = q; AppModel.notify(title: L("Сена спрашивает"), body: q, id: "question") }
        if q.isEmpty { notifiedQuestion = "" }
        NSApp.dockTile.badgeLabel = !q.isEmpty ? "?" : (ownTasks.isEmpty ? nil : "\(ownTasks.count)")
    }
    /// A finished task worth a notification with a sound: his own, not Sena's housekeeping (the night at 4 am, a review, a long
    /// work's turn, overheard speech — the log keeps them as service), and not a check that answered "OK": that answer is how
    /// a scheduled or a news check says "nothing to tell", and Telegram stays quiet for it on purpose (agent._background)
    static func worthTelling(_ t: JSON) -> Bool {
        guard !t["service"].bool else { return false }
        return !["OK", "ОК"].contains(t["answer"].str.trimmingCharacters(in: .whitespacesAndNewlines).uppercased())
    }
    static func notify(title: String, body: String, id: String) {
        let c = UNMutableNotificationContent(); c.title = title; c.body = body; c.sound = .default
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: id, content: c, trigger: nil))
    }

    // ---- actions ----
    /// A toast for a few seconds; each one has its own id, so two identical ones in a row ("Copied") don't cancel each other out
    func say(_ text: String, hold: Double = 3.5) {
        toastID += 1
        let id = toastID
        toastFailed = hold > Style.Motion.toastErrorHold   // errors (held longer) get the exclamation mark, everything else gets a checkmark
        toast = text
        Task { try? await Task.sleep(nanoseconds: UInt64(hold * 1_000_000_000)); if toastID == id { toast = nil } }
    }
    /// To the clipboard plus a short toast; one shared way to copy across the whole app (a file path, a command, a reply, log lines).
    /// secret — a password: marked concealed (clipboard managers and Universal Clipboard leave such an item alone) and taken off
    /// the clipboard after a minute, if nothing else was copied meanwhile
    func copy(_ text: String, toast: String = L("Скопировано"), secret: Bool = false) {
        let pb = NSPasteboard.general; pb.clearContents(); pb.setString(text, forType: .string)
        if secret {
            pb.setString("", forType: NSPasteboard.PasteboardType("org.nspasteboard.ConcealedType"))
            let mine = pb.changeCount
            Task { try? await Task.sleep(nanoseconds: 60_000_000_000); if NSPasteboard.general.changeCount == mine { NSPasteboard.general.clearContents() } }
        }
        say(toast)
    }
    /// A server action with a result toast. key — so the same action (starting Sena) can't be triggered a second time by a second tap while the first is in flight
    func run(_ what: String, key: String? = nil, _ body: @escaping () async throws -> JSON, then: (() -> Void)? = nil) {
        if let key = key { guard !busy.contains(key) else { return }; busy.insert(key) }
        Task {
            defer { if let key = key { busy.remove(key) } }
            do {
                let r = try await body()
                if r["handover"].bool {   // the server is about to go down and come back as a different one: the window doesn't flash, it waits
                    handover = r["result"].str; up = false; status = handover
                    handoverFrom = overview["core"]["this_pid"].int
                    handoverUntil = Date().addingTimeInterval(35); handoverSeenDown = false   // a stop first finishes or writes down the tasks: up to 30 s (desk STOP_HARD)
                }
                say(r["result"].string ?? String(format: L("%@: готово"), what))
                then?()
                if !r["handover"].bool { await poll() }
            } catch { say(String(format: L("%@: %@"), what, error.localizedDescription), hold: 7) }
        }
    }
    func coreStart() { run(L("Запуск"), key: "core", { try await self.backend.act("core_start") }) }
    func coreStop() { run(L("Остановка"), key: "core", { try await self.backend.act("core_stop") }) }
    /// Turn on directly, but when turning off — ask if tasks are running (turning off would cut them short)
    func toggleCore() {
        guard alive else { coreStart(); return }
        if !active.isEmpty && !confirm(L("Остановить Сену?"), String(format: L("Задач в работе: %d. Они будут прерваны."), active.count), button: L("Остановить"), destructive: true) { return }
        coreStop()
    }
    /// One of the server's settings (settings.json), from a switch: the overview is read again, so the switch shows what was saved
    func setSetting(_ key: String, _ value: Any) {
        run(L("Настройки"), key: "setting_" + key, { try await self.backend.act("settings", [key: value]) }, then: { Task { await self.poll() } })
    }
    func restart() { run(L("Перезапуск"), key: "core", { try await self.backend.act("restart") }) }
    func stopAll() { run(L("Стоп"), key: "stop_all", { try await self.backend.act("stop_all") }) }
    /// With confirmation: every place this can be triggered from (menu, mini panel, header, settings) asks the same way and cuts tasks off only on "yes"
    func restartAsking() {
        if confirm(L("Перезапустить Сену?"), L("Она выйдет и поднимется заново за десять секунд. Задачи в работе оборвутся."), button: L("Перезапустить"), destructive: true) { restart() }
    }
    func stopAllAsking() {
        if confirm(L("Остановить все задачи?"), String(format: L("Задач в работе: %d. Они будут прерваны."), ownTasks.count), button: L("Остановить все"), destructive: true) { stopAll() }
    }
    /// Open a task in the task list (from any screen: the "List" tab, not the previous one, say "Diagram")
    func openTask(_ id: String) { if open(.tasks, tab: 0) { selectedTask = id } }
    /// A running task's id for opening and stopping it: its own (tid), or the thread's number from a server that sends no tid
    static func taskID(_ t: JSON) -> String { t["tid"].string ?? t["id"].str }
    /// Stop one task; always with confirmation (wherever it's tapped from: the list, the diagram, the mini panel, the board).
    /// id — the task's own id (tid): a thread's number is reused, and while the dialog waits another task may start on that thread
    func stopTask(_ id: String, title: String) {
        guard confirm(L("Остановить задачу?"), String(title.prefix(120)), button: L("Остановить"), destructive: true) else { return }
        run(L("Остановить"), key: "stop-" + id, { try await self.backend.act("stop_task", ["id": id]) })
    }
    /// The owner's «Да» / «Отмена» on anything Sena prepared (a message, a mail, a site edit, an errand, a secret shown): key — a double click won't do it twice
    func confirmSend(_ id: String) { run(L("Подтверждение"), key: "send-" + id, { try await self.backend.act("confirm_send", ["id": id]) }) }
    func cancelSend(_ id: String) { run(L("Отмена"), key: "send-" + id, { try await self.backend.act("cancel_send", ["id": id]) }) }
    func openPrivacy() { NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders")!) }
    func openLog() { NSWorkspace.shared.open(backend.launchLog) }
}

// ---- formatting ----
enum Fmt {
    /// Month and weekday names follow the system's language; the digits in dates don't depend on language by themselves
    static let dateLocale = Locale(identifier: isEnglish ? "en_US" : "ru_RU")
    static let hm: DateFormatter = { let f = DateFormatter(); f.locale = dateLocale; f.dateFormat = "HH:mm"; return f }()
    static let hms: DateFormatter = { let f = DateFormatter(); f.locale = dateLocale; f.dateFormat = "HH:mm:ss"; return f }()
    static let dt: DateFormatter = { let f = DateFormatter(); f.locale = dateLocale; f.dateFormat = "d MMM, HH:mm"; return f }()
    static let full: DateFormatter = { let f = DateFormatter(); f.locale = dateLocale; f.dateFormat = "d MMMM yyyy, HH:mm:ss"; return f }()
    private static let iso = ISO8601DateFormatter()   // one shared instance: creating it is expensive, and dates get parsed for every row of a list
    private static var parsed: [String: Date] = [:]   // parsed dates keyed by string: chat, tasks and the journal parse the same hundreds over and over
    static func date(_ j: JSON) -> Date? {
        if let s = j.string {
            if let d = parsed[s] { return d }
            guard let d = iso.date(from: s) ?? isoLocal.date(from: s) else { return nil }
            if parsed.count > 4000 { parsed.removeAll(keepingCapacity: true) }
            parsed[s] = d
            return d
        }
        if let t = j.double, t > 0 { return Date(timeIntervalSince1970: t) }
        return nil
    }
    /// Formats used to exchange data with the server don't depend on language, calendar, or the system's 12-hour time
    private static func machine(_ format: String) -> DateFormatter {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.calendar = Calendar(identifier: .gregorian); f.dateFormat = format; return f
    }
    static let isoLocal = machine("yyyy-MM-dd'T'HH:mm:ss")
    static let dayKey = machine("yyyy-MM-dd")   // the server's day key and the request's period
    /// Weekday name from the server's number (0 — Monday … 6 — Sunday), in the system's language
    static func weekday(_ i: Int) -> String {
        let f = DateFormatter(); f.locale = dateLocale
        let names = f.standaloneWeekdaySymbols ?? []
        return names.count == 7 ? names[(i + 1) % 7] : ""
    }
    static let dayName: DateFormatter = { let f = DateFormatter(); f.locale = dateLocale; f.dateFormat = "d MMMM"; return f }()
    static let dayNameYear: DateFormatter = { let f = DateFormatter(); f.locale = dateLocale; f.dateFormat = "d MMMM yyyy"; return f }()
    static func time(_ j: JSON) -> String { date(j).map(hm.string) ?? "—" }
    static func times(_ j: JSON) -> String { date(j).map(hms.string) ?? "—" }
    static func when(_ j: JSON) -> String { date(j).map(dt.string) ?? "—" }
    static func whenFull(_ j: JSON) -> String { date(j).map(full.string) ?? "—" }
    /// A day key for grouping rows in a list (a neighbor on the same day skips repeating the header). Shared by chat, tasks, files
    static func dayGroup(_ j: JSON) -> String { date(j).map { Calendar.current.startOfDay(for: $0).description } ?? "" }
    /// A day header above a group: "Today", "Yesterday", otherwise "d MMMM" (the year only if the date isn't from this year)
    static func dayTitle(_ j: JSON) -> String {
        guard let d = date(j) else { return "" }
        if Calendar.current.isDateInToday(d) { return L("Сегодня") }
        if Calendar.current.isDateInYesterday(d) { return L("Вчера") }
        return Calendar.current.isDate(d, equalTo: Date(), toGranularity: .year) ? dayName.string(from: d) : dayNameYear.string(from: d)
    }
    /// Time units: second/minute/hour/day — "с/мин/ч/дн" in Russian, "s/min/h/d" in English
    private static let sU = isEnglish ? "s" : "с", minU = isEnglish ? "min" : "мин", hU = isEnglish ? "h" : "ч", dU = isEnglish ? "d" : "дн"
    static func dur(_ s: Double) -> String {
        let s = max(0, s)
        if s < 60 { return s < 10 ? String(format: "%.1f \(sU)", s) : "\(Int(s)) \(sU)" }
        if s < 3600 { return "\(Int(s / 60)) \(minU)" }
        if s < 86400 { return String(format: "%.1f \(hU)", s / 3600) }
        return "\(Int(s / 86400)) \(dU) \(Int(s.truncatingRemainder(dividingBy: 86400) / 3600)) \(hU)"
    }
    static func ago(_ t: Double) -> String {
        let s = max(0, Date().timeIntervalSince1970 - t)
        if s < 60 { return L("только что") }
        if isEnglish {
            if s < 3600 { return "\(Int(s / 60)) \(minU) ago" }
            if s < 86400 { return "\(Int(s / 3600)) \(hU) ago" }
            return "\(Int(s / 86400)) \(dU) ago"
        }
        if s < 3600 { return "\(Int(s / 60)) мин назад" }
        if s < 86400 { return "\(Int(s / 3600)) ч назад" }
        return "\(Int(s / 86400)) дн назад"
    }
    /// "5 calls": a number and the word in the right form (there used to be six copies of this across the costs screens)
    static func calls(_ n: Int) -> String { "\(n) \(plural(n, "вызов", "вызова", "вызовов"))" }
    /// A percentage share, one shared look across the app: "12%"
    static func percent(_ x: Double) -> String { String(format: "%.0f %%", x * 100) }
    static func usd(_ x: Double) -> String { x > 0 && x < 0.01 ? String(format: "$%.4f", x) : String(format: "$%.2f", x) }
    static func bytes(_ n: Double) -> String {
        var n = n; let u = isEnglish ? ["B", "KB", "MB", "GB"] : ["Б", "КБ", "МБ", "ГБ"]; var i = 0
        while n >= 1024 && i < u.count - 1 { n /= 1024; i += 1 }
        return i == 0 ? "\(Int(n)) \(u[i])" : String(format: "%.1f %@", n, u[i])
    }
    /// Plural form of a word: three forms in Russian (one, two, five), in English — the translation of the "one" form,
    /// plural uses a plain "s", and after a consonant before "y" — the "y" changes to "ies" (entry → entries)
    static func plural(_ n: Int, _ one: String, _ few: String, _ many: String) -> String {
        if isEnglish {
            let w = L(one)
            if n == 1 { return w }
            if w.hasSuffix("y"), let before = w.dropLast().last, !"aeiouAEIOU".contains(before) { return w.dropLast() + "ies" }
            return w + "s"
        }
        if n % 10 == 1 && n % 100 != 11 { return one }
        if [2, 3, 4].contains(n % 10) && ![12, 13, 14].contains(n % 100) { return few }
        return many
    }
}

// ---- translation: Russian text in the code is the default wording, Localizable.strings swaps it for English ----

/// Is the system set to English right now (rather than the default Russian)? Decided by the language in Mac System Settings
let isEnglish = Bundle.main.preferredLocalizations.first == "en"
/// Interface text in the system's language: in Russian — the word as written in the code (no translation needed, it already is the source text),
/// in English — whatever is found in en.lproj/Localizable.strings; if not found, it shows the Russian as is
func L(_ s: String) -> String { NSLocalizedString(s, bundle: .main, comment: "") }

let AGENT_RU = ["ag_sena": L("Сена"), "ag_web": L("Браузер"), "ag_mac": L("Компьютер"), "ag_code": L("Код"), "ag_judge": L("Разбор"), "ag_night": L("Уборка"), "ag_ears": L("Слух")]
let VERDICT_RU = ["done": L("сделано"), "done_with_remarks": L("с замечаниями"), "failed": L("не вышло"), "unverified": L("не проверено"), "none": L("без оценки")]
/// Journal kinds (events.KINDS on the server, plus the few logged outside that list): the kind stays the key, this is only how it reads
let KIND_RU = ["start": L("старт"), "message_in": L("сообщение"), "message_out": L("ответ"), "file_out": L("файл"), "task_start": L("задача"), "task_end": L("конец задачи"), "step": L("шаг"),
               "model": L("модель"), "media": L("медиа"), "tool": L("инструмент"), "check": L("проверка"), "stop": L("стоп"), "error": L("ошибка"), "job": L("задание"), "heartbeat": L("фон"),
               "watch": L("слежка"), "verdict": L("оценка"), "rating": L("балл"), "comment": L("замечание"), "app": L("приложение"), "transcribe": L("речь→текст"), "speech": L("озвучка"),
               "embeddings": L("отпечатки"), "fetch": L("запрос"), "download": L("скачивание"), "sync": L("сверка"), "recipe": L("рецепт"), "heard": L("услышано"), "telegram_held": L("в Телеграм не ушло"), "voice": L("живой голос"),
               "person": L("человек"), "personality": L("характер"), "alert": L("сигнал"), "history": L("переписка"), "google": "google", "browser": L("браузер"), "cleanup": L("чистка"),
               "backup": L("копия"), "rule": L("правило"), "update": L("обновление"), "review": L("разбор"), "work": L("долгая работа"), "retry": L("повтор"), "effect": L("действие"),
               "mcp": "MCP", "secret": L("секрет"), "goal": L("цель"), "read": L("чтение"), "outside": L("чужой текст"), "lesson": L("урок"), "attention": L("внимание"),
               "code": L("код"), "exam": L("экзамен")]
/// An agent's name on screen: agents go by their own names — the codes ag_web, ag_mac, ag_code, ag_judge, ag_night, ag_ears; the coordinator is Sena
/// (in the system's language, like the app's own name). The Russian labels from AGENT_RU are roles, not names
func agentName(_ who: String) -> String { let code = agentCode(who); return code == "ag_sena" ? L("Сена") : (code.isEmpty ? "—" : code) }   // agent codes aren't translated
func agentRole(_ who: String) -> String { AGENT_RU[agentCode(who)] ?? "" }
