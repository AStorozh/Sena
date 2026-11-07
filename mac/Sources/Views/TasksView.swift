// Tasks: filter and rows on the left (running ones on top with a pulsing dot and a stop button, then finished ones by day),
// on the right the details of the selected one — summary tiles, the answer, step-by-step progress, verdict, remarks and task events.
// The filter and search for tasks are in the section's header; the "Diagram" tab is FlowView.
import SwiftUI

/// Column widths within rows and the minimum tile width: this is layout, not style
private enum TaskLayout {
    static let time: CGFloat = 64     // "12:34:56"
    static let when: CGFloat = 96     // "12 Oct, 12:34"
    static let agent: CGFloat = 88    // agent name in a step
    static let tile: CGFloat = 118    // a tile with a number: in a narrow column tiles wrap to a new row
}

// ---- "Tasks" tab ----

/// Filter and task rows on the left, details of the selected one on the right; the list refreshes every 3 s.
/// "Diagram" is switched above the content (ContentView), "Quality" has been removed from navigation
struct TasksView: View {
    @EnvironmentObject var model: AppModel
    @State private var data = JSON(nil)
    @State private var sig = ""                     // the server's signature of the list it sent: the next poll names it and gets "same" while nothing changed
    @State private var failed: String? = nil
    @State private var search = ""
    @State private var filter = "all"
    var query: String { search.trimmingCharacters(in: .whitespaces) }
    var filtering: Bool { filter != "all" || !query.isEmpty }

    /// Running tasks: under "all" his own (a long work's turn too: his "Stop all" stops it), under "service" Sena's service ones
    /// (the other filters are about finished ones); search also looks among them
    var active: [JSON] {
        guard filter == "all" || filter == "service" else { return [] }
        let all = data["active"].array.filter { ($0["service"].bool && !$0["work"].exists) == (filter == "service") }
        return query.isEmpty ? all : all.filter { taskTitle($0).localizedCaseInsensitiveContains(query) || $0["text"].str.localizedCaseInsensitiveContains(query) }
    }

    /// Finished tasks by filter and search; service ones (cleanup, triage) are visible only under their own filter
    var rows: [JSON] {
        let fin = data["finished"].array.filter { filter == "service" ? $0["service"].bool : !$0["service"].bool }
        var list = fin
        if filter == "remarks" { list = fin.filter { !$0["comments"].array.isEmpty || $0["outcome"]["verdict"].str == "done_with_remarks" } }
        if filter == "failed" { list = fin.filter { $0["outcome"]["verdict"].str == "failed" } }
        if !query.isEmpty { list = list.filter { taskTitle($0).localizedCaseInsensitiveContains(query) || $0["text"].str.localizedCaseInsensitiveContains(query) || $0["answer"].str.localizedCaseInsensitiveContains(query) } }
        return list
    }

    var body: some View {
        Columns(width: Style.Size.columnWide) {
            Block { list }.frame(maxHeight: .infinity)   // the filter is in the section header, like all the toggles
        } right: {
            if let id = model.selectedTask {
                TaskDetail(id: id).id(id).transition(Style.Motion.appear)
            } else {
                Empty(title: L("Выбери задачу"), icon: "checklist")
            }
        }
        .motion(value: model.selectedTask)
        .actions {
            Segments(selection: $filter, options: [("all", L("все")), ("remarks", L("с замечаниями")), ("failed", L("не вышло")), ("service", L("служебные"))], collapses: true)
            HeaderSearch(prompt: L("Поиск по задачам"), text: $search)
        }
        .task {
            while !Task.isCancelled {
                await reload()
                // every 3 s only while his own task runs (Sena's service ones — a review, the night, a long work's turn — can run
                // for hours); idle — every 15 s, and a task starting or ending (the overview, every 2 s) brings it at once
                try? await Task.sleep(nanoseconds: (model.ownTasks.isEmpty ? 15 : 3) * 1_000_000_000)
            }
        }
        .onChange(of: model.active.count) { Task { await reload() } }
    }

    /// The list without the finished tasks' steps and cards (the detail pane loads them), and only when it changed: the server
    /// answers "same" to the signature it sent last time
    func reload() async {
        do {
            let d = try await model.backend.get("tasks", ["limit": "300", "slim": "1", "same": data.exists ? sig : ""])
            failed = nil
            if !d["same"].bool || !data.exists { data = d; sig = d["sig"].str }
            if model.selectedTask == nil { model.selectedTask = data["active"].array.first.map(AppModel.taskID) ?? data["finished"].array.first?["id"].string }
        } catch { if !Task.isCancelled && !data.exists { failed = error.localizedDescription } }
    }

