// Processes: a diagram of what Sena is doing right now. Agents on top (who's doing what), below a lane for each of Tema's
// commands: a command card, with a chain of nodes branching from it — work stages per agent (who, what they did, how many steps, when);
// the current stage is highlighted and "typing". The chain scrolls sideways and jumps to the latest stage on its own. Below the
// running ones — "Recent": the last finished tasks with the same diagram, ending in a "Result" node. Service chores (cleanup, triage)
// show up with a tag but don't appear in "Recent". All diagram cards are the same size with a colored band on top (the agent's color),
// agents go by their own names (codes), Sena is the leaf. Data: model.overview (active tasks and agents, every 2 s) and tasks (finished, every 10 s). Background: a dot grid.
import SwiftUI

private let SHOWN_STAGES = 8        // how many of the latest stages to show in the lane
private let AGENT_TILE: CGFloat = 164   // the narrowest agent tile in the grid at the top
private let NODE_TINT = Style.Colors.tintOpacity / 2                                       // the diagram card's pastel is half as strong as a tile's
private let NODE_SHADOW_RADIUS = Style.Size.shadowRadius / 2, NODE_SHADOW_Y = Style.Size.shadowY / 2   // the diagram card's shadow is half as soft as usual
/// The agent's role description comes from the server in Russian (desk.AGENTS): we show a translation
private let ROLE_RU = ["координатор": L("координатор"), "сайты и покупки": L("сайты и покупки"), "файлы и программы": L("файлы и программы"),
                       "проекты и свой код": L("проекты и свой код"), "после задач": L("после задач"), "ночью": L("ночью"), "фоном": L("фоном")]

/// A stage — consecutive steps by one agent
struct Stage: Identifiable {
    let id: Int
    let who: String
    let steps: [JSON]
    var last: String { steps.last?["text"].str ?? "" }
    var to: Double { steps.last?["time"].dbl ?? 0 }
    static func split(_ steps: [JSON]) -> [Stage] {
        var whos: [String] = [], groups: [[JSON]] = []
        for s in steps {
            let who = s["who"].str.split(separator: "/").first.map(String.init) ?? "ag_sena"
            if whos.last == who { groups[groups.count - 1].append(s) } else { whos.append(who); groups.append([s]) }
        }
        return groups.enumerated().map { Stage(id: $0.offset, who: whos[$0.offset], steps: $0.element) }
    }
}

struct FlowView: View {
    @EnvironmentObject var model: AppModel
    @State private var recent: [JSON] = []
    @State private var failure: String? = nil   // the last load of finished tasks failed

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Style.Space.xl) {
                AgentsStrip()
                if model.active.isEmpty {
                    Block(model.alive ? L("Сейчас") : L("Сена выключена"), lead: true) {
                        Text(model.alive ? L("Свободна") : "—").font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
                    }
                } else {
                    FlowTitle(String(format: L("В работе · %d"), model.active.count))
                    ForEach(model.active, id: \.["tid"].str) { t in Lane(task: t, live: true).transition(Style.Motion.appear) }
                }
                if let e = failure {
                    Text(String(format: L("Недавние задачи не загрузились: %@"), e)).font(Style.Text.caption).foregroundStyle(Style.Colors.badText)
                }
                if !recent.isEmpty {
                    FlowTitle(L("Недавно"))
                    ForEach(recent, id: \.["id"].str) { t in Lane(task: t, live: false) }
                }
            }
            .motion(value: model.active.count)
        }
        .background(DotGrid())
        .task {
            while !Task.isCancelled {
                do {
                    let r = try await model.backend.get("tasks", ["limit": "12"])
                    recent = Array(r["finished"].array.filter { !$0["service"].bool }.prefix(5)); failure = nil
                } catch { if !Task.isCancelled { failure = error.localizedDescription } }
                try? await Task.sleep(nanoseconds: 10_000_000_000)
            }
        }
        .actions {
            Button(L("К задачам")) { model.open(.tasks, tab: 0) }   // the diagram itself lives in the "Tasks" section: needs the "List" tab
        }
    }
}

struct FlowTitle: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View { Text(text).font(Style.Text.heading).foregroundStyle(Style.Colors.secondaryText) }
}

/// Dot grid behind the diagram
struct DotGrid: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        Canvas { ctx, size in
            let step = Style.Size.gridStep, r = Style.Size.line * 0.75
            var x = step / 2
            while x < size.width {
                var y = step / 2
                while y < size.height {
                    ctx.fill(Path(ellipseIn: CGRect(x: x - r, y: y - r, width: r * 2, height: r * 2)), with: .color(Style.Colors.grid(scheme)))
                    y += step
                }
                x += step
            }
        }
        .allowsHitTesting(false)
    }
}

