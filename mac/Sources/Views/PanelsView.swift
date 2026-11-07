// "Sena keeps": panels the server describes (sena/phone/panels.py) — what waits for a "yes", the owner's long goals, the agents' own
// memory, the exam of her mind. The app has no logic of its own here: it draws the blocks, sends the owner's action with what he typed,
// and draws the panel the server returns. A new thing Sena can do with a panel appears here without a new version of the app.
import SwiftUI

/// One value an action asks for before it goes (a note, a corrected lesson): shown in a sheet
private struct PendingInput: Identifiable {
    let action: JSON
    var id: String { action["id"].str + action["args"].dict.keys.sorted().joined() }
}

struct PanelsView: View {
    @EnvironmentObject var model: AppModel
    @State private var list: [JSON] = []
    @State private var selected = ""
    @State private var panel = JSON(nil)
    @State private var loaded = false
    @State private var failed: String? = nil
    @State private var busy: String? = nil
    @State private var input: PendingInput? = nil
    @State private var forms: [String: [String: String]] = [:]   // what the owner is typing in each form, by form id

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Style.Space.xl) {
                if !loaded {
                    if let f = failed { loadFailed(f) { Task { await load() } } } else { SkeletonList(rows: 5) }
                } else if !panel["subtitle"].str.isEmpty {
                    Text(panel["subtitle"].str).font(Style.Text.body).foregroundStyle(Style.Colors.secondaryText)
                }
                ForEach(Array(groups().enumerated()), id: \.offset) { _, group in
                    Block(group.title) { ForEach(Array(group.blocks.enumerated()), id: \.offset) { _, b in block(b) } }
                }
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
        .task { while !Task.isCancelled { await load(); try? await Task.sleep(nanoseconds: 15_000_000_000) } }
        .onChange(of: model.focus) { if model.focus != nil { Task { await load() } } }   // a search result points at one of the panels
        .sheet(item: $input) { pending in InputSheet(action: pending.action) { value in act(pending.action, values: ["value": value]) } }
        .actions {
            if list.count > 1 {
                Segments(selection: Binding(get: { selected }, set: { id in selected = id; Task { await load() } }),
                         options: list.map { ($0["id"].str, $0["title"].str + ($0["badge"].int > 0 ? " · \($0["badge"].int)" : "")) }, collapses: true)
            }
        }
    }

    /// A "section" block starts a new Block with its title; everything before the first one goes into a Block without a title
    private func groups() -> [(title: String?, blocks: [JSON])] {
        var out: [(title: String?, blocks: [JSON])] = []
        for b in panel["blocks"].array {
            if b["type"].str == "section" { out.append((b["title"].str, [])); continue }
            if out.isEmpty { out.append((nil, [])) }
            out[out.count - 1].blocks.append(b)
        }
        return out.filter { !$0.blocks.isEmpty }
    }

    @ViewBuilder private func block(_ b: JSON) -> some View {
        switch b["type"].str {
        case "stats":
            HStack(spacing: Style.Space.gap) { ForEach(Array(b["items"].array.enumerated()), id: \.offset) { _, s in Stat(value: s["value"].str, label: s["label"].str, bad: s["tone"].str == "bad") } }
        case "text":
            Text(b["text"].str).font(Style.Text.body).foregroundStyle(tone(b["tone"].str))
        case "items":
            if b["items"].array.isEmpty {
                if !b["empty"].str.isEmpty { Text(b["empty"].str).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText) }
            } else {
                VStack(spacing: Style.Space.s) { ForEach(Array(b["items"].array.enumerated()), id: \.offset) { _, item in row(item) } }
            }
        case "form":
            form(b)
        case "switch":
            Toggle(b["label"].str, isOn: Binding(get: { b["value"].bool }, set: { on in act(b["action"], values: ["value": on]) })).font(Style.Text.body)
        case "buttons":
            HStack(spacing: Style.Space.m) { ForEach(Array(b["items"].array.enumerated()), id: \.offset) { _, a in button(a, primary: true) }; Spacer(minLength: 0) }
        default:
            EmptyView()   // a block this version can't draw: skipped, the rest of the panel stays usable
        }
    }

    private func row(_ item: JSON) -> some View {
        VStack(alignment: .leading, spacing: Style.Space.s) {
            HStack(alignment: .firstTextBaseline, spacing: Style.Space.s) {
                Text(item["title"].str).font(Style.Text.body.weight(.medium)).foregroundStyle(tone(item["tone"].str, plain: true)).lineLimit(4)
                Spacer(minLength: 0)
                if !item["meta"].str.isEmpty { Text(item["meta"].str).font(Style.Text.caption).foregroundStyle(Style.Colors.tertiaryText).lineLimit(1) }
            }
            if !item["subtitle"].str.isEmpty { Text(item["subtitle"].str).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText).lineLimit(4) }
            if let p = item["progress"].double { Bar(value: p / 100) }
            if !item["actions"].array.isEmpty {
                HStack(spacing: Style.Space.s) { ForEach(Array(item["actions"].array.enumerated()), id: \.offset) { _, a in button(a, primary: false) }; Spacer(minLength: 0) }
            }
        }
        .row()
    }

    private func form(_ f: JSON) -> some View {
        let id = f["id"].str
        let fields = f["fields"].array
        let value = { (name: String, fallback: String) -> Binding<String> in
            Binding(get: { forms[id]?[name] ?? fallback }, set: { forms[id, default: [:]][name] = $0 })
        }
        let first = fields.first.map { forms[id]?[$0["name"].str] ?? $0["value"].text } ?? ""
        return VStack(alignment: .leading, spacing: Style.Space.m) {
            if !f["title"].str.isEmpty { Text(f["title"].str).font(Style.Text.heading) }
            ForEach(Array(fields.enumerated()), id: \.offset) { _, field in
                let name = field["name"].str
                switch field["kind"].str {   // a default may be a number (field["value"].text): a number field isn't left empty
                case "choice":
                    Segments(selection: value(name, field["value"].text), options: field["options"].array.map { ($0["value"].str, $0["label"].str) })
                case "multiline":
                    TextField(field["label"].str, text: value(name, field["value"].text), prompt: Text(field["placeholder"].string ?? field["label"].str), axis: .vertical).lineLimit(3...8)
                default:
                    TextField(field["label"].str, text: value(name, field["value"].text), prompt: Text(field["placeholder"].string ?? field["label"].str))
                }
            }
            HStack {
                Spacer()
                Button(f["submit"]["label"].str) {
                    var values: [String: Any] = [:]
                    for field in fields { values[field["name"].str] = forms[id]?[field["name"].str] ?? field["value"].text }
                    act(f["submit"], values: values) { forms[id] = nil }   // done (a refusal keeps what was typed): the form starts clean
                }
                .buttonStyle(.primary).disabled(first.trimmingCharacters(in: .whitespaces).isEmpty || busy != nil)
            }
        }
    }

    private func button(_ a: JSON, primary: Bool) -> some View {
        Button(a["label"].str, role: a["danger"].bool ? .destructive : nil) { run(a) }
            .buttonStyle(primary ? .primary : .quiet)
            .disabled(busy != nil)
    }

    private func run(_ a: JSON) {
        if !a["input"].dict.isEmpty { input = PendingInput(action: a); return }
        if let question = a["confirm"].string, !confirm(a["label"].str, question, button: a["label"].str, destructive: a["danger"].bool) { return }
        act(a)
    }

    /// The action belongs to the panel drawn now (panel["id"]), not to the one just picked in the header and still loading
    private func act(_ a: JSON, values: [String: Any] = [:], then: (() -> Void)? = nil) {
        busy = a["id"].str
        let pid = panel["id"].str
        let body: [String: Any] = ["panel": pid, "action": a["id"].str, "args": a["args"].raw ?? [String: Any](), "values": values]
        model.runAction(panel["title"].str, "panel_act", body, done: { r in
            busy = nil
            if pid == selected { panel = r["panel"] }   // the server's answer is the panel as it is now — unless another one was picked meanwhile
            then?()
            Task { await model.pollPanels() }   // the menu's counter follows at once
        }, fail: { busy = nil })
    }

    private func tone(_ t: String, plain: Bool = false) -> Color {
        switch t {
        case "bad": return Style.Colors.badText
        case "warn": return Style.Colors.warnText
        case "muted": return Style.Colors.secondaryText
        default: return plain ? Style.Colors.ink : Style.Colors.secondaryText
        }
    }

    private func load() async {
        do {
            let l = try await model.backend.get("panels")
            list = l["items"].array
            if let f = model.focus, list.contains(where: { $0["id"].str == f }) { _ = model.take(); selected = f }   // a found goal or lesson
            if selected.isEmpty || !list.contains(where: { $0["id"].str == selected }) {
                selected = (list.first { $0["badge"].int > 0 } ?? list.first)?["id"].str ?? ""
            }
            guard !selected.isEmpty else { return }
            let want = selected
            let p = try await model.backend.get("panel", ["id": want])
            guard want == selected else { return }   // another panel was picked while this one loaded: a late reply isn't drawn over it
            panel = p
            failed = nil; loaded = true
        } catch { if !Task.isCancelled && !loaded { failed = error.localizedDescription } }
    }
}

/// The one value an action needs (how a goal is going, a corrected lesson). optional — an empty answer is a real one too
/// (resume a paused work without a word, clear the list of tools that may run without a "yes")
private struct InputSheet: View {
    @Environment(\.dismiss) private var dismiss
    let action: JSON
    let save: (String) -> Void
    @State private var text: String
    init(action: JSON, save: @escaping (String) -> Void) {
        self.action = action; self.save = save
        _text = State(initialValue: action["input"]["value"].text)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.xl) {
            Text(action["label"].str).font(Style.Text.title)
            if action["input"]["kind"].str == "multiline" {
                TextField(action["input"]["label"].str, text: $text, axis: .vertical).lineLimit(3...8)
            } else {
                TextField(action["input"]["label"].str, text: $text)
            }
            HStack(spacing: Style.Space.m) {
                Spacer()
                Button(L("Отмена")) { dismiss() }.buttonStyle(.secondary).keyboardShortcut(.cancelAction)
                Button(L("Сохранить")) { save(text); dismiss() }.buttonStyle(.primary).keyboardShortcut(.defaultAction)
                    .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !action["input"]["optional"].bool)
            }
        }
        .sheetStyle()
        .padding(Style.Space.xxl).frame(width: Style.Size.sheet)
    }
}