    @ViewBuilder var list: some View {
        let running = active, finished = rows   // each list is computed once per frame, not once per row
        if !data.exists {
            if let f = failed { loadFailed(f) } else { SkeletonList(rows: 7) }
        } else if running.isEmpty && finished.isEmpty {
            Empty(title: filtering ? L("Ничего не нашлось") : L("Задач ещё не было"), icon: "checklist")
        } else {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Style.Space.s) {
                    if !running.isEmpty {
                        SectionHeader(title: String(format: L("В работе · %d"), running.count))
                        ForEach(running, id: \.["id"].str) { t in
                            TaskActiveRow(t: t, selected: model.selectedTask == AppModel.taskID(t)) { model.selectedTask = AppModel.taskID(t) }
                                .transition(Style.Motion.remove)
                        }
                    }
                    if filtering && !finished.isEmpty {
                        SectionHeader(title: String(format: L("Найдено · %d"), finished.count)).padding(.top, running.isEmpty ? 0 : Style.Space.m)
                    }
                    ForEach(Array(finished.enumerated()), id: \.element["id"].str) { i, t in
                        if i == 0 || Fmt.dayGroup(finished[i - 1]["start"]) != Fmt.dayGroup(t["start"]) {
                            SectionHeader(title: dayTitle(t)).padding(.top, i == 0 && running.isEmpty && !filtering ? 0 : Style.Space.m)
                        }
                        TaskDoneRow(t: t, selected: model.selectedTask == t["id"].str) { model.selectedTask = t["id"].str }
                            .transition(Style.Motion.remove)
                    }
                }
                .motion(value: running.count)
                .motion(value: finished.count)
            }
        }
    }

    func dayTitle(_ t: JSON) -> String { let s = Fmt.dayTitle(t["start"]); return s.isEmpty ? L("Без даты") : s }
}

/// A running task: a card with a lightning bolt, the last step, how long it's been running and its cost, a stop button
struct TaskActiveRow: View {
    @EnvironmentObject var model: AppModel
    let t: JSON
    let selected: Bool
    let select: () -> Void
    var body: some View {
        RecordCard(icon: "bolt.fill", tint: Style.Colors.warn, title: taskTitle(t), lines: 1, selected: selected, action: select, meta: {
            Text(taskStage(t)).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(2)
        }, trailing: { _ in
            VStack(alignment: .trailing, spacing: Style.Space.xxs) {
                TimelineView(.periodic(from: .now, by: 1)) { ctx in   // the clock runs on its own, without redrawing the whole list
                    let spent = Fmt.dur(ctx.date.timeIntervalSince1970 - t["start"].dbl)
                    Text(spent).font(Style.Text.caption.monospacedDigit()).foregroundStyle(Style.Colors.secondaryText).rolling(spent)
                }
                if t["cost"].dbl > 0 { Text(Fmt.usd(t["cost"].dbl)).font(Style.Text.caption.monospacedDigit()).foregroundStyle(Style.Colors.secondaryText) }
            }
            Button(action: stop) {
                Glyph("stop.circle").font(Style.Icon.inline).foregroundStyle(Style.Colors.bad)
                    .frame(width: Style.Size.controlHeight, height: Style.Size.controlHeight).contentShape(Rectangle())
            }
            .buttonStyle(.plain).help(L("Остановить задачу")).accessibilityLabel(L("Остановить задачу"))
        })
    }
    func stop() {
        let id = AppModel.taskID(t)
        guard !id.isEmpty else { return }
        model.stopTask(id, title: taskTitle(t))
    }
}

/// The task's name in a list: the short title Sena gave it when it ended; his own words until then (or if naming failed)
func taskTitle(_ t: JSON) -> String { t["title"].string.flatMap { $0.isEmpty ? nil : $0 } ?? t["text"].str }

/// Where a running task is now: which step, who works and what it is doing — «Шаг 4 · Агент браузера: Открываю hh.ru».
/// The list showed only the last step's words, and nothing said how far along it was
func taskStage(_ t: JSON) -> String {
    let steps = t["steps"].array
    guard let last = steps.last else { return L("Сена разбирает задачу") }
    return String(format: L("Шаг %d · %@: %@"), steps.count, agentName(last["who"].str), last["text"].str)
}