// ---- agents ----
struct AgentsStrip: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: AGENT_TILE), spacing: Style.Space.m)], spacing: Style.Space.m) {
            ForEach(model.overview["agents"].array, id: \.["code"].str) { a in AgentTile(agent: a) }
        }
    }
}

struct AgentTile: View {
    let agent: JSON
    var body: some View {
        let code = agent["code"].str, busy = agent["busy"].string, role = agent["role"].str
        FlowCard(color: Style.Colors.agent(code), active: busy != nil, width: nil) {
            HStack(spacing: Style.Space.s) {
                AgentIcon(code: code)
                Text(agentName(code)).font(Style.Text.heading).lineLimit(1)
                Spacer()
                StatusDot(ok: busy != nil, bad: busy == nil)
            }
            Text(ROLE_RU[role] ?? role).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(1)
            Spacer(minLength: 0)
            Text(busy ?? L("свободен")).font(Style.Text.small).foregroundStyle(busy == nil ? Style.Colors.secondaryText : Color.primary).lineLimit(2)
        }
        .help(busy ?? L("свободен"))
        .accessibilityElement(children: .combine)
        .motion(Style.Motion.quick, value: busy == nil)
    }
}

// ---- lane for one command ----
/// The command card stays in place, the chain of stages scrolls to the right and jumps to the end on its own with every new stage
struct Lane: View {
    @Environment(\.accessibilityReduceMotion) private var still
    let task: JSON
    let live: Bool
    private static let end = "end"
    var body: some View {
        let stages = Stage.split(task["steps"].array)
        let shown = Array(stages.suffix(SHOWN_STAGES))
        HStack(spacing: 0) {
            TaskCard(task: task, live: live, stages: stages.count)
            ScrollViewReader { proxy in
                ScrollView(.horizontal) {
                    HStack(spacing: 0) {
                        Connector()
                        if stages.count > shown.count { MoreNode(count: stages.count - shown.count); Connector() }
                        if shown.isEmpty { StageNode(stage: nil, current: live) }
                        ForEach(shown) { st in
                            if st.id != shown.first?.id { Connector() }
                            StageNode(stage: st, current: live && st.id == stages.count - 1)
                        }
                        if !live { Connector(); ResultNode(task: task) }
                        Color.clear.frame(width: 1, height: 1).id(Lane.end)
                    }
                    .padding(.vertical, Style.Space.s)
                }
                .onAppear { proxy.scrollTo(Lane.end, anchor: .trailing) }
                .onChange(of: stages.count) { withAnimation(still ? nil : Style.Motion.spring) { proxy.scrollTo(Lane.end, anchor: .trailing) } }
            }
        }
    }
}

/// Tema's command: a card in Sena's color at the start of the lane; a service chore (cleanup, triage) gets a different label
struct TaskCard: View {
    @EnvironmentObject var model: AppModel
    let task: JSON
    let live: Bool
    let stages: Int
    var body: some View {
        let start = task["start"].dbl
        let end = live ? model.overview["now"].dbl : (task["end"].dbl > 0 ? task["end"].dbl : start)
        let tint = Style.Colors.agent("ag_sena")
        let service = task["service"].bool
        let open = { model.openTask(live ? AppModel.taskID(task) : task["id"].str) }
        FlowCard(color: tint, active: live, width: Style.Size.flowCard, fill: tint.opacity(Style.Colors.tintOpacity)) {
            HStack(spacing: Style.Space.s) {
                Glyph("text.bubble.fill").font(Style.Icon.inline).foregroundStyle(tint)
                Text(live ? (service ? L("Служебное · идёт") : L("Команда · идёт")) : L("Команда")).font(Style.Text.heading)
                Spacer()
                if live { Button(L("Остановить"), role: .destructive) { model.stopTask(AppModel.taskID(task), title: task["text"].str) }.buttonStyle(.quiet) }
                else { verdictTag(task) }
            }
            Text(taskTitle(task)).font(Style.Text.body.weight(.medium)).lineLimit(2)
            Spacer(minLength: 0)
            HStack(spacing: Style.Space.m) {
                Text(Fmt.time(task["start"]))
                Text(Fmt.dur(end - start)).contentTransition(.numericText())
                if task["cost"].dbl > 0 { Text(Fmt.usd(task["cost"].dbl)) }
                Spacer()
                Text("\(stages) \(Fmt.plural(stages, "этап", "этапа", "этапов"))")
            }
            .font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
        }
        .contentShape(Rectangle())
        .onTapGesture(perform: open)
        .help(task["text"].str + "\n" + L("Открыть в «Задачах»"))
        .accessibilityElement(children: .contain)
        .accessibilityAction(named: L("Открыть в «Задачах»"), open)
    }
}

