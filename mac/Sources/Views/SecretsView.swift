// Secrets: the owner's passwords and codes kept by the server's keeper (core/secrets.py). Values are typed only here, behind the Mac's own
// login; agents get them by rule and every access goes to the journal; the model never sees a value. A list on the left, the card on the right:
// the fields, the value (shown after an identity check), who may use it, when it changed.
import LocalAuthentication
import SwiftUI

/// The card's fields as edited by hand
private struct SecretForm: Equatable {
    var name = "", value = "", note = "", sites = ""
    var allow: Set<String> = []
    var never = false   // reveal rule: never show to the owner either
    init() {}
    init(_ s: JSON) {
        name = s["name"].str; note = s["note"].str; allow = Set(s["allow"].array.map { $0.str }); never = s["reveal"].str == "never"
        sites = s["sites"].array.map { $0.str }.joined(separator: ", ")
    }
    /// The sites a password may be typed on, as the server keeps them (it strips addresses down to their hosts)
    var siteList: [String] { sites.split(whereSeparator: { ",; \n".contains($0) }).map(String.init) }
}

private func fieldLabel(_ s: String) -> some View { Text(s).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText).gridColumnAlignment(.leading) }

struct SecretsView: View {
    @EnvironmentObject var model: AppModel
    @State private var rows: [JSON] = []
    @State private var agents: [String] = []
    @State private var lastRaw = JSON(nil)
    @State private var loaded = false
    @State private var failed: String? = nil
    @State private var selected: String? = nil
    @State private var isNew = false
    @State private var search = ""
    @State private var form = SecretForm()
    @State private var saving = false
    @State private var revealing = false
    @State private var shown: String? = nil   // the value, after the owner proved it's him

