// The shelf by the screen notch: not just an indicator, a small remote. Three tabs as icons — "Chat" (write to Sena and see
// her latest reply without opening the app), "Tasks" (what runs, stop it) and "Settings". At rest it's a narrow capsule (dots
// for busy agents), hovering expands it. Data comes from model.overview,
// model.active, ChatSession.shared — the same foundation that AgentsWidget and ComposeWidget used to show.
// The window and the notch shape follow the open-source project boring.notch (github.com/TheBoredTeam/boring.notch):
// level .mainMenu+3, no shadow, top flush with the screen edge. The window's size does NOT change while hovering —
// moving the real AppKit window to follow SwiftUI's spring on every frame lagged. Instead the window is sized once,
// to the biggest of all states, and the capsule and the tabs are just different SwiftUI content in the same spot.
import SwiftUI
import UniformTypeIdentifiers

enum NotchTab: Hashable {
    case chat, tasks, settings
    // filled, plain shapes of one weight: a speech bubble, a clipboard, a gear (the power button too) — none thin next to a solid one
    var icon: String { switch self { case .chat: return "bubble.left.fill"; case .tasks: return "list.bullet.clipboard.fill"; case .settings: return "gearshape.fill" } }
    var title: String { switch self { case .chat: return L("Чат"); case .tasks: return L("Задачи"); case .settings: return L("Настройки") } }
}

/// The agent's current task: the same way the server decides "busy" (desk.py: the last three steps) — shared by the agent tile and hovering over it
@MainActor func currentTask(_ model: AppModel, of code: String) -> JSON? {
    model.active.first { $0["steps"].array.suffix(3).contains { agentCode($0["who"].str) == code } }
}

/// One shelf state shared by the app: changed by both mouse hover and voice ("Sena, …" opens chat on its own — Ears.swift)
@MainActor final class NotchState: ObservableObject {
    static let shared = NotchState()
    @Published var expanded = false { didSet { if !expanded { NotchPanel.shared.releaseKeyboard() } } }
    @Published var tab: NotchTab = .chat
    var hovering = false   // the cursor is currently over the shelf: voice auto-collapse doesn't yank it out from under the hand
}