/// An agent's work stage; the current one has a border in the agent's color and "typing" dots
struct StageNode: View {
    let stage: Stage?
    let current: Bool
    var body: some View {
        let who = stage?.who ?? "ag_sena", n = stage?.steps.count ?? 0, tint = Style.Colors.agent(who)
        let last = stage?.last ?? ""
        FlowCard(color: tint, active: current, width: Style.Size.flowNode) {
            HStack(spacing: Style.Space.s) {
                AgentIcon(code: who)
                Text(agentName(who)).font(Style.Text.heading).lineLimit(1)
                Spacer()
                if current { TypingDots() } else if let s = stage { Text(Fmt.times(JSON(s.to))).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText) }
            }
            Text(last.isEmpty ? L("начало") : last).font(Style.Text.small).lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
            Spacer(minLength: 0)
            HStack {
                Text(n > 0 ? "\(n) \(Fmt.plural(n, "шаг", "шага", "шагов"))" : L("ждёт"))
                Spacer()
                Text(agentRole(who)).lineLimit(1)
            }
            .font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
        }
        .help(last)
        .accessibilityElement(children: .combine)
    }
}

/// Collapsed earlier stages
struct MoreNode: View {
    @Environment(\.colorScheme) private var scheme
    let count: Int
    var body: some View {
        FlowCard(color: Style.Colors.lineStrong(scheme), active: false, width: Style.Size.flowNode) {
            Spacer(minLength: 0)
            Text(String(format: L("ещё %@ раньше"), "\(count) \(Fmt.plural(count, "этап", "этапа", "этапов"))")).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
            Spacer(minLength: 0)
        }
    }
}

/// Result of a finished task: verdict, start of the answer, duration; the band's color follows the verdict
struct ResultNode: View {
    let task: JSON
    var body: some View {
        let v = task["outcome"]["verdict"].string ?? "none", color = v == "none" ? Style.Colors.ink : verdictColor(v)
        FlowCard(color: color, active: false, width: Style.Size.flowNode) {
            HStack(spacing: Style.Space.s) {
                Glyph("flag.checkered").font(Style.Icon.inline).foregroundStyle(color)
                Text(L("Итог")).font(Style.Text.heading)
                Spacer()
                verdictTag(task)
            }
            Text(task["answer"].str.isEmpty ? L("без ответа") : task["answer"].str).font(Style.Text.small).lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
            Spacer(minLength: 0)
            Text(Fmt.dur(task["end"].dbl - task["start"].dbl)).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
        }
        .help(task["answer"].str)
        .accessibilityElement(children: .combine)
    }
}

/// Line between nodes with a dot in the middle
struct Connector: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        ZStack {
            Rectangle().fill(Style.Colors.lineStrong(scheme)).frame(height: Style.Size.line)
            Circle().fill(Style.Colors.lineStrong(scheme)).frame(width: Style.Size.lineDot, height: Style.Size.lineDot)
        }
        .frame(width: Style.Size.connector)
    }
}

/// Diagram card: same size for all, a colored band on top, a visible border and shadow; an active one has a border in its own color
struct FlowCard<Content: View>: View {
    @Environment(\.colorScheme) private var scheme
    let color: Color
    let active: Bool
    let width: CGFloat?
    var fill: Color? = nil
    @ViewBuilder let content: () -> Content
    var body: some View {
        let shape = RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous)
        VStack(alignment: .leading, spacing: Style.Space.xs) { content() }
            .padding(Style.Space.l)
            .padding(.top, Style.Space.xs)
            .frame(width: width, height: Style.Size.flowHeight, alignment: .topLeading)
            .frame(maxWidth: width == nil ? .infinity : nil, alignment: .topLeading)
            .background(fill ?? (color == Style.Colors.ink ? Style.Colors.accent : color).opacity(NODE_TINT), in: shape)   // a pastel of the agent's color, not white
            .overlay(alignment: .top) { Rectangle().fill(color).frame(height: Style.Size.band) }
            .clipShape(shape)
            .overlay(shape.strokeBorder(active ? color : Color.primary.opacity(Style.Colors.nodeBorder), lineWidth: active ? Style.Size.line * 2 : Style.Size.line))
            .shadow(color: .black.opacity(Style.Colors.nodeShadow), radius: NODE_SHADOW_RADIUS, y: NODE_SHADOW_Y)
            .hoverScale(Style.Motion.lift)
            .rise(compact: true)
            .motion(Style.Motion.quick, value: active)
    }
}
