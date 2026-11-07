// Memory: the memory text with an editor and a summary alongside it, people, notes, skills. The tab is picked with pills in the section header.
// Every tab is built from blocks: a list of rows on the left, details on the right; the tab's actions are in the header via .actions.
// Anything dangerous (deleting a skill, merging people's cards, clearing memory) only happens through confirmation.
// An unsaved edit isn't lost silently: the screen reports it to the model (setUnsaved) — leaving the tab asks on its own,
// and picking a different record within the screen asks through confirmDiscard. Lists refresh themselves every 15 seconds, without overwriting an open edit.
import SwiftUI

// ---- shared across memory tabs ----

/// Editor field: a contrasting backing (dark on a light block, light in the main one)
private struct MemoryEditorBox: ViewModifier {
    func body(content: Content) -> some View {
        content
            .scrollContentBackground(.hidden)
            .padding(Style.Space.m)
            .contrast()
    }
}
private extension View { func memoryEditor() -> some View { modifier(MemoryEditorBox()) } }

/// Labels for index entry kinds in the counters: for cases where the word needs a different form than in search results (SEARCH_KIND_RU in ContentView);
/// other kinds are taken from there, an unknown one is shown as is
private let INDEX_KIND_RU = ["сообщение": L("сообщения"), "факт": L("факты"), "навык": L("навыки"), "услышано": L("услышано"), "файл": L("файлы"),
                              "урок": L("уроки агентов"), "цель": L("цели")]

/// Note vault folders arrive from the server in Russian (vault.FOLDERS): the key stays as is, for display there's a translation; a nested path is translated piece by piece
private let NOTE_FOLDER_RU = ["Дневник": L("Дневник"), "Люди": L("Люди"), "Проекты": L("Проекты"), "Организации": L("Организации"), "Места": L("Места"),
                              "Знания": L("Знания"), "Идеи": L("Идеи"), "Входящие": L("Входящие"), "Память": L("Память"), "Навыки": L("Навыки"),
                              "Архив": L("Архив"), "Вложения": L("Вложения")]
private func folderName(_ folder: String) -> String {
    folder.split(separator: "/", omittingEmptySubsequences: false).map { NOTE_FOLDER_RU[String($0)] ?? String($0) }.joined(separator: "/")
}

/// Markers for the automatic block in Sena's notes (vault.BEGIN and vault.END): she writes inside them, everything after is Tema's own
private let NOTE_BEGIN = "<!-- sena:begin -->"
private let NOTE_END = "<!-- sena:end -->"

/// How many lines to show in a fact card: all of them, no truncation
private let ALL_LINES = 99

/// A field's label in an edit grid
private func fieldLabel(_ s: String) -> some View { Text(s).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText).gridColumnAlignment(.leading) }

private func lineCount(_ s: String) -> Int { s.isEmpty ? 0 : s.split(separator: "\n", omittingEmptySubsequences: false).count }
private func lines(_ n: Int) -> String { "\(n) \(Fmt.plural(n, "строка", "строки", "строк"))" }

/// Counter on the right of a block's title: the digits roll over
func listCount(_ text: String) -> some View {
    Text(text).font(Style.Text.caption.monospacedDigit()).foregroundStyle(Style.Colors.secondaryText).rolling(text)
}

/// Empty, but the block stays: a short dash instead of empty space
private struct Dash: View {
    var body: some View { Text("—").font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText) }
}

/// Search field in a section's header: narrower in a narrow window
struct HeaderSearch: View {
    @Environment(\.narrowWindow) private var narrow
    let prompt: String
    @Binding var text: String
    var body: some View { TextField(prompt, text: $text).frame(width: narrow ? Style.Size.searchNarrow : Style.Size.field) }
}

/// Leave an unsaved edit (pick a different record, start a new one): if there's an edit, ask first
private func confirmDiscard(_ dirty: Bool) -> Bool {
    !dirty || confirm(L("Есть несохранённые правки"), L("Если уйти, они пропадут."), button: L("Уйти без сохранения"), destructive: true)
}

extension AppModel {
    /// A server action with a toast (like run). done gets the reply before the toast, fail is called on error: the screen will restore the input or revert the switch
    func runAction(_ what: String, _ name: String, _ body: [String: Any] = [:], done: ((JSON) -> Void)? = nil, fail: (() -> Void)? = nil) {
        run(what, {
            do {
                let r = try await self.backend.act(name, body)
                let rejected = r["rejected"].array.map { $0.str }   // settings: the server didn't accept everything — that's a refusal, not "done"
                if !rejected.isEmpty { throw APIError(message: String(format: L("Сервер не принял: %@"), rejected.joined(separator: ", "))) }
                done?(r)
                return r
            } catch { fail?(); throw error }
        })
    }
}

/// Loading a record on screen: still in progress, ready, or failed (with a reason)
private enum OpenPhase: Equatable { case idle, loading, ready, failed(String) }

/// Edit actions in a block's header: an "edited" tag, "Reset" (if there's somewhere to reset to), "Save" (⌘S)
private struct EditActions: View {
    let dirty: Bool
    var busy = false          // a server request is still in flight
    var canSave = true
    var hint = ""             // why "Save" is disabled even though there are edits
    var reset: (() -> Void)? = nil
    let save: () -> Void
    var body: some View {
        if dirty { Tag(text: L("изменено"), color: Style.Colors.warn).transition(Style.Motion.appear) }
        if let reset = reset { Button(L("Сбросить"), action: reset).disabled(!dirty || busy) }
        Button(L("Сохранить"), action: save).buttonStyle(.primary).disabled(!dirty || !canSave || busy)
            .keyboardShortcut("s", modifiers: .command)
            .help(dirty && !canSave ? hint : "")
    }
}

/// Long text, collapsed by default: a hint line and a "Show" button; expands with a spring
private struct FoldedText: View {
    let title: String
    let text: String
    let empty: String
    @State private var open = false
    var body: some View {
        Block(title, content: {
            if open {
                Text(text.isEmpty ? empty : text).font(Style.Text.monoSmall).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .transition(Style.Motion.appear)
            } else {
                Text(text.isEmpty ? empty : lines(lineCount(text))).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
            }
        }, trailing: {
            Button(open ? L("Скрыть") : L("Показать")) { open.toggle() }.disabled(text.isEmpty && !open)
        })
        .motion(value: open)
    }
}

/// The screen didn't load and there's nothing to show: a reason instead of an endless placeholder
func loadFailed(_ message: String, retry: (() -> Void)? = nil) -> some View {
    Empty(title: L("Не загрузилось"), text: message, icon: "exclamationmark.triangle", button: retry == nil ? nil : L("Повторить"), act: retry)
}