/// The "Chat" tab: a conversation as bubbles, like a real chat — Tema's white on the right, Sena's black on the left, no labels
/// or boilerplate words. A voice command ("Sena, …" from the Mac's microphone) is shown with the same bubbles; the text can be
/// hidden in the shelf's settings. The feed's height is always the same: the window is measured once, it can't jump around
struct NotchChatBody: View {
    @EnvironmentObject var model: AppModel
    @ObservedObject private var session = ChatSession.shared
    @ObservedObject private var ears = Ears.shared
    @AppStorage("notchTranscript") private var transcript = true
    @State private var text = ""
    var trimmed: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }
    var body: some View {
        VStack(spacing: Style.Space.gapSmall) {
            if transcript || NotchAgentsView.probing {   // while measuring, the feed always counts: if it gets turned on, it must fit
                bubbles(session.messages + session.pending)   // the very feed the chat shows (ChatSession): never a different moment
                    .frame(maxWidth: .infinity, minHeight: Style.Size.controlHeight * 3, alignment: .bottomLeading)
            }
            if !session.files.isEmpty || NotchAgentsView.probing {   // dropped files: will be sent together with the text by the button
                HStack(spacing: Style.Space.s) {
                    Spacer(minLength: 0)   // the upcoming attachment sits at the right edge, like Tema's own bubbles, not Sena's replies
                    ForEach(session.files, id: \.["path"].str) { f in
                        HStack(spacing: Style.Space.xs) {
                            Image(systemName: "paperclip").font(Style.Icon.tiny)
                            Text(f["name"].str).lineLimit(1)
                            Button { session.files.removeAll { $0["path"].str == f["path"].str } } label: { Image(systemName: "xmark").font(Style.Icon.tiny) }.buttonStyle(.plain)
                        }
                        .font(Style.Text.caption).foregroundStyle(Color.black)
                        .padding(.horizontal, Style.Space.m).padding(.vertical, Style.Space.xs)
                        .background(Color.white).clipShape(RoundedRectangle(cornerRadius: Style.Radius.all))
                    }
                }
                .frame(height: Style.Size.counter + Style.Space.gapSmall)
            }
            HStack(spacing: Style.Space.gapSmall) {
                TextField(L("Напиши Сене…"), text: $text).textFieldStyle(.plain).foregroundStyle(.white).onSubmit(send)
                if session.uploading > 0 {
                    Text(L("Вложение ещё загружается…")).font(Style.Text.caption).foregroundStyle(Style.Colors.off).lineLimit(1)
                } else if ears.running {   // the live voice: waiting for her name, her brain at work, or no stream to Sena (and why, when she said)
                    StatusDot(ok: ears.connected, bad: !ears.connected)
                    Text(!ears.connected ? (ears.refused ?? L("Не слышу")) : ears.thinking ? L("Думаю") : L("Слушаю"))
                        .font(Style.Text.caption).foregroundStyle(Style.Colors.off).lineLimit(1)
                }
                Button(action: send) { Image(systemName: "arrow.up.circle.fill").font(Style.Icon.chip) }
                    .buttonStyle(.plain).foregroundStyle(trimmed.isEmpty && session.files.isEmpty ? Style.Colors.off : Color.white)
                    .disabled(trimmed.isEmpty && session.files.isEmpty)
            }
            .padding(.horizontal, Style.Space.m).padding(.vertical, Style.Space.s)
            .background(Color.black).clipShape(RoundedRectangle(cornerRadius: Style.Radius.all))
        }
        .padding(.horizontal, Style.Space.gap).padding(.bottom, Style.Space.gapSmall)
        .onAppear { session.watch(model) }
        .onDisappear { session.unwatch() }
    }
    /// The latest lines: the server's feed, with the live conversation from the Mac on top (what was heard, what she said)
    /// if it isn't in the feed yet
    func bubbles(_ history: [JSON]) -> some View {
        var rows = history.map { m -> (mine: Bool, text: String) in
            let parts = FileNote.split(m["content"].str)   // a file note is shown by the file's name, not as a raw path
            return (m["role"].str == "user", parts.text.isEmpty ? parts.notes.map(\.name).joined(separator: ", ") : parts.text)
        }.filter { !$0.text.isEmpty }
        // the live conversation's words until the feed has that turn. Not by the text: the feed keeps the request as her brain
        // got it («где музыка…»), the live voice shows it as heard («Ну так что, где музыка…») — both showed, one phrase twice.
        // A turn the feed already has a message from, since it began, is the feed's
        func written(_ role: String, since: Date?) -> Bool {
            guard let since else { return false }
            return history.suffix(6).contains { $0["role"].str == role && (Fmt.date($0["time"]) ?? .distantPast) >= since.addingTimeInterval(-2) }
        }
        if ears.fresh {
            if let heard = ears.lastHeard, !heard.isEmpty, !written("user", since: ears.heardAt) { rows.append((true, heard)) }
            if let said = ears.lastSaid, !said.isEmpty, !written("assistant", since: ears.saidAt) { rows.append((false, said)) }
        }
        if !session.sent.isEmpty && rows.last?.mine == true { rows.append((false, session.sent.last?.steps.last ?? "…")) }   // she is working on it
        return VStack(spacing: Style.Space.xs) {
            ForEach(Array(rows.suffix(3).enumerated()), id: \.offset) { _, r in bubble(mine: r.mine, r.text) }
        }
    }
    func bubble(mine: Bool, _ what: String) -> some View {
        HStack(spacing: 0) {
            if mine { Spacer(minLength: Style.Space.huge) }
            Text(mine ? AttributedString(what) : markdownText(what)).font(Style.Text.caption).foregroundStyle(mine ? Color.black : Color.white).lineLimit(2)   // her bold is bold, not asterisks
                .padding(.horizontal, Style.Space.m).padding(.vertical, Style.Space.s)
                .background(mine ? Color.white : Color.black)
                .clipShape(RoundedRectangle(cornerRadius: Style.Radius.all))
            if !mine { Spacer(minLength: Style.Space.huge) }
        }
    }
    func send() {
        let t = trimmed
        guard session.submit(model, t) else { return }
        text = ""
    }
}