/// A finished task: a card with a verdict icon, verdict as a tag, time, duration, cost, remarks
struct TaskDoneRow: View {
    let t: JSON
    let selected: Bool
    let select: () -> Void
    var meta: String {
        var parts = [Fmt.time(t["start"])]
        if t["end"].dbl > t["start"].dbl { parts.append(Fmt.dur(t["end"].dbl - t["start"].dbl)) }
        if t["cost"].dbl > 0 { parts.append(Fmt.usd(t["cost"].dbl)) }
        let n = t["comments"].array.count
        if n > 0 { parts.append("\(n) " + Fmt.plural(n, "замечание", "замечания", "замечаний")) }
        return parts.joined(separator: " · ")
    }
    var body: some View {
        let v = t["outcome"]["verdict"].string ?? "none"
        RecordCard(icon: verdictIcon(v), tint: verdictColor(v), title: taskTitle(t), lines: 1, selected: selected, action: select) {
            verdictTag(t)
            Text(meta).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(1)
        }
    }
}

/// Task details: summary tiles, answer, progress, verdict, remarks, events.
/// A running task refreshes every 2 s, a finished one every 10 s (the verdict and review arrive after it ends)
struct TaskDetail: View {
    @EnvironmentObject var model: AppModel
    let id: String
    @State private var t = JSON(nil)
    @State private var error: String? = nil
    @State private var applied = ""          // which verdict from the server is already in the form: while it hasn't been touched, a new verdict (from the judge) is applied automatically
    @State private var verdict = "done"
    @State private var reason = ""
    @State private var comment = ""
    @State private var savingOutcome = false
    @State private var commenting = false
    @State private var showEvents = false
    var done: Bool { t["done"].bool }
    var answer: String { t["answer"].str }
    var steps: [JSON] { t["steps"].array }
    var events: [JSON] { t["events"].array }
    var errorCount: Int { events.filter { $0["kind"].str == "error" }.count }