/// The list column on the left (people, skills, notes): loading, error, "empty", or rows, with a counter in the title
private struct ListColumn<Rows: View>: View {
    let title: String
    let count: Int            // total records: shown on the right of the title
    let loaded: Bool
    let failed: String?
    let empty: String         // what to write when there's nothing to show
    let emptyIcon: String
    let ids: [String]         // what's currently shown: rows rearrange smoothly based on this
    @ViewBuilder let rows: () -> Rows
    var body: some View {
        Block(title, content: {
            if !loaded {
                if let f = failed { loadFailed(f) } else { SkeletonList(rows: 5) }
            } else if ids.isEmpty {
                Empty(title: empty, icon: emptyIcon)
            } else {
                ScrollView {
                    VStack(spacing: Style.Space.s) { rows() }
                        .motion(value: ids)
                }
            }
        }, trailing: { listCount("\(count)") })
        .frame(maxHeight: .infinity)
    }
}

// ---- Memory: text ----

/// A memory section from the editor's text: "## Section" with "- fact" lines under it (like facts.sections() on the server)
private struct MemorySection { let title: String; var items: Int }

/// One memory line the editor changed, as the server takes it: a new line (fact_add), a line replaced by another (fact_edit —
/// the old wording stays in the archive), a line gone (fact_delete). The same one-line actions the phone and Sena's own
/// remember go through: a password is refused, a new line gets its date. The whole text written raw skipped all of that
private struct MemoryChange {
    let action: String
    let body: [String: Any]
    let section: String
    let line: String   // the line as the editor had it: put back into the editor when the server refuses it
}

/// The editor's lines with their sections: "- " items under "## " headers; a plain line typed under a section counts as one too
private func memoryLines(_ text: String) -> [(section: String, line: String)] {
    var section = "", out: [(section: String, line: String)] = []
    for raw in text.split(separator: "\n") {
        let l = raw.trimmingCharacters(in: .whitespaces)
        if l.hasPrefix("## ") { section = String(l.dropFirst(3)).trimmingCharacters(in: .whitespaces); continue }
        guard !l.isEmpty, !l.hasPrefix("#") else { continue }
        let item = (l.hasPrefix("- ") ? String(l.dropFirst(2)) : l).trimmingCharacters(in: .whitespaces)
        if !item.isEmpty { out.append((section, item)) }
    }
    return out
}

/// A memory line's date stamp ("24.10.2026 14:03: ") and the rest (like facts.plain on the server)
private func memoryStamp(_ line: String) -> (stamp: String, rest: String) {
    guard let m = try? NSRegularExpression(pattern: "^\\d{2}\\.\\d{2}\\.\\d{4}(?: \\d{2}:\\d{2})?:\\s*").firstMatch(in: line, range: NSRange(line.startIndex..., in: line)),
          let r = Range(m.range, in: line) else { return ("", line) }
    return (String(line[r]).trimmingCharacters(in: .whitespaces), String(line[r.upperBound...]))
}

/// What changed between the text the edit started from and the edited one, line by line within each section. A line changed
/// in place pairs with the line it replaced (kept in the archive); one that kept its date but not its words gets today's date;
/// a line moved to another section goes there with its own date. Deletes go first: forget takes a line's twins with it
private func memoryChanges(from base: String, to text: String) -> [MemoryChange] {
    let old = memoryLines(base), new = memoryLines(text)
    let first = new.first { !$0.section.isEmpty }?.section ?? "Обо мне"   // a line above every header joins the first section (a server key, not shown)
    var sections: [String] = []
    for l in new + old where !sections.contains(l.section) { sections.append(l.section) }
    var deletes: [MemoryChange] = [], edits: [MemoryChange] = [], adds: [MemoryChange] = []
    for s in sections {
        let into = s.isEmpty ? first : s
        var gone = old.filter { $0.section == s }.map(\.line), added: [String] = []
        for l in new where l.section == s {
            if let i = gone.firstIndex(of: l.line) { gone.remove(at: i) } else { added.append(l.line) }
        }
        let pairs = min(gone.count, added.count)
        for i in 0..<pairs {
            let was = memoryStamp(gone[i]), now = memoryStamp(added[i])
            if now.rest.trimmingCharacters(in: .whitespaces).isEmpty {   // only the date is left of the line: it's gone
                deletes.append(MemoryChange(action: "fact_delete", body: ["text": gone[i]], section: into, line: gone[i]))
                continue
            }
            let text = now.stamp == was.stamp ? now.rest : added[i]   // the words changed under the old date: dated anew; a date he typed stays
            edits.append(MemoryChange(action: "fact_edit", body: ["section": into, "old": gone[i], "text": text], section: into, line: added[i]))
        }
        deletes += gone.dropFirst(pairs).map { MemoryChange(action: "fact_delete", body: ["text": $0], section: into, line: $0) }
        adds += added.dropFirst(pairs).map { MemoryChange(action: "fact_add", body: ["section": into, "text": $0], section: into, line: $0) }
    }
    return deletes + edits + adds
}

/// The server's memory text with the lines it refused put back where the owner typed them: the editor stays "edited" with
/// exactly what didn't go through, the next save sends only that
private func withRefused(_ text: String, _ refused: [MemoryChange]) -> String {
    var lines = text.components(separatedBy: "\n")
    for c in refused where c.action != "fact_delete" {
        if let h = lines.firstIndex(of: "## " + c.section) {
            var at = h + 1
            while at < lines.count && !lines[at].hasPrefix("## ") { at += 1 }
            while at > h + 1 && lines[at - 1].trimmingCharacters(in: .whitespaces).isEmpty { at -= 1 }
            lines.insert("- " + c.line, at: at)
        } else {
            lines += ["", "## " + c.section, "- " + c.line]
        }
    }
    return lines.joined(separator: "\n")
}

/// The memory text editor on the left, sections (computed from the text on the fly), the index, personality and archive on the right
struct MemoryCoreView: View {
    @EnvironmentObject var model: AppModel
    @State private var m = JSON(nil)
    @State private var failed: String? = nil
    @State private var text = ""
    @State private var saved = ""   // the text the edit is based on: only what differs from it is sent, so facts Sena added in the meantime aren't lost
    @State private var saving = false
    @State private var indexBusy: String? = nil   // which index action is running right now
    var dirty: Bool { text != saved }
    var serverText: String { m["text"].str }
    private var sections: [MemorySection] {
        var out: [MemorySection] = []
        for line in text.split(separator: "\n") {
            if line.hasPrefix("## ") { out.append(MemorySection(title: String(line.dropFirst(3)), items: 0)) }
            else if line.hasPrefix("- "), !out.isEmpty { out[out.count - 1].items += 1 }
        }
        return out
    }