    var filtered: [JSON] {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return rows }
        return rows.filter { $0["name"].str.lowercased().contains(q) || $0["note"].str.lowercased().contains(q) }
    }
    var current: JSON? { isNew ? nil : rows.first { $0["name"].str == selected } }
    var dirty: Bool {
        if isNew { return !(form.name.isEmpty && form.value.isEmpty && form.note.isEmpty) }
        guard let c = current else { return false }
        let server = SecretForm(c)
        return !form.value.isEmpty || form.note != server.note || form.allow != server.allow || form.never != server.never || form.siteList != server.siteList
    }
    var canSave: Bool { isNew ? !form.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !form.value.isEmpty : true }

    var body: some View {
        Columns(width: Style.Size.column) {
            listBlock
        } right: {
            pane
        }
        .task { while !Task.isCancelled { await load(); try? await Task.sleep(nanoseconds: 15_000_000_000) } }
        .onChange(of: dirty) { _, d in model.setUnsaved("secret", d) }
        .onDisappear { model.setUnsaved("secret", false) }
        .actions {
            HeaderSearch(prompt: L("Поиск по секретам"), text: $search)
            Button(L("Новый секрет")) { startNew() }.buttonStyle(.primary)
        }
    }

    // ---- the list ----
    private var listBlock: some View {
        Block(L("Секреты"), content: {
            if !loaded {
                if let f = failed { loadFailed(f) } else { SkeletonList(rows: 5) }
            } else if filtered.isEmpty {
                Empty(title: rows.isEmpty ? L("Секретов пока нет") : L("Ничего не нашлось"),
                      text: rows.isEmpty ? L("Пароли и коды вводятся только здесь: в чате их не диктуют.") : "", icon: "key")
            } else {
                ScrollView {
                    VStack(spacing: Style.Space.s) {
                        ForEach(filtered, id: \.["name"].str) { s in row(s).transition(Style.Motion.remove) }
                    }
                    .motion(value: filtered.map { $0["name"].str })
                }
            }
        }, trailing: { listCount("\(rows.count)") })
        .frame(maxHeight: .infinity)
    }

    private func row(_ s: JSON) -> some View {
        let name = s["name"].str
        return RecordCard(icon: "key.fill", tint: Style.Colors.tint(name), title: name, lines: 1, selected: !isNew && selected == name, action: { choose(name) }, meta: {
            if !s["note"].str.isEmpty { Text(s["note"].str).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(1) }
            ForEach(s["allow"].array.map { $0.str }, id: \.self) { Tag(text: AGENT_RU[$0] ?? $0, color: Style.Colors.agent($0)) }
            if s["reveal"].str == "never" { Tag(text: L("не показывать")) }
        }, trailing: { _ in
            if s["t"].dbl > 0 { Text(Fmt.ago(s["t"].dbl)).font(Style.Text.caption).foregroundStyle(Style.Colors.tertiaryText) }
        })
    }

    // ---- the card ----
    @ViewBuilder private var pane: some View {
        if isNew || current != nil {
            ScrollView {
                VStack(spacing: Style.Space.xl) {
                    editor
                    if let c = current {
                        valueBlock(c)
                        about(c)
                    }
                }
            }
        } else {
            Empty(title: L("Выбери секрет или добавь новый"),
                  text: L("Хранится зашифрованным на этом Mac; агенты получают доступ по правилам, каждый доступ — в журнале."), icon: "key")
        }
    }

    private var editor: some View {
        Block(isNew ? L("Новый секрет") : L("Секрет"), lead: true, content: {
            if let c = current { Text(c["name"].str).font(Style.Text.display) }
            Grid(alignment: .leading, horizontalSpacing: Style.Space.xl, verticalSpacing: Style.Space.m) {
                if isNew { GridRow { fieldLabel(L("Имя")); TextField(L("Например: почта, сервер, роутер"), text: $form.name) } }
                GridRow { fieldLabel(L("Значение")); SecureField(isNew ? L("Пароль или ключ, от 8 знаков") : L("Новое значение; пусто — оставить как есть"), text: $form.value) }
                GridRow { fieldLabel(L("Для чего")); TextField(L("Заметка: от чего это и где пригодится"), text: $form.note) }
                GridRow { fieldLabel(L("Где вводить")); TextField(L("Сайты через запятую: hh.ru, mail.google.com; пусто — на любом"), text: $form.sites) }
                GridRow {
                    fieldLabel(L("Кому можно"))
                    HStack(spacing: Style.Space.xl) {
                        ForEach(agents, id: \.self) { a in Toggle(AGENT_RU[a] ?? a, isOn: allowed(a)) }
                        Spacer(minLength: 0)
                    }
                }
                GridRow {
                    fieldLabel(L("Показывать тебе"))
                    Toggle(form.never ? L("никогда") : L("после подтверждения"), isOn: Binding(get: { !form.never }, set: { form.never = !$0 }))
                }
            }
            .font(Style.Text.body)
            .padding(.top, Style.Space.xs)
        }, trailing: {
            if dirty { Tag(text: L("изменено"), color: Style.Colors.warn).transition(Style.Motion.appear) }
            if isNew { Button(L("Отмена")) { cancelNew() } }
            Button(L("Сохранить"), action: save).buttonStyle(.primary).disabled(!dirty || !canSave || saving).keyboardShortcut("s", modifiers: .command)
        })
        .motion(value: dirty)
    }

    private func allowed(_ agent: String) -> Binding<Bool> {
        Binding(get: { form.allow.contains(agent) }, set: { on in if on { form.allow.insert(agent) } else { form.allow.remove(agent) } })
    }

    private func valueBlock(_ c: JSON) -> some View {
        let name = c["name"].str, hidden = c["reveal"].str == "never"
        return Block(L("Значение"), content: {
            if let v = shown {
                HStack(spacing: Style.Space.m) {
                    Text(v).font(Style.Text.mono).textSelection(.enabled).lineLimit(3)
                    Spacer(minLength: Style.Space.m)
                    Button(L("Скопировать")) { model.copy(v, toast: L("Скопировано на минуту"), secret: true) }
                    Button(L("Скрыть")) { shown = nil }
                }
            } else {
                Text(hidden ? L("Помечен «никогда не показывать»: только агенты по правилам.") : L("Показ — с подтверждением личности и записью в журнал."))
                    .font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
            }
        }, trailing: {
            if shown == nil && !hidden { Button(L("Показать")) { reveal(name) }.disabled(revealing) }
            Button(L("Убрать"), role: .destructive) { remove(name) }
        })
        .motion(value: shown != nil)
    }

    private func about(_ c: JSON) -> some View {
        let changed = c["changed"].int
        let who = c["allow"].array.map { AGENT_RU[$0.str] ?? $0.str }.joined(separator: ", ")
        return Block(L("О секрете"), content: {
            KV(rows: [(L("Добавлен или изменён"), Fmt.when(c["t"])),
                      (L("Менялся"), "\(changed) \(Fmt.plural(changed, "раз", "раза", "раз"))"),
                      (L("Кому можно"), who.isEmpty ? L("никому") : who),
                      (L("Где вводить"), c["sites"].array.isEmpty ? L("на любом сайте") : c["sites"].array.map { $0.str }.joined(separator: ", "))])
        })
    }

    // ---- actions ----
    private func leaveOK() -> Bool {
        !dirty || confirm(L("Есть несохранённые правки"), L("Если уйти, они пропадут."), button: L("Уйти без сохранения"), destructive: true)
    }
    private func choose(_ name: String) {
        guard name != selected || isNew, leaveOK() else { return }
        isNew = false; selected = name; shown = nil
        form = rows.first { $0["name"].str == name }.map(SecretForm.init) ?? SecretForm()
    }
    private func startNew() {
        guard leaveOK() else { return }
        isNew = true; selected = nil; shown = nil; form = SecretForm()
    }
    private func cancelNew() {
        guard leaveOK() else { return }
        isNew = false; form = SecretForm()
    }
    private func save() {
        let name = isNew ? form.name.trimmingCharacters(in: .whitespacesAndNewlines) : (selected ?? "")
        let note = form.note.trimmingCharacters(in: .whitespacesAndNewlines)
        var body: [String: Any] = ["name": name, "note": note, "allow": Array(form.allow).sorted(), "reveal": form.never ? "never" : "confirm", "sites": form.siteList]
        if !form.value.isEmpty { body["value"] = form.value }
        saving = true
        model.runAction(L("Секрет"), "secret_put", body,
                        done: { _ in saving = false; form.value = ""; form.note = note; form.name = name; isNew = false; selected = name; shown = nil; Task { await load() } },
                        fail: { saving = false })
    }
    private func remove(_ name: String) {
        guard confirm(String(format: L("Убрать секрет «%@»?"), name), L("Агенты перестанут его получать; прежние значения остаются в истории хранилища."),
                      button: L("Убрать"), destructive: true) else { return }
        model.runAction(L("Секрет"), "secret_remove", ["name": name], done: { _ in selected = nil; shown = nil; form = SecretForm(); Task { await load() } })
    }
    /// The value for the owner's eyes: Touch ID or the Mac's password first (a plain question when the Mac can't ask), then the server, which journals the fact
    private func reveal(_ name: String) {
        revealing = true
        Task { @MainActor in
            defer { revealing = false }
            guard await proveOwner(String(format: L("Показать секрет «%@»"), name)) else { return }
            do { shown = try await model.backend.act("secret_reveal", ["name": name])["value"].str }
            catch { model.say(String(format: L("%@: %@"), L("Секрет"), error.localizedDescription), hold: 7) }
        }
    }
    @MainActor private func proveOwner(_ reason: String) async -> Bool {
        let context = LAContext()
        var unavailable: NSError?
        if context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &unavailable) {
            return (try? await context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason)) ?? false
        }
        return confirm(L("Показать секрет?"), reason, button: L("Показать"))
    }
    private func load() async {
        do {
            let r = try await model.backend.get("secrets")   // names, notes and rules only: no value ever travels here
            failed = nil; loaded = true
            if !r.isSame(lastRaw) { lastRaw = r; rows = r["items"].array; agents = r["agents"].array.map { $0.str } }
            if !isNew, let s = selected, !rows.contains(where: { $0["name"].str == s }) { selected = nil; shown = nil }
        } catch { if !Task.isCancelled && !loaded { failed = error.localizedDescription } }
    }
}