/// The "Settings" tab: only the switches that matter day to day, on two black cards readable on any glass. Left — this Mac (Sena
/// herself, entirely, so she doesn't spend money; its microphone; voice replies); right — what she does on her own (writing to the
/// bot, the background check, hearing from the phone: the server's settings). Everything else is in the app
struct NotchSettingsBody: View {
    @EnvironmentObject var model: AppModel
    @AppStorage("macEars") private var macEars = false
    @AppStorage("macVoiceReply") private var macVoiceReply = true
    var body: some View {
        HStack(alignment: .top, spacing: Style.Space.gapSmall) {
            card {
                row(L("Сена включена"), isOn: Binding(get: { model.alive }, set: { _ in model.toggleCore() })).disabled(!model.up)
                row(L("Слушать голос с Mac"), isOn: Binding(get: { macEars }, set: { macEars = $0; Ears.shared.sync(model) }))
                    .help(L("Микрофон этого Mac слышит всё вокруг: на «Сена, …» она отвечает, остальную речь расшифровывает и хранит для памяти"))
                row(L("Отвечать голосом"), isOn: Binding(get: { macVoiceReply }, set: { macVoiceReply = $0; Ears.shared.sync(model) }))   // at once, mid-conversation too
            }
            card {
                server(L("Сама пишет в бот"), "telegram_notify")
                server(L("Фоновая проверка"), "heartbeat")
                server(L("Слух с телефона"), "use_hearing")
            }
        }
        .font(Style.Text.small)
        .foregroundStyle(.white)
        .padding(.horizontal, Style.Space.gap).padding(.bottom, Style.Space.gapSmall)
    }
    func card<Content: View>(@ViewBuilder _ rows: () -> Content) -> some View {
        VStack(spacing: Style.Space.gapSmall) { rows() }
            .padding(Style.Space.gap)
            .frame(maxWidth: .infinity)
            .background(Color.black).clipShape(RoundedRectangle(cornerRadius: Style.Radius.all))
    }
    /// A switch of the server's settings (settings.json): it waits for the server, so the screen shows what was really saved
    func server(_ title: String, _ key: String) -> some View {
        row(title, isOn: Binding(get: { model.settings[key].bool }, set: { model.setSetting(key, $0) })).disabled(!model.up)
    }
    /// A row like in System Settings: a label on the left, a compact switch at the right edge — everything on one line
    func row(_ title: String, isOn: Binding<Bool>) -> some View {
        HStack(spacing: Style.Space.gap) {
            Text(title).lineLimit(1)
            Spacer(minLength: Style.Space.gapSmall)
            Toggle(title, isOn: isOn).toggleStyle(.senaMonoBare)
        }
    }
}