    var body: some View {
        let secs = sections   // parsing the text once per frame, not once per block
        Group {
            if model.narrow {   // in a narrow window the columns stack on top of each other
                ScrollView {
                    VStack(spacing: Style.Space.xl) {
                        editor(secs).frame(height: Style.Size.flowHeight * 3)
                        side(secs)
                    }
                }
            } else {
                HStack(alignment: .top, spacing: Style.Space.xl) {
                    editor(secs).frame(maxWidth: .infinity, maxHeight: .infinity)
                    ScrollView { side(secs) }.frame(width: Style.Size.column)
                }
            }
        }
        .task {
            while !Task.isCancelled {
                await load()
                let pause: UInt64 = failed == nil ? 20 : 5   // didn't work — retry more often
                try? await Task.sleep(nanoseconds: pause * 1_000_000_000)
            }
        }
        .onChange(of: dirty) { _, d in model.setUnsaved("memory", d) }
        .onDisappear { model.setUnsaved("memory", false) }
    }

    private func side(_ secs: [MemorySection]) -> some View {
        VStack(spacing: Style.Space.xl) {
            sectionsBlock(secs)
            indexBlock
            FoldedText(title: L("Характер"), text: m["personality"].str, empty: L("Пока пусто"))
            FoldedText(title: L("Архив"), text: m["archive"].str, empty: L("Архив пуст"))
        }
    }

    private func editor(_ secs: [MemorySection]) -> some View {
        let facts = secs.reduce(0) { $0 + $1.items }
        return Block(L("Что Сена помнит"), lead: true, content: {
            if !m.exists {   // while memory hasn't arrived, there's nothing to edit: otherwise an empty field could get saved over all of memory
                if let f = failed { loadFailed(f) } else { Loading() }
            } else {
                TextEditor(text: $text).font(Style.Text.mono).memoryEditor()
                Text(String(format: L("%@ в %@ · сохранено %@"), "\(facts) \(Fmt.plural(facts, "факт", "факта", "фактов"))",
                            "\(secs.count) \(Fmt.plural(secs.count, "разделе", "разделах", "разделах"))", Fmt.when(m["updated"])))
                    .font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).contentTransition(.numericText())
            }
        }, trailing: {
            EditActions(dirty: dirty, busy: saving, reset: { text = serverText; saved = serverText }, save: save)
        })
        .frame(maxHeight: .infinity)
        .motion(value: dirty)
    }

    private func sectionsBlock(_ secs: [MemorySection]) -> some View {
        Block(L("Разделы"), content: {
            if secs.isEmpty {
                Dash()
            } else {
                VStack(spacing: Style.Space.s) {
                    ForEach(Array(secs.enumerated()), id: \.offset) { _, sec in
                        RecordCard(icon: "text.alignleft", tint: Style.Colors.tint(sec.title), title: sec.title, lines: 1, meta: {
                            Text("\(sec.items) \(Fmt.plural(sec.items, "факт", "факта", "фактов"))").font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
                        })
                    }
                }
            }
        }, trailing: { listCount("\(secs.count)") })
        .motion(value: secs.count)
    }

    private var indexBlock: some View {
        let idx = m["index"]
        // descending by count, ties broken by kind: labels don't jump between polls
        let kinds = idx["by_kind"].dict.map { ($0.key, $0.value.int) }.sorted { $0.1 != $1.1 ? $0.1 > $1.1 : $0.0 < $1.0 }
        // the registry's own numbers (index.stats) and the seam where exact search should give way to a vector index (index.ANN_*)
        let stats = idx["stats"], threshold = idx["ann_threshold"]
        let vectors = stats["vectors"].int, searchMs = stats["search_ms"].dbl
        let annVectors = threshold["vectors"].exists ? threshold["vectors"].int : 300_000
        let annMs = threshold["search_ms"].exists ? threshold["search_ms"].dbl : 50
        let crowded = vectors > annVectors || searchMs > annMs
        return Block(L("Индекс поиска"), lead: true) {
            HStack(spacing: Style.Space.m) {
                Stat(value: "\(idx["items"].int)", label: L("записей"))
                Stat(value: "\(idx["pending"].int)", label: L("без индекса"))
                Stat(value: Fmt.bytes(idx["size"].dbl), label: L("размер базы"))
            }
            if stats.exists {
                HStack(spacing: Style.Space.m) {
                    Stat(value: "\(vectors)", label: L("отпечатков"))
                    Stat(value: "\(stats["versions"].int)", label: L("версий"))
                    Stat(value: "\(stats["links"].int)", label: L("связей"))
                }
                KV(rows: [(L("матрица в памяти"), String(format: "%.1f %@", stats["matrix_mb"].dbl, L("МБ"))),
                          (L("последний поиск"), String(format: "%.1f %@", searchMs, L("мс"))),
                          (L("запас до быстрого индекса"), crowded ? L("исчерпан") : "\(max(0, annVectors - vectors)) \(L("отпечатков"))")])
                if crowded {
                    Text(String(format: L("Пора переходить на быстрый индекс: отпечатков %d из %d, поиск %.0f мс при пороге %.0f"), vectors, annVectors, searchMs, annMs))
                        .font(Style.Text.caption).foregroundStyle(Style.Colors.warnText)
                }
            }
            if !kinds.isEmpty {
                Text(kinds.map { "\(INDEX_KIND_RU[$0.0] ?? SEARCH_KIND_RU[$0.0] ?? $0.0) \($0.1)" }.joined(separator: " · ")).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
            }
            HStack(spacing: Style.Space.m) {   // buttons below the tiles: in a narrow column they don't fit in the title
                Button(L("Пересобрать")) { indexAction("sync_index", L("Пересборка индекса")) }
                    .disabled(indexBusy != nil)
                    .help(L("Пересобрать индекс поиска по памяти, людям и заметкам"))
                Button(L("Проиндексировать")) { indexAction("embed", L("Индексация")) }
                    .disabled(indexBusy != nil || idx["pending"].int == 0)
                    .help(idx["embed_place"].str == "cloud"
                          ? String(format: L("Посчитать отпечатки записям, которых ещё нет в индексе (облачная модель %@ — платно)"), idx["embed"].str)
                          : L("Посчитать отпечатки записям, которых ещё нет в индексе (домашняя модель на этом Mac — бесплатно)"))
            }
        }
    }

    /// While an action is running, both buttons are disabled: a second "Index" would count the same records again
    private func indexAction(_ name: String, _ what: String) {
        guard indexBusy == nil else { return }
        indexBusy = name
        model.runAction(what, name, done: { _ in indexBusy = nil; reloadSoon() }, fail: { indexBusy = nil })
    }
    /// The reconciliation runs in the background: refresh the numbers right away and once more a bit later
    private func reloadSoon() { Task { await load(); try? await Task.sleep(nanoseconds: 3_000_000_000); await load() } }

    func load() async {
        do {
            let r = try await model.backend.get("memory")
            failed = nil
            if m.exists && r.isSame(m) { return }
            // an outside change doesn't overwrite what's typed, and the edit's base (saved) stays the same: only his own changes are sent
            if !dirty { text = r["text"].str; saved = r["text"].str }
            m = r
        } catch { if !Task.isCancelled && !m.exists { failed = error.localizedDescription } }
    }

    /// Only what changed goes to the server, a line at a time (memoryChanges): what Sena added meanwhile stays, a password is
    /// refused, a new line is dated, a replaced one keeps its history in the archive. Refused lines stay in the editor, marked edited
    private func save() {
        let sent = text, changes = memoryChanges(from: saved, to: sent)
        guard !changes.isEmpty else { text = saved; model.say(L("Изменений в фактах нет: порядок строк память не хранит")); return }
        if memoryLines(sent).isEmpty,
           !confirm(L("Очистить всю память?"), L("Все факты исчезнут. Отменить нельзя."), button: L("Очистить"), destructive: true) { return }
        saving = true
        Task {
            var refused: [(MemoryChange, String)] = []
            for c in changes {
                do { _ = try await model.backend.act(c.action, c.body) } catch { refused.append((c, error.localizedDescription)) }
            }
            if let r = try? await model.backend.get("memory") {
                let fresh = r["text"].str
                if text == sent { text = withRefused(fresh, refused.map(\.0)) }   // typed more meanwhile — what's typed stays, the base moves on
                saved = fresh
                m = r
            }
            saving = false
            if let f = refused.first {
                let line = String(f.0.line.prefix(60)), why = f.1
                model.say(refused.count == 1 ? String(format: L("Не сохранено «%@»: %@"), line, why)
                                             : String(format: L("Не сохранено строк: %d. Первая — «%@»: %@"), refused.count, line, why), hold: 9)
            } else {
                model.say(L("Память сохранена"))
            }
        }
    }
}