    var body: some View {
        Group {
            if let e = error, !t.exists {
                Empty(title: L("Задача не открылась"), text: e, icon: "exclamationmark.triangle")
            } else if !t.exists {
                Loading()
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: Style.Space.xl) {
                        head
                        answerBlock
                        stepsBlock
                        if done { outcomeBlock; commentsBlock }
                        eventsBlock
                    }
                    .motion(value: showEvents)
                    .motion(value: done)
                }
            }
        }
        .task(id: id) {
            while !Task.isCancelled {
                await load()
                let pause: UInt64 = (done || error != nil) ? 10 : 2
                try? await Task.sleep(nanoseconds: pause * 1_000_000_000)
            }
        }
    }

    // ---- blocks ----
    var head: some View {
        let spent = (t["end"].double ?? Date().timeIntervalSince1970) - t["start"].dbl
        return Block(lead: true) {
            VStack(alignment: .leading, spacing: Style.Space.l) {
                Text(taskTitle(t)).font(Style.Text.title).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                if taskTitle(t) != t["text"].str {   // the title is Sena's; his own words stay right under it
                    Text(String(format: L("Твои слова: %@"), t["text"].str)).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
                        .textSelection(.enabled).lineLimit(3)
                }
                if !done { Text(taskStage(t)).font(Style.Text.small.weight(.medium)).lineLimit(2) }   // what is happening right now
                Text(Fmt.whenFull(t["start"])).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
                HStack(spacing: Style.Space.m) {
                    if done { verdictTag(t) } else { Tag(text: L("в работе"), color: Style.Colors.warn) }
                    if t["service"].bool { Tag(text: L("служебная")) }
                    if error != nil { Tag(text: L("обновление не удалось"), color: Style.Colors.warn) }   // the data on screen may be stale
                    Spacer()
                    if !done { Button(L("Остановить"), role: .destructive, action: stop) }
                }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: TaskLayout.tile), spacing: Style.Space.m)], spacing: Style.Space.m) {
                    if done { Stat(value: Fmt.dur(spent), label: L("длилась")) }
                    else { TimelineView(.periodic(from: .now, by: 1)) { ctx in Stat(value: Fmt.dur(ctx.date.timeIntervalSince1970 - t["start"].dbl), label: L("идёт")) } }   // the clock runs on its own, not only on new data
                    Stat(value: Fmt.usd(t["cost"].dbl), label: L("стоимость"))
                    Stat(value: "\(steps.count)", label: L("шагов"))
                    Stat(value: "\(events.count)", label: L("событий"))
                    Stat(value: "\(errorCount)", label: L("ошибок"), bad: errorCount > 0)
                }
            }
        }
    }

    var answerBlock: some View {
        Block(L("Ответ"), content: {
            if answer.isEmpty {
                Text(done ? L("ответа не было") : "—").font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
            } else {
                MarkdownView(text: answer).frame(maxWidth: .infinity, alignment: .leading)
            }
        }, trailing: {
            if !answer.isEmpty { Button(L("Копировать")) { model.copy(answer, toast: L("Ответ скопирован")) } }
        })
    }

    var stepsBlock: some View {
        Block(String(format: L("Ход работы · %d"), steps.count)) {
            VStack(alignment: .leading, spacing: Style.Space.s) {
                ForEach(Array(steps.enumerated()), id: \.offset) { _, s in
                    HStack(alignment: .firstTextBaseline, spacing: Style.Space.m) {
                        Text(Fmt.times(s["time"])).font(Style.Text.caption.monospacedDigit()).foregroundStyle(Style.Colors.tertiaryText).frame(width: TaskLayout.time, alignment: .leading)
                        Text(agentName(s["who"].str)).font(Style.Text.small.weight(.semibold)).frame(width: TaskLayout.agent, alignment: .leading)
                        Text(s["text"].str).font(Style.Text.small).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .transition(Style.Motion.appear)
                }
                if steps.isEmpty { Text(done ? L("шагов не было") : "—").font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText) }
                if !done { TypingDots() }
            }
            .motion(value: steps.count)
        }
    }

    /// The verdict with whose it is (the judge's or the owner's), what went wrong and whose doing it was; when the owner overrode
    /// the judge, the judge's own view stays beside it (task_log.set_outcome keeps both); the owner's 1–5 score, given on the phone
    var outcomeBlock: some View {
        let o = t["outcome"], judge = t["judge_outcome"]
        return Block(L("Оценка"), content: {
            VStack(alignment: .leading, spacing: Style.Space.m) {
                if o["reason"].exists, !o["reason"].str.isEmpty {
                    Text(o["reason"].str).font(Style.Text.small).textSelection(.enabled)
                }
                if o.exists {
                    HStack(spacing: Style.Space.s) {
                        Tag(text: o["by"].str == "owner" ? L("твоя оценка") : L("оценка разбора"))
                        if let cause = o["cause"].string { Tag(text: String(format: L("чьё упущение: %@"), L(cause)), color: Style.Colors.warn) }
                        if t["rating"]["value"].int > 0 { Tag(text: String(format: L("твой балл: %d из 5"), t["rating"]["value"].int), color: Style.Colors.info) }
                    }
                }
                if judge.exists {
                    Text(String(format: L("Разбор считал: %@"), (VERDICT_RU[judge["verdict"].str] ?? judge["verdict"].str) + (judge["reason"].str.isEmpty ? "" : " — " + judge["reason"].str)))
                        .font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).textSelection(.enabled)
                }
                HStack(spacing: Style.Space.m) {
                    Select(selection: $verdict, options: ["done", "done_with_remarks", "failed", "unverified"].map { ($0, VERDICT_RU[$0] ?? $0) })
                    TextField(L("Причина"), text: $reason).onSubmit(saveOutcome)
                    Button(L("Сохранить"), action: saveOutcome).buttonStyle(.primary).disabled(savingOutcome)
                }
            }
        })
    }

    var commentsBlock: some View {
        Block(String(format: L("Замечания · %d"), t["comments"].array.count)) {
            VStack(alignment: .leading, spacing: Style.Space.m) {
                ForEach(Array(t["comments"].array.enumerated()), id: \.offset) { _, c in
                    HStack(alignment: .firstTextBaseline, spacing: Style.Space.m) {
                        Text(Fmt.when(c["time"])).font(Style.Text.caption).foregroundStyle(Style.Colors.tertiaryText).frame(width: TaskLayout.when, alignment: .leading)
                        Text(c["text"].str).font(Style.Text.small).textSelection(.enabled)
                    }
                }
                HStack(alignment: .bottom, spacing: Style.Space.m) {
                    TextField(L("Что сделать иначе в следующий раз"), text: $comment, axis: .vertical).lineLimit(1...4).onSubmit(addComment)
                    Button(L("Добавить"), action: addComment).buttonStyle(.primary).disabled(comment.trimmingCharacters(in: .whitespaces).isEmpty || commenting)
                }
            }
        }
    }

    var eventsBlock: some View {
        Block(String(format: L("События задачи · %d"), events.count), content: {
            if showEvents {
                LazyVStack(alignment: .leading, spacing: Style.Space.xs) {
                    ForEach(Array(events.enumerated()), id: \.offset) { _, e in TaskEventRow(e: e) }
                }
                .transition(Style.Motion.appear)
            } else {
                Text(eventsSummary).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
            }
        }, trailing: {
            Button(showEvents ? L("Скрыть") : L("Показать")) { showEvents.toggle() }.disabled(events.isEmpty)
        })
    }

    /// "model 4 · tool 7 · error 1" — what the task was made of, without expanding the list
    var eventsSummary: String {
        if events.isEmpty { return L("событий нет") }
        let counts = Dictionary(grouping: events, by: { $0["kind"].str }).mapValues(\.count)
        return counts.sorted { $0.value != $1.value ? $0.value > $1.value : $0.key < $1.key }   // on a tie — by kind: labels don't jump around
            .prefix(6).map { "\(KIND_RU[$0.key] ?? $0.key) \($0.value)" }.joined(separator: " · ")
    }

    // ---- actions ----
    func load() async {
        do {
            let r = try await model.backend.get("task", ["id": id])
            if !t.isSame(r) { t = r }
            error = nil
            fill(r)
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
    /// The verdict and reason in the form are the server's; but only once the task is finished (a running one has no verdict) and as long as they haven't been edited by hand:
    /// the judge sets the verdict after the task ends, and then it appears in the form on its own
    func fill(_ r: JSON) {
        guard r["done"].bool else { return }
        let v = r["outcome"]["verdict"].string ?? "done", why = r["outcome"]["reason"].str
        let key = v + "|" + why
        guard key != applied else { return }
        if applied.isEmpty || verdict + "|" + reason == applied { verdict = v; reason = why }
        applied = key
    }
    func stop() {
        let tid = t["id"].string ?? id   // the page is the task's own entry: its id is the task's, not the thread it runs on
        guard !tid.isEmpty else { return }
        model.stopTask(tid, title: taskTitle(t))
    }
    func saveOutcome() {
        guard !savingOutcome else { return }
        let why = reason.trimmingCharacters(in: .whitespaces)
        var body: [String: Any] = ["id": id, "verdict": verdict]
        if !why.isEmpty { body["reason"] = why }   // without a reason the server will label it "verdict from the app"
        savingOutcome = true
        model.runAction(L("Оценка"), "outcome", body, done: { _ in savingOutcome = false; Task { await load() } }, fail: { savingOutcome = false })
    }
    func addComment() {
        let c = comment.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !c.isEmpty, !commenting else { return }
        commenting = true; comment = ""   // the field clears right away, on failure the remark is put back: every remark triggers a paid review, a second tap would run it twice
        model.runAction(L("Замечание"), "comment", ["id": id, "text": c],
                        done: { _ in commenting = false; Task { await load() } },
                        fail: { commenting = false; if comment.isEmpty { comment = c } })
    }
}

/// A task event: time, kind as a tag, text, who
struct TaskEventRow: View {
    let e: JSON
    var text: String {
        let t = e["text"].str
        if !t.isEmpty { return t }
        return e["name"].str.isEmpty ? "—" : e["name"].str
    }
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Style.Space.m) {
            Text(Fmt.times(e["t"])).font(Style.Text.caption.monospacedDigit()).foregroundStyle(Style.Colors.tertiaryText).frame(width: TaskLayout.time, alignment: .leading)
            Tag(text: KIND_RU[e["kind"].str] ?? e["kind"].str, color: kindColor(e["kind"].str))
            Text(text).font(Style.Text.caption).lineLimit(2).textSelection(.enabled)
            Spacer(minLength: Style.Space.m)
            if !e["who"].str.isEmpty { Text(agentName(e["who"].str)).font(Style.Text.tiny).foregroundStyle(Style.Colors.tertiaryText) }
        }
    }
}