/// The notch's own silhouette: concave corners on top (the transition into the screen's edge), an ordinary rounded corner on the bottom.
/// Shape source: NotchShape from DynamicNotchKit/boring.notch — the same quadratic curves, with no extra corners
struct NotchShape: Shape {
    var topRadius: CGFloat
    var bottomRadius: CGFloat
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: r.minX, y: r.minY))
        p.addQuadCurve(to: CGPoint(x: r.minX + topRadius, y: r.minY + topRadius), control: CGPoint(x: r.minX + topRadius, y: r.minY))
        p.addLine(to: CGPoint(x: r.minX + topRadius, y: r.maxY - bottomRadius))
        p.addQuadCurve(to: CGPoint(x: r.minX + topRadius + bottomRadius, y: r.maxY), control: CGPoint(x: r.minX + topRadius, y: r.maxY))
        p.addLine(to: CGPoint(x: r.maxX - topRadius - bottomRadius, y: r.maxY))
        p.addQuadCurve(to: CGPoint(x: r.maxX - topRadius, y: r.maxY - bottomRadius), control: CGPoint(x: r.maxX - topRadius, y: r.maxY))
        p.addLine(to: CGPoint(x: r.maxX - topRadius, y: r.minY + topRadius))
        p.addQuadCurve(to: CGPoint(x: r.maxX, y: r.minY), control: CGPoint(x: r.maxX - topRadius, y: r.minY))
        p.addLine(to: CGPoint(x: r.minX, y: r.minY))
        return p
    }
}

/// A dot's breathing on the collapsed capsule: a smooth inhale-exhale while the agent is busy
private struct Breathe: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var still
    @State private var big = false
    func body(content: Content) -> some View {
        content.scaleEffect(still ? 1 : (big ? 1.35 : 0.85))
            .motion(Style.Motion.breathe, value: big)
            .onAppear { big = true }
    }
}

struct NotchAgentsView: View {
    @EnvironmentObject var model: AppModel
    let notchHeight: CGFloat   // the notch's own height (NSScreen.safeAreaInsets.top): nothing is drawn under it — that's where the camera is
    let notchWidth: CGFloat    // the notch's own width: only this narrow zone at the top opens the shelf on hover
    let canvas: CGSize?        // the window's canvas (the visible part inside it is centered at the top); nil — only for measuring the size
    let forced: (expanded: Bool, tab: NotchTab)?   // measuring: show this state, don't touch the shared state
    @ObservedObject private var state = NotchState.shared
    @State private var trigger = 0

    init(notchHeight: CGFloat, notchWidth: CGFloat = Style.Size.notchFallback, canvas: CGSize? = nil, forced: (expanded: Bool, tab: NotchTab)? = nil) {
        self.notchHeight = notchHeight
        self.notchWidth = notchWidth
        self.canvas = canvas
        self.forced = forced
    }

    var expanded: Bool { forced?.expanded ?? state.expanded }
    var tab: NotchTab { forced?.tab ?? state.tab }
    static var probing = false   // the window is being measured: show everything at maximum (including the chat feed), toggles don't count

    var agents: [JSON] { model.overview["agents"].array }
    var busyList: [JSON] { agents.filter { $0["busy"].string != nil } }
    /// One width for the open shelf, whatever it shows: the two side blocks by the notch and the notch between them. It used to
    /// be measured by the widest tab — and a chat message, measured as one unbroken line, made the shelf as wide as the screen
    var shelfWidth: CGFloat { notchWidth + Style.Size.notchSide * 2 + Style.Space.l * 2 + Style.Space.gap * 2 }

    var body: some View {
        Group {
            if let canvas {
                ZStack(alignment: .top) { shelf }
                    .frame(width: canvas.width, height: canvas.height, alignment: .top)
                    // the whole invisible window zone catches a file drop: right at the screen's edge the system's own drag interferes with Mission Control
                    .onDrop(of: [.fileURL], isTargeted: Binding(get: { false }, set: { if $0 { openForDrop() } })) { acceptDrop($0) }
            } else { shelf }   // no canvas — only to measure the natural size of the state needed
        }
        .task(id: expanded) {
            guard expanded else { return }
            while !Task.isCancelled {   // while the shelf is open, busy agents occasionally play their icon animation
                try? await Task.sleep(for: .seconds(2.6))
                trigger += 1
            }
        }
    }