// ---- People ----

/// How to show a person: their name, otherwise the first of their other names (same as on the server), otherwise "No name"
private func personName(_ p: JSON) -> String {
    if let n = p["name"].string, !n.isEmpty { return n }
    return p["aliases"].array.compactMap(\.string).first { !$0.isEmpty } ?? L("Без имени")
}

/// Cards as rows on the left (name, last seen, voice), a card with editing, facts and merging on the right
struct PeopleView: View {
    @EnvironmentObject var model: AppModel
    @State private var lastRaw = JSON(nil)
    @State private var rows: [JSON] = []
    @State private var loaded = false
    @State private var failed: String? = nil
    @State private var selected: String? = nil
    @State private var editing = false   // the open card has an unsaved edit
    @State private var search = ""
    var shown: [JSON] {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return rows }
        return rows.filter { p in
            ([p["name"].str, p["relationship"].str, p["opinion"].str] + p["aliases"].array.map { $0.str } + p["facts"].array.map { $0.str })
                .contains { $0.lowercased().contains(q) }
        }
    }
    var selectedPerson: JSON? { rows.first { $0["id"].str == selected } }

    var body: some View {
        Columns(width: Style.Size.column) {
            ListColumn(title: L("Люди"), count: rows.count, loaded: loaded, failed: failed, empty: rows.isEmpty ? L("Пока никого") : L("Никого не нашлось"),
                       emptyIcon: "person.2", ids: shown.map { $0["id"].str }) {
                ForEach(shown, id: \.["id"].str) { p in
                    PersonRow(person: p, selected: selected == p["id"].str) { choose(p["id"].str) }
                        .transition(Style.Motion.remove)
                }
            }
        } right: {
            if let p = selectedPerson {
                PersonDetail(person: p, others: rows, dirty: $editing, reload: { await load() }, select: { selected = $0; editing = false }).id(p["id"].str)
            } else {
                Empty(title: L("Выбери человека"), icon: "person.2")
            }
        }
        .task { while !Task.isCancelled { await load(); try? await Task.sleep(nanoseconds: 15_000_000_000) } }
        .onChange(of: model.focus) { pick() }
        .actions { HeaderSearch(prompt: L("Поиск по людям"), text: $search) }
    }

    private func choose(_ id: String) {
        guard id != selected, confirmDiscard(editing) else { return }
        editing = false
        selected = id
    }
    /// A search result points at a person: their card opens (once the list is here)
    private func pick() {
        guard loaded, let id = model.focus, rows.contains(where: { $0["id"].str == id }) else { return }
        _ = model.take()
        search = ""
        choose(id)
    }
    func load() async {
        do {
            let r = try await model.backend.get("people")
            failed = nil; loaded = true
            if !r.isSame(lastRaw) { lastRaw = r; rows = r.array }
            if selected == nil || !rows.contains(where: { $0["id"].str == selected }) { selected = rows.first?["id"].string; editing = false }
            pick()
        } catch { if !Task.isCancelled && !loaded { failed = error.localizedDescription } }
    }
}

private struct PersonRow: View {
    let person: JSON
    let selected: Bool
    let select: () -> Void
    var subtitle: String {
        if !person["relationship"].str.isEmpty { return person["relationship"].str }
        let aliases = person["aliases"].array.map { $0.str }.joined(separator: ", ")
        if !aliases.isEmpty { return aliases }
        let facts = person["facts"].array.count   // the current ones: the server sends them as text, a retired one stays in the card's history
        return facts > 0 ? "\(facts) \(Fmt.plural(facts, "факт", "факта", "фактов"))" : L("пока ничего не известно")
    }
    var body: some View {
        let name = personName(person)
        RecordCard(icon: "person.fill", tint: Style.Colors.tint(name), title: name, lines: 1, selected: selected, action: select, meta: {
            if person["voices"].int > 0 { Glyph("waveform").font(Style.Icon.sub).foregroundStyle(Style.Colors.secondaryText).help(L("узнаёт по голосу")) }
            Text(subtitle).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(1)
        }, trailing: { _ in
            if person["last_seen"].dbl > 0 { Text(Fmt.ago(person["last_seen"].dbl)).font(Style.Text.caption).foregroundStyle(Style.Colors.tertiaryText) }
        })
    }
}