    /// At rest nothing sticks out by the notch — no glass, no glow, just the invisible hover zone (and dots,
    /// if someone's busy, right on the desktop, with no backing). The background and shape appear only when expanded.
    /// Only hovering over the notch itself opens it (a narrow strip at the top, its real size); it closes when the
    /// cursor leaves the whole already-expanded shelf, otherwise the icons and buttons would be unreachable with the mouse
    var shelf: some View {
        VStack(spacing: 0) {
            topRow
            if expanded { expandedBody.transition(Style.Motion.appear) } else { collapsedPill.transition(Style.Motion.appear) }
        }
        // ONE SHARED width for every tab: the expanded glass always fills the whole canvas (measured by the widest tab),
        // buttons stay in the same spots, nothing jumps or gets clipped by the shape's corners when switching tabs
        .frame(width: expanded ? shelfWidth : nil)
        .background(expanded ? AnyView(Rectangle().fill(.ultraThinMaterial)) : AnyView(Color.clear))
        .clipShape(NotchShape(topRadius: Style.Radius.notchTop, bottomRadius: Style.Radius.all))
        .onHover { h in
            state.hovering = h
            guard forced == nil, expanded, !h else { return }
            withAnimation(Style.Motion.spring) { state.expanded = false }
        }
        .onDrop(of: [.fileURL], isTargeted: nil) { acceptDrop($0) }   // a file can be dropped anywhere on the expanded glass
    }

    /// The notch's own row: at rest — an invisible hover zone exactly beneath it. Expanded — tab toggles
    /// on either side of the notch, at a distance from it (the middle is invisible anyway: covered by the camera),
    /// the row keeps the notch's own height (the menu bar's), so the buttons fit in it like menu bar items
    var topRow: some View {
        Group {
            if expanded {
                HStack(spacing: Style.Space.l) {
                    // side blocks of equal width keep the gap exactly under the notch: the agents' characters left of it, the tabs right
                    HStack(spacing: Style.Space.xs) {
                        ForEach(characters, id: \.["code"].str) { a in
                            AgentCharacter(code: a["code"].str, emotion: a["emotion"].string ?? "calm", thought: a["thought"].string)
                                .frame(width: characterSize, height: characterSize)
                                .help(a["busy"].string.map { "\(a["name"].str): \($0)" } ?? a["name"].str)
                        }
                    }
                    .frame(width: Style.Size.notchSide, alignment: .trailing)
                    // not a Spacer (would stretch across the whole window width), and it must have a height: without one, Color grew
                    // greedily in height when the tab is shorter than the window's canvas, and dragged the buttons toward the middle (the "Chat" tab)
                    Color.clear.frame(width: notchWidth, height: 0)
                    HStack(spacing: Style.Space.l) {
                        tabButton(.chat)
                        tabButton(.tasks)
                        tabButton(.settings)
                        powerButton
                    }
                    .frame(width: Style.Size.notchSide, alignment: .leading)
                }
                .padding(.horizontal, Style.Space.gap)   // from the glass's edges, so the outer buttons don't sit on the rounded corners
                .frame(height: notchHeight)   // the row is exactly the menu bar's height: buttons sit in it like the system's own, nothing hangs below
            } else {
                Color.clear.frame(width: notchWidth, height: notchHeight)
                    .contentShape(Rectangle())
                    .onHover { h in if h, forced == nil { withAnimation(Style.Motion.spring) { state.expanded = true } } }
                    // a file was brought near the notch: the shelf opens to "Chat" while it's still being dragged
                    .onDrop(of: [.fileURL], isTargeted: Binding(get: { false }, set: { if $0 { openForDrop() } })) { acceptDrop($0) }
            }
        }
    }

    /// While a file is being dragged toward the notch — open chat; dropped — attach it (sending only via the button)
    func openForDrop() {
        guard forced == nil else { return }
        withAnimation(Style.Motion.spring) { state.tab = .chat; state.expanded = true }
    }

    func acceptDrop(_ providers: [NSItemProvider]) -> Bool {
        let taken = dropFiles(providers) { ChatSession.shared.upload(model, $0) }   // the same path as dragging into the app's chat
        openForDrop()
        return taken
    }

    /// Turns Sena (the core) fully off or on, the same as "Сена включена" in settings; stopping asks first while tasks run
    var powerButton: some View {
        let title = model.alive ? L("Выключить Сену") : L("Включить Сену")
        return Button { guard forced == nil else { return }; model.toggleCore() } label: {
            Image(systemName: "power.circle.fill").resizable().scaledToFit()
                .frame(width: Style.Icon.menuWidth, height: Style.Icon.menuWidth)
                .foregroundStyle(Color.black)
                .frame(width: tabSize, height: tabSize)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!model.up)
        .help(title)
        .accessibilityLabel(title)
    }

    /// The agents that have a character of their own (Style.Colors.characters), in the server's order
    var characters: [JSON] { agents.filter { Style.Colors.characters[$0["code"].str] != nil } }
    var characterSize: CGFloat { notchHeight - 2 * Style.Space.xxs }

    /// The tab button fits inside the menu bar's height with a small margin above and below
    var tabSize: CGFloat { notchHeight - 2 * Style.Space.xs }

    /// A tab button: a bare black icon (words took too much room by the notch); its name on hover
    func tabButton(_ t: NotchTab) -> some View {
        Button { guard forced == nil else { return }; withAnimation(Style.Motion.spring) { state.tab = t } } label: {
            Image(systemName: t.icon).resizable().scaledToFit()
                .frame(width: Style.Icon.menuWidth, height: Style.Icon.menuWidth)   // one box for every sign: none looks bigger or bolder
                .foregroundStyle(Color.black)
                .frame(width: tabSize, height: tabSize)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(t.title)
        .accessibilityLabel(t.title)
    }

    /// The narrow zone by the notch at rest: nothing is visible if everyone's free, otherwise a dot in each busy agent's color —
    /// no background, just dots on the desktop. The hover zone is wider than the dots themselves, to give the mouse something to aim at
    var collapsedPill: some View {
        HStack(spacing: Style.Space.xs) {
            ForEach(busyList, id: \.["code"].str) { a in
                Circle().fill(Style.Colors.agent(a["code"].str)).frame(width: Style.Space.s, height: Style.Space.s).modifier(Breathe())
            }
        }
        .padding(.horizontal, Style.Space.gap).padding(.vertical, Style.Space.gapSmall)
        .frame(minWidth: Style.Size.counter * 2, minHeight: Style.Space.m)
        .contentShape(Rectangle())
    }

    var expandedBody: some View {
        VStack(spacing: Style.Space.gapSmall) {
            switch tab {
            case .tasks: tasksTab
            case .chat: NotchChatBody()
            case .settings: NotchSettingsBody()
            }
        }
        .padding(.top, Style.Space.gapSmall)
    }

    /// The "Tasks" tab: up to three running tasks as black cards (white text reads on any glass), each with "Stop"; clicking a card
    /// opens the task in the app. Nothing running — one card with today's count and spending. The height for three cards is
    /// always the same — the window can't jump around
    var tasksTab: some View {
        VStack(spacing: Style.Space.gapSmall) {
            if model.ownTasks.isEmpty {
                card {
                    VStack(alignment: .leading, spacing: Style.Space.xxs) {
                        Text(model.alive ? L("Сена свободна") : L("Сена выключена")).font(Style.Text.small.weight(.semibold)).foregroundStyle(.white)
                        Text(String(format: L("Сегодня задач: %d · %@"), model.overview["today"]["tasks"].int, Fmt.usd(model.overview["today"]["usd"].dbl)))
                            .font(Style.Text.caption).foregroundStyle(Style.Colors.off)
                    }
                    Spacer(minLength: Style.Space.m)
                    if !model.alive { pill(L("Включить")) { model.toggleCore() }.disabled(!model.up) }
                }
            }
            ForEach(model.ownTasks.prefix(3), id: \.["tid"].str) { t in taskRow(t) }
        }
        .frame(maxWidth: .infinity, minHeight: (Style.Size.controlHeight + Style.Space.gap * 2 + Style.Space.gapSmall) * 3, alignment: .top)
        .padding(.horizontal, Style.Space.gap).padding(.bottom, Style.Space.gapSmall)
    }

    func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        HStack(spacing: Style.Space.m) { content() }
            .padding(Style.Space.gap)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.black).clipShape(RoundedRectangle(cornerRadius: Style.Radius.all))
    }

    /// A white button on a black card: the one action of the card
    func pill(_ title: String, _ action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .buttonStyle(.plain)
            .font(Style.Text.caption.weight(.medium)).foregroundStyle(Color.black)
            .padding(.horizontal, Style.Space.m).padding(.vertical, Style.Space.xs)
            .background(Color.white).clipShape(RoundedRectangle(cornerRadius: Style.Radius.all))
    }

    func taskRow(_ t: JSON) -> some View {
        let last = t["steps"].array.last
        return card {
            AgentIcon(code: last?["who"].str ?? "ag_sena", trigger: trigger)
            VStack(alignment: .leading, spacing: Style.Space.xxs) {
                Text(t["text"].str).font(Style.Text.small.weight(.semibold)).foregroundStyle(.white).lineLimit(1)
                HStack(spacing: Style.Space.s) {
                    Text(Fmt.dur(model.overview["now"].dbl - t["start"].dbl)).contentTransition(.numericText())
                    if t["cost"].dbl > 0 { Text(Fmt.usd(t["cost"].dbl)) }
                    if last != nil { Text(last?["text"].str ?? "").lineLimit(1) }
                }
                .font(Style.Text.caption).foregroundStyle(Style.Colors.off)
            }
            Spacer(minLength: Style.Space.m)
            pill(L("Стоп")) { model.stopTask(AppModel.taskID(t), title: t["text"].str) }
        }
        .contentShape(Rectangle())
        .onTapGesture { if openApp(model, .tasks) { model.selectedTask = AppModel.taskID(t) } }
    }
}