/// The card's fields, edited by hand; aliases — the other names as one comma-separated line (the server takes the whole list)
private struct PersonFields: Equatable {
    var name = "", rel = "", opinion = "", aliases = ""
    init(_ p: JSON) { name = p["name"].str; rel = p["relationship"].str; opinion = p["opinion"].str; aliases = p["aliases"].array.map { $0.str }.joined(separator: ", ") }
    var aliasList: [String] { aliases.split(separator: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty } }
}

/// A person's card: who they are and how they're seen, field editing, facts, merging with another card
private struct PersonDetail: View {
    @EnvironmentObject var model: AppModel
    let person: JSON
    let others: [JSON]
    @Binding var dirty: Bool
    let reload: () async -> Void
    let select: (String) -> Void
    @State private var form: PersonFields
    @State private var base: PersonFields   // what's currently on the server: while the form hasn't been touched, it follows this (polling brings fresh data)
    @State private var fact = ""
    @State private var merge = ""
    @State private var saving = false
    @State private var adding = false
    init(person: JSON, others: [JSON], dirty: Binding<Bool>, reload: @escaping () async -> Void, select: @escaping (String) -> Void) {
        self.person = person; self.others = others; _dirty = dirty; self.reload = reload; self.select = select
        _form = State(initialValue: PersonFields(person)); _base = State(initialValue: PersonFields(person))
    }
    var current: PersonFields { PersonFields(person) }
    var changed: Bool { form != base }

    var body: some View {
        ScrollView {
            VStack(spacing: Style.Space.xl) {
                card
                factsBlock
                mergeBlock
            }
        }
        .onChange(of: current) { _, new in if form == base { form = new }; base = new }
        .onChange(of: changed) { _, c in dirty = c; model.setUnsaved("person", c) }
        .onDisappear { dirty = false; model.setUnsaved("person", false) }
    }

    var card: some View {
        let voices = person["voices"].int
        return Block(L("Карточка"), lead: true, content: {
            Text(personName(person)).font(Style.Text.display)
            KV(rows: [(L("Последний раз"), person["last_seen"].dbl > 0 ? "\(Fmt.when(person["last_seen"])) · \(Fmt.ago(person["last_seen"].dbl))" : "—"),
                      (L("Добавлен"), Fmt.when(person["created"])),
                      (L("Голос"), voices > 0 ? "\(voices) \(Fmt.plural(voices, "отпечаток", "отпечатка", "отпечатков"))" : L("не слышала"))])
            Grid(alignment: .leading, horizontalSpacing: Style.Space.xl, verticalSpacing: Style.Space.m) {
                GridRow { fieldLabel(L("Имя")); TextField(L("Как ты его зовёшь"), text: $form.name) }
                GridRow { fieldLabel(L("Отношения")); TextField(L("Кто он тебе"), text: $form.rel) }
                GridRow { fieldLabel(L("Мнение Сены")); TextField(L("Что Сена о нём думает"), text: $form.opinion, axis: .vertical).lineLimit(2...4) }
                GridRow { fieldLabel(L("Другие имена")); TextField(L("Через запятую: имя в чате, как назвали голосом"), text: $form.aliases) }
            }
            .padding(.top, Style.Space.xs)
        }, trailing: {
            EditActions(dirty: changed, busy: saving, save: save)
        })
        .motion(value: changed)
    }

    var factsBlock: some View {
        let facts = person["facts"].array.map { $0.str }   // what holds now, as text (desk.people_view): no cap, an old fact is never pushed out
        return Block(L("Факты"), content: {
            if facts.isEmpty {
                Dash()
            } else {
                VStack(spacing: Style.Space.s) {
                    ForEach(Array(facts.enumerated()), id: \.offset) { _, f in
                        RecordCard(icon: "text.quote", tint: Style.Colors.tint(f), title: f, lines: ALL_LINES, reserve: false, meta: { EmptyView() }) { hover in
                            Button { retire(f) } label: {   // a fact that no longer holds: it leaves the card, its history keeps it
                                Glyph("trash").font(Style.Icon.inline).foregroundStyle(Style.Colors.secondaryText)
                                    .frame(width: Style.Size.controlHeight, height: Style.Size.controlHeight).contentShape(Rectangle())
                            }
                            .buttonStyle(.plain).help(L("Убрать факт")).accessibilityLabel(L("Убрать факт")).opacity(hover ? 1 : 0).allowsHitTesting(hover)
                        }
                    }
                }
            }
            HStack(spacing: Style.Space.m) {
                TextField(L("Новый факт"), text: $fact).onSubmit(addFact)
                Button(L("Добавить")) { addFact() }.disabled(fact.trimmingCharacters(in: .whitespaces).isEmpty || adding)
            }
        }, trailing: { listCount("\(facts.count)") })
    }

    var mergeBlock: some View {
        let candidates = others.filter { $0["id"].str != person["id"].str }
        return Block(L("Слияние"), content: {
            if candidates.isEmpty {
                Text(L("Других карточек нет")).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
            } else {
                HStack(spacing: Style.Space.m) {
                    Select(selection: $merge, options: [("", L("Выбери карточку"))] + candidates.map { ($0["id"].str, candidateLabel($0)) })
                    Button(L("Слить"), role: .destructive) { confirmMerge() }.disabled(merge.isEmpty)
                }
            }
        })
    }

    /// A card's label in the merge list: for two "Johns" (voice and chat), you can tell which is which
    private func candidateLabel(_ p: JSON) -> String {
        var parts = [personName(p)]
        if p["voices"].int > 0 { parts.append(L("голос")) }
        if p["last_seen"].dbl > 0 { parts.append(Fmt.ago(p["last_seen"].dbl)) }
        return parts.joined(separator: " · ")
    }