/// A custom window: no border, no shadow (the shadow left a light strip above the panel at the screen's edge), never activates the
/// app, a level slightly above the menu bar — the same way the notch itself sits above it; a dark appearance so the glass doesn't
/// lighten in light mode. It takes the keyboard only for the "Chat" field (becomesKeyOnlyIfNeeded: a click on a button leaves typing
/// with the app in front) — a panel that can't become key sent what was typed into the shelf to that other app
final class NotchWindow: NSPanel {
    override init(contentRect: NSRect, styleMask style: NSWindow.StyleMask, backing: NSWindow.BackingStoreType, defer flag: Bool) {
        super.init(contentRect: contentRect, styleMask: style, backing: backing, defer: flag)
        isFloatingPanel = true
        becomesKeyOnlyIfNeeded = true
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        appearance = NSAppearance(named: .darkAqua)
        isMovable = false
        isMovableByWindowBackground = false
        hidesOnDeactivate = false
        isReleasedWhenClosed = false
        level = .mainMenu + 3
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
    }
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

/// The shelf window: the size is set ONCE, to the largest of the states (the "Agents" or "Chat" tab) — nobody
/// touches it after that. The capsule and the tabs switch only inside SwiftUI; AppKit never moves the window frame by frame
@MainActor final class NotchPanel: ObservableObject {
    static let shared = NotchPanel()
    private var panel: NotchWindow?
    var isOpen: Bool { panel?.isVisible == true }
    var windowFrame: NSRect? { panel?.frame }   // where the shelf is on screen: the characters' eyes follow the mouse from there
    @Published private(set) var shown = false   // the same, for the menu bar's item: it reads "hide" while the shelf is up