    func save() {
        let id = person["id"].str
        let before = form
        var sent = form   // the server stores it trimmed: we send and show the same, otherwise "edited" wouldn't clear because of a trailing space
        sent.name = sent.name.trimmingCharacters(in: .whitespacesAndNewlines); sent.rel = sent.rel.trimmingCharacters(in: .whitespacesAndNewlines); sent.opinion = sent.opinion.trimmingCharacters(in: .whitespacesAndNewlines)
        sent.aliases = sent.aliasList.joined(separator: ", ")
        saving = true
        var body: [String: Any] = ["id": id, "name": sent.name, "relationship": sent.rel, "opinion": sent.opinion]
        if sent.aliasList != base.aliasList { body["aliases"] = sent.aliasList }   // the whole list as he left it (desk._person); untouched — not sent
        model.runAction(L("Карточка"), "person", body,
                        done: { _ in saving = false; if form == before { form = sent }; base = sent; Task { await reload() } },
                        fail: { saving = false })
    }
    func retire(_ f: String) {
        guard confirm(L("Убрать факт?"), f + "\n" + L("Он останется в истории карточки."), button: L("Убрать"), destructive: true) else { return }
        model.runAction(L("Факт"), "person", ["id": person["id"].str, "retire": [f]], done: { _ in Task { await reload() } })
    }
    func addFact() {
        let f = fact.trimmingCharacters(in: .whitespaces), id = person["id"].str
        guard !f.isEmpty, !adding else { return }
        adding = true; fact = ""   // the field clears right away, and on error the fact is put back: a repeated tap won't create a duplicate
        model.runAction(L("Факт"), "person", ["id": id, "facts": [f]],
                        done: { _ in adding = false; Task { await reload() } },
                        fail: { adding = false; if fact.isEmpty { fact = f } })
    }
    func confirmMerge() {
        guard let other = others.first(where: { $0["id"].str == merge }) else { return }
        let keep = merge, drop = person["id"].str, into = personName(other)   // ids are captured before the prompt: the selection shouldn't change while the dialog is open
        guard confirm(String(format: L("Слить «%@» с «%@»?"), personName(person), into), String(format: L("Имена, голос и факты перейдут в «%@», эта карточка исчезнет. Отменить нельзя."), into), button: L("Слить"), destructive: true) else { return }
        model.runAction(L("Слияние"), "person_merge", ["keep": keep, "drop": drop], done: { _ in Task { await reload(); select(keep) } })
    }
}

// ---- Skills ----

/// A list on the left (name, when to apply, a "code" tag), an editor on the right: when, recipe, code
struct SkillsView: View {
    @EnvironmentObject var model: AppModel
    @State private var list: [JSON] = []
    @State private var lastRaw = JSON(nil)
    @State private var listLoaded = false
    @State private var failed: String? = nil
    @State private var selected: String? = nil
    @State private var reopen = 0           // the re-read counter for the same skill: after saving and an outside edit
    @State private var phase = OpenPhase.idle
    @State private var loaded = JSON(nil)   // the skill as it sits on the server
    @State private var isNew = false
    @State private var saving = false
    @State private var search = ""
    @State private var name = ""
    @State private var when = ""
    @State private var recipe = ""
    @State private var code = ""
    @State private var showCode = false
    var shown: [JSON] {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return list }
        return list.filter { $0["name"].str.lowercased().contains(q) || $0["when"].str.lowercased().contains(q) }
    }
    /// The editor holds exactly the selected skill (not the previous one, while the new one is still loading)
    var ready: Bool { !isNew && phase == .ready && loaded["name"].str == selected }
    var dirty: Bool {
        isNew ? !(name.isEmpty && when.isEmpty && recipe.isEmpty && code.isEmpty)
              : ready && (when != loaded["when"].str || recipe != loaded["recipe"].str || code != loaded["code"].str)
    }
    var canSave: Bool { ![name, when, recipe].contains { $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty } }
    var serverRow: JSON? { list.first { $0["name"].str == selected } }
    /// The skill is open, but it's no longer in the list (removed elsewhere): saving would re-create it
    var gone: Bool { ready && listLoaded && serverRow == nil }
    /// The skill's file was changed while it's open
    var changedElsewhere: Bool { ready && (serverRow?["updated"].dbl ?? 0) > loaded["updated"].dbl + 0.001 }

    var body: some View {
        Columns(width: Style.Size.column) {
            ListColumn(title: L("Навыки"), count: list.count, loaded: listLoaded, failed: failed, empty: list.isEmpty ? L("Навыков пока нет") : L("Ничего не нашлось"),
                       emptyIcon: "wand.and.stars", ids: shown.map { $0["name"].str }) {
                ForEach(shown, id: \.["name"].str) { s in
                    SkillRow(skill: s, selected: !isNew && selected == s["name"].str) { choose(s["name"].str) }
                        .transition(Style.Motion.remove)
                }
            }
        } right: {
            pane
        }
        .task { while !Task.isCancelled { await load(); try? await Task.sleep(nanoseconds: 15_000_000_000) } }
        .task(id: "\(isNew)|\(selected ?? "")|\(reopen)") { await open() }   // changing the selection cancels reading the previous skill: a late reply won't overwrite the editor
        .onChange(of: model.focus) { pick() }
        .onChange(of: dirty) { _, d in model.setUnsaved("skill", d) }
        .onDisappear { model.setUnsaved("skill", false) }
        .actions {
            HeaderSearch(prompt: L("Поиск по навыкам"), text: $search)
            Button(L("Новый навык")) { startNew() }.buttonStyle(.primary)
        }
    }

    @ViewBuilder private var pane: some View {
        if isNew {
            editor
        } else if selected == nil {
            Empty(title: L("Выбери навык"), icon: "wand.and.stars")
        } else if ready {
            editor
        } else if case .failed(let message) = phase {
            loadFailed(message, retry: { reopen += 1 })
        } else {
            Loading()
        }
    }

    var editor: some View {
        VStack(spacing: Style.Space.xl) {
            head
            Block(L("Рецепт"), content: {
                TextEditor(text: $recipe).font(Style.Text.body).memoryEditor()
            })
            .frame(maxHeight: .infinity)
            codeBlock
        }
        .motion(value: showCode)
        .motion(value: isNew)
    }

    var head: some View {
        Block(isNew ? L("Новый навык") : L("Навык"), lead: true, content: {
            Grid(alignment: .leading, horizontalSpacing: Style.Space.xl, verticalSpacing: Style.Space.m) {
                GridRow {
                    fieldLabel(L("Имя"))
                    if isNew { TextField(L("Название латиницей через дефис, например mac-volume"), text: $name) }
                    else { Text(name).font(Style.Text.heading).textSelection(.enabled) }
                }
                GridRow { fieldLabel(L("Когда применять")); TextField(L("Одна строка: в каких случаях применять"), text: $when) }
            }
            if !isNew, loaded["updated"].dbl > 0 {
                Text(String(format: L("изменён %@ · %@"), Fmt.when(loaded["updated"]), Fmt.bytes(loaded["size"].dbl))).font(Style.Text.caption).foregroundStyle(Style.Colors.tertiaryText)
            }
        }, trailing: {
            if gone { Tag(text: L("уже удалено"), color: Style.Colors.warn) }
            else if changedElsewhere { Tag(text: L("изменено в другом месте"), color: Style.Colors.warn) }
            if !isNew { Button(L("Удалить"), role: .destructive) { confirmDelete() } }
            EditActions(dirty: dirty, busy: saving, canSave: canSave, hint: L("Заполни имя, «когда применять» и рецепт"), save: save)
        })
    }

    var codeBlock: some View {
        Block(L("Код"), content: {
            if showCode {
                TextEditor(text: $code).font(Style.Text.mono).memoryEditor().transition(Style.Motion.appear)
            } else {
                Text(code.isEmpty ? "—" : lines(lineCount(code)))
                    .font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
            }
        }, trailing: {
            Button(showCode ? L("Скрыть") : code.isEmpty ? L("Добавить") : L("Показать")) { showCode.toggle() }
        })
        .frame(maxHeight: showCode ? CGFloat.infinity : nil)
    }

    /// A skill's name on the server: letters, digits, hyphens (like skills._slug); needed to select a just-created skill in the list
    func slug(_ s: String) -> String {
        var out = ""; var dash = false
        for ch in s.trimmingCharacters(in: .whitespaces).lowercased() {
            if ch.isLetter || ch.isNumber || ch == "_" || ch == "-" { out.append(ch); dash = false }
            else if !dash { out.append("-"); dash = true }
        }
        return out.trimmingCharacters(in: CharacterSet(charactersIn: "-"))
    }

    func load() async {
        do {
            let r = try await model.backend.get("skills")
            failed = nil; listLoaded = true
            if !r.isSame(lastRaw) { lastRaw = r; list = r.array }
            if changedElsewhere && !dirty { reopen += 1 }   // someone else edited the skill and we have no edits of our own: pull it in
            pick()
        } catch { if !Task.isCancelled && !listLoaded { failed = error.localizedDescription } }
    }
    /// A search result points at a skill: it opens (once the list is here)
    private func pick() {
        guard listLoaded, let n = model.focus, list.contains(where: { $0["name"].str == n }) else { return }
        _ = model.take()
        search = ""
        choose(n)
    }
    func open() async {
        guard !isNew, let n = selected else { return }
        let first = loaded["name"].str != n   // a different skill (not a re-read of the same one)
        if first { phase = .loading }
        do {
            let s = try await model.backend.get("skills", ["name": n])
            guard !Task.isCancelled else { return }
            if first || !dirty {   // a re-read doesn't overwrite what's typed
                name = s["name"].str; when = s["when"].str; recipe = s["recipe"].str; code = s["code"].str
                if first { showCode = !code.isEmpty }
            }
            loaded = s
            phase = .ready
        } catch {
            guard !Task.isCancelled else { return }
            loaded = JSON(nil); name = ""; when = ""; recipe = ""; code = ""   // the previous skill's fields don't linger under a different name
            phase = .failed(error.localizedDescription)
        }
    }
    func choose(_ n: String) {
        guard isNew || selected != n, confirmDiscard(dirty) else { return }
        isNew = false
        selected = n
    }
    func startNew() {
        guard confirmDiscard(dirty) else { return }
        isNew = true; loaded = JSON(nil); phase = .idle; name = ""; when = ""; recipe = ""; code = ""; showCode = false
    }
    func save() {
        let n = isNew ? slug(name) : (selected ?? name)
        if isNew, list.contains(where: { $0["name"].str == n }) {   // can't silently overwrite someone else's skill
            guard confirm(String(format: L("Навык «%@» уже есть"), n), L("Его рецепт и код заменятся новыми. Отменить нельзя."), button: L("Заменить"), destructive: true) else { return }
        } else if changedElsewhere {
            guard confirm(L("Навык изменили в другом месте"), L("Твоя правка заменит его целиком."), button: L("Заменить"), destructive: true) else { return }
        }
        saving = true
        let w = when.trimmingCharacters(in: .whitespacesAndNewlines), rc = recipe.trimmingCharacters(in: .whitespacesAndNewlines), cd = code.trimmingCharacters(in: .whitespacesAndNewlines)
        let before = (when, recipe, code)   // the server stores it trimmed: we show the same after saving, otherwise "edited" wouldn't clear because of a trailing space
        model.runAction(L("Навык"), "skill_write", ["name": n, "when": w, "recipe": rc, "code": cd], done: { r in
            saving = false
            if (when, recipe, code) == before { when = w; recipe = rc; code = cd }
            isNew = false; selected = n; reopen += 1
            Task { await load() }
        }, fail: { saving = false })
    }
    func confirmDelete() {
        guard !isNew, let n = selected else { return }
        guard confirm(String(format: L("Удалить навык «%@»?"), n), L("Рецепт и код пропадут. Отменить нельзя."), button: L("Удалить"), destructive: true) else { return }
        model.runAction(L("Удаление"), "skill_delete", ["name": n], done: { _ in
            if selected == n { selected = nil; loaded = JSON(nil); phase = .idle }
            Task { await load() }
        })
    }
}