    /// The shelf collapsed while its field had the keyboard: typing goes back to the app in front (ordering the panel out and in
    /// drops its key status; the shelf itself stays where it is)
    func releaseKeyboard() {
        guard let p = panel, p.isKeyWindow else { return }
        p.orderOut(nil)
        p.orderFrontRegardless()
    }

    func toggle(_ model: AppModel) {
        defer { shown = isOpen }
        if isOpen {
            panel?.orderOut(nil)
            panel?.contentView = nil
            NotchState.shared.expanded = false   // otherwise, after reopening, the shelf would remember it was expanded and stay stuck open
            return
        }
        let screen = Self.notchScreen()
        let notchHeight = max(screen.safeAreaInsets.top, 0)
        let notchWidth: CGFloat = {
            guard let l = screen.auxiliaryTopLeftArea, let r = screen.auxiliaryTopRightArea else { return Style.Size.notchFallback }
            return max(r.minX - l.maxX, Style.Size.notchFallback / 2)
        }()
        // measure: what the largest state will be (any tab, expanded), without a canvas
        func measure(_ tab: NotchTab) -> CGSize {
            NSHostingView(rootView: NotchAgentsView(notchHeight: notchHeight, notchWidth: notchWidth, forced: (true, tab)).environmentObject(model)).fittingSize
        }
        NotchAgentsView.probing = true
        let sizes = [measure(.chat), measure(.tasks), measure(.settings)]
        NotchAgentsView.probing = false
        let maxSize = CGSize(width: sizes.map(\.width).max() ?? 0, height: sizes.map(\.height).max() ?? 0)
        let p = panel ?? makePanel()
        p.contentView = NSHostingView(rootView: NotchAgentsView(notchHeight: notchHeight, notchWidth: notchWidth, canvas: maxSize).environmentObject(model))
        let size = NSSize(width: maxSize.width.rounded(.up), height: maxSize.height.rounded(.up))
        // positioning the top from screen.frame.maxY left a one-point gap (measured by a pixel-level screenshot) — hence +1
        let origin = NSPoint(x: (screen.frame.midX - size.width / 2).rounded(), y: screen.frame.maxY - size.height + 1)
        p.setFrame(NSRect(origin: origin, size: size), display: true)
        p.orderFrontRegardless()
        panel = p
    }

    /// Bring up the shelf when the app starts: at rest it's invisible, but the notch's hover zone must work right away,
    /// without ⌘⇧A (after a rebuild Tema would hover the mouse there — and there'd simply be no window). We wait for agents
    /// to arrive from the server: measuring the size off an empty list would give a cramped window; after half a minute we bring it up as is
    func arm(_ model: AppModel) {
        Task {
            for _ in 0..<60 where model.overview["agents"].array.isEmpty {
                try? await Task.sleep(for: .seconds(0.5))
            }
            if !isOpen { toggle(model) }
        }
    }

    /// A voice called Sena: show the shelf (open it if hidden), expand to "Chat" and quietly collapse a while after the
    /// conversation ends, if the cursor isn't over it. Called by Ears.swift when her name was heard
    private var voiceGen = 0
    func showVoice(_ model: AppModel) {
        if !isOpen { toggle(model) }
        NotchState.shared.tab = .chat
        withAnimation(Style.Motion.spring) { NotchState.shared.expanded = true }
        voiceGen += 1
        let gen = voiceGen
        Task {
            while Ears.shared.talking { try? await Task.sleep(for: .seconds(1)) }
            try? await Task.sleep(for: .seconds(8))   // the last words stay readable for a moment
            guard gen == voiceGen, !NotchState.shared.hovering else { return }
            withAnimation(Style.Motion.spring) { NotchState.shared.expanded = false }
        }
    }

    private func makePanel() -> NotchWindow {
        let p = NotchWindow(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel = p
        return p
    }

    /// The screen with the built-in display (the notch), otherwise the main screen
    static func notchScreen() -> NSScreen {
        NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 }) ?? NSScreen.main ?? NSScreen.screens[0]
    }
}