private struct SkillRow: View {
    let skill: JSON
    let selected: Bool
    let select: () -> Void
    var body: some View {
        RecordCard(icon: "wand.and.stars", tint: Style.Colors.model, title: skill["name"].str, lines: 1, selected: selected, action: select, meta: {
            if skill["has_code"].bool { Tag(text: L("код"), color: Style.Colors.info) }
            Text(skill["when"].str).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(1)
        }, trailing: { _ in
            if skill["updated"].dbl > 0 { Text(Fmt.ago(skill["updated"].dbl)).font(Style.Text.caption).foregroundStyle(Style.Colors.tertiaryText) }
        })
    }
}

// ---- Notes ----

/// A folder picker and search in the header, note rows on the left, text with saving on the right
struct NotesView: View {
    @Environment(\.narrowWindow) private var narrow
    @EnvironmentObject var model: AppModel
    @State private var data = JSON(nil)
    @State private var failed: String? = nil
    @State private var selected: String? = nil
    @State private var shownPath = ""       // whose note is currently in the editor
    @State private var reopen = 0           // the re-read counter for the same note: after saving and an outside edit
    @State private var phase = OpenPhase.idle
    @State private var head = ""            // Sena's automatic block together with its end marker: shown, but not editable
    @State private var text = ""
    @State private var saved = ""
    @State private var baseUpdated = 0.0    // when the file last changed, at the moment it was opened
    @State private var saving = false
    @State private var search = ""
    @State private var folder = ""
    var notes: [JSON] { data["notes"].array }
    /// The editor holds exactly the selected note (not the previous one, while the new one is still loading)
    var ready: Bool { phase == .ready && shownPath == selected }
    var dirty: Bool { ready && text != saved }
    var current: JSON? { notes.first { $0["path"].str == shownPath } }
    /// The file was changed elsewhere (in Obsidian) while it's open; search results have no modification time in their rows, so the check stays quiet
    var changedElsewhere: Bool { ready && baseUpdated > 0 && (current?["updated"].dbl ?? 0) > baseUpdated + 0.001 }

    var body: some View {
        Columns(width: Style.Size.column) {
            ListColumn(title: L("Заметки"), count: notes.count, loaded: data.exists, failed: failed, empty: search.isEmpty ? L("Заметок нет") : L("Ничего не нашлось"),
                       emptyIcon: "note.text", ids: notes.map { $0["path"].str }) {
                ForEach(notes, id: \.["path"].str) { n in
                    NoteRow(note: n, selected: selected == n["path"].str) { choose(n["path"].str) }
                        .transition(Style.Motion.remove)
                }
            }
        } right: {
            pane
        }
        .task(id: folder + "\u{1F}" + search) {
            if data.exists { try? await Task.sleep(nanoseconds: 300_000_000) }   // search: wait while the word is still being typed
            while !Task.isCancelled { await load(); try? await Task.sleep(nanoseconds: 15_000_000_000) }
        }
        .task(id: "\(selected ?? "")|\(reopen)") { await open() }   // changing the selection cancels reading the previous note: a late reply won't overwrite the editor
        .onChange(of: model.focus) { pick() }
        .onAppear { pick() }
        .onChange(of: dirty) { _, d in model.setUnsaved("note", d) }
        .onDisappear { model.setUnsaved("note", false) }
        .actions {
            Select(selection: $folder, options: [("", L("Все папки"))] + data["folders"].array.map { ($0.str, folderName($0.str)) }, width: narrow ? Style.Size.selectShort : Style.Size.select)
            HeaderSearch(prompt: L("Поиск по заметкам"), text: $search)
        }
    }

    @ViewBuilder private var pane: some View {
        if selected == nil {
            Empty(title: L("Выбери заметку"), icon: "note.text")
        } else if ready {
            VStack(spacing: Style.Space.xl) {
                if !head.isEmpty { FoldedText(title: L("Блок Сены, только чтение"), text: head, empty: "") }
                editor(shownPath)
            }
        } else if case .failed(let message) = phase {
            loadFailed(message, retry: { reopen += 1 })
        } else {
            Loading()
        }
    }

    func editor(_ p: String) -> some View {
        let updated = current?["updated"].dbl ?? 0
        return Block(L("Заметка"), lead: true, content: {
            Text(p).font(Style.Text.small.weight(.medium)).textSelection(.enabled).lineLimit(1)
            TextEditor(text: $text).font(Style.Text.mono).memoryEditor()
            Text(updated > 0 ? String(format: L("%@ · изменена %@"), lines(lineCount(text)), Fmt.when(JSON(updated))) : lines(lineCount(text)))
                .font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
        }, trailing: {
            if changedElsewhere { Tag(text: L("изменено в другом месте"), color: Style.Colors.warn) }
            Button(L("Показать в Finder")) { reveal(p) }.disabled(data["root"].str.isEmpty)
            EditActions(dirty: dirty, busy: saving, reset: { text = saved }, save: { save(p) })
        })
        .frame(maxHeight: .infinity)
        .motion(value: dirty)
    }

    func choose(_ p: String) {
        guard p != selected, confirmDiscard(dirty) else { return }
        selected = p
    }
    /// A search result points at a note (an entity's too: a project, an organization, a place): it opens, whatever the filter
    private func pick() {
        guard let p = model.focus, !p.isEmpty else { return }
        _ = model.take()
        folder = ""; search = ""
        choose(p)
    }
    func load() async {
        do {
            let r = try await model.backend.get("notes", ["folder": folder, "q": search.trimmingCharacters(in: .whitespaces)])
            failed = nil
            if !data.isSame(r) { data = r }
            if changedElsewhere && !dirty { reopen += 1 }   // the file was edited outside and we have no edits of our own: pull it in
        } catch { if !Task.isCancelled && !data.exists { failed = error.localizedDescription } }
    }
    func open() async {
        guard let p = selected else { return }
        let first = shownPath != p   // a different note (not a re-read of the same one)
        if first { phase = .loading }
        do {
            let n = try await model.backend.api("/notes/read?path=" + Backend.enc(p))
            guard !Task.isCancelled else { return }
            let full = n["text"].str
            // like vault.write_note: the automatic block exists only when both markers are present; then the server only changes what's after it
            var mine = full, auto = ""
            if full.contains(NOTE_BEGIN), let r = full.range(of: NOTE_END) {
                auto = String(full[..<r.upperBound])
                mine = String(full[r.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
            }
            if first || !dirty {   // a re-read doesn't overwrite what's typed
                head = auto; text = mine
            }
            saved = mine
            shownPath = p
            baseUpdated = current?["updated"].dbl ?? 0
            phase = .ready
        } catch {
            guard !Task.isCancelled else { return }
            head = ""; text = ""; saved = ""; shownPath = ""   // the previous note's text doesn't linger under a new title
            phase = .failed(error.localizedDescription)
        }
    }
    func save(_ p: String) {
        if changedElsewhere {
            guard confirm(L("Заметку изменили в другом месте"), L("Твоя правка заменит её целиком."), button: L("Заменить"), destructive: true) else { return }
        }
        let body = text
        saving = true
        model.runAction(L("Заметка"), "note_write", ["path": p, "text": body, "mode": "replace"], done: { r in
            saving = false
            guard r["result"].str.hasPrefix("Заменен") else { return }   // it wasn't written (e.g. too long): the edits stay in the editor
            saved = body
            Task { await load(); reopen += 1 }
        }, fail: { saving = false })
    }
    func reveal(_ p: String) {
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: data["root"].str).appendingPathComponent(p)])
    }
}

private struct NoteRow: View {
    let note: JSON
    let selected: Bool
    let select: () -> Void
    var body: some View {
        let folder = note["folder"].str
        RecordCard(icon: "doc.text", tint: Style.Colors.tint(folder.isEmpty ? note["title"].str : folder), title: note["title"].str, lines: 1, selected: selected, action: select, meta: {
            if !folder.isEmpty { Tag(text: folderName(folder)) }
            Text(note["snippet"].string ?? note["preview"].str).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(1)
        }, trailing: { _ in
            if note["updated"].dbl > 0 { Text(Fmt.ago(note["updated"].dbl)).font(Style.Text.caption).foregroundStyle(Style.Colors.tertiaryText) }
        })
    }
}
