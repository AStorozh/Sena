// Window: transparent glass across the whole window (the wallpaper and windows behind it show through), with solid white blocks on
// top: on the left a menu header with window buttons and the leaf, and the menu itself with Sena's status, on the right a floating
// section header (shared search, tabs, actions) and the section itself. Menu motion: the selected section's "pill" moves with a
// spring, the icon bounces on a tap, a row shifts slightly on hover. All of it from Style.Motion; the system's "reduce motion" turns animations off.
import SwiftUI

struct ContentView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.openWindow) private var openWindow
    @Environment(\.accessibilityReduceMotion) private var still
    @AppStorage("appTheme") private var theme = "system"   // system | light | dark
    @State private var intro = true   // the launch splash is playing

    var body: some View {
        GeometryReader { g in   // the window's own width goes into the model: content could end up wider than the window before it adapts to narrow mode
            ZStack {
                // a full-window splash on every launch: a tile, a leaf made of petals, rings. The app itself is built while it fades out,
                // and blocks slide in as a cascade (Rise) — this way the launch motion is seen in full, not hidden behind an already-built screen
                if intro { PlaceholderView().transition(.opacity.combined(with: .scale(scale: Style.Motion.splashExit))).zIndex(1) }
                else { layout.transition(.identity) }
            }
            .onAppear { model.setWidth(g.size.width) }.onChange(of: g.size.width) { model.setWidth(g.size.width) }
        }
        .onAppear { openMainWindow = { [openWindow] in openWindow(id: MAIN_WINDOW) } }   // closed later, the window comes back through this
        .motion(Style.Motion.rise, value: intro)
        .environment(\.openedAt, model.openedAt)
        .environment(\.narrowWindow, model.narrow)
        .environment(\.pageSlide, model.slide)
        .task {
            let began = Date()
            do {
                try await Task.sleep(nanoseconds: UInt64((still ? Style.Motion.introShort : Style.Motion.intro) * 1e9))   // the splash is shown for at least this long
                while !(model.up || model.accessProblem != nil) && Date().timeIntervalSince(began) < Style.Motion.introLimit { try await Task.sleep(nanoseconds: 100_000_000) }
                model.openedAt = Date()   // the block cascade is timed from this moment
                intro = false
                try await Task.sleep(nanoseconds: 2_500_000_000)
            } catch { return }   // the window was closed: no point waiting and building the board without it
            BoardScene.prewarm(overview: model.overview, alive: model.alive)   // the integrations board is built ahead of time, once the cascade has played out: the first visit to the section has no pause
        }
        // one shared standard for every element in the window: buttons are SenaButton, fields are a contrasting island (SenaField), toggles are switches, one size, our accent color
        .buttonStyle(.secondary)
        .textFieldStyle(SenaField())
        .toggleStyle(.sena)
        .controlSize(Style.Size.control)
        .tint(Style.Colors.accent)
        .scrollIndicators(.never)   // no scrollbars anywhere in the app (even with the system's "always show" setting): scroll by gesture and wheel
        .preferredColorScheme(appScheme(theme))
        .ignoresSafeArea(.container, edges: .top)   // blocks start right at the top edge: no title bar needed, window buttons live in their own block
        .background(WindowSetup())
        .modifier(WindowGlass())
        .navigationTitle(model.section.title)
        .overlay(alignment: .top) {
            if let t = model.toast {
                ToastBubble(text: t, failed: model.toastFailed)
                    .id(model.toast)   // a new notification plays the badge again
                    .transition(.move(edge: .top).combined(with: .opacity).combined(with: .scale(scale: Style.Motion.riseScale)))
            }
        }
        .motion(value: model.toast)
    }

    /// Two columns: on the left the menu header and the menu itself, on the right the floating section header and the section
    var layout: some View {
        HStack(alignment: .top, spacing: Style.Space.xl) {
            // on the left, two separate blocks: window buttons and the leaf on top, the menu below them; in a narrow window the menu shrinks to icons
            VStack(spacing: Style.Space.xl) {
                MenuHeader()
                SidebarCard()
            }
            .frame(width: model.narrow ? Style.Size.menuNarrow : Style.Size.menuWidth)
            .motion(Style.Motion.spring, value: model.narrow)
            // on the right, two separate blocks on the glass: the section header on top, the section itself below it
            VStack(spacing: Style.Space.xl) {
                Color.clear.frame(height: Style.Size.header)   // room for the floating header (it's drawn on top, see overlayPreferenceValue)
                ZStack {
                    Group { switch model.section {   // the menu's sections (Screen.allCases); tabs are model.tab
                    case .chat: if model.tab(.chat) == 1 { FilesView() } else { ChatView() }
                    case .tasks: if model.tab(.tasks) == 1 { FlowView() } else { TasksView() }
                    case .rules: RulesView()
                    case .panels: PanelsView()   // panels the server describes: goals, the agents' memory, the exam, what waits for a "yes"
                    case .memory:
                        switch model.tab(.memory) { case 1: PeopleView(); case 2: NotesView(); case 3: SkillsView(); default: MemoryCoreView() }
                    case .secrets: SecretsView()
                    case .integrations: IntegrationsView()
                    case .analytics: if model.tab(.analytics) == 0 { CostsView() } else { JournalView() }
                    case .settings:
                        switch model.tab(.settings) { case 1: SystemView(); case 2: ChangesView(); default: BehaviourView() }
                    } }
                    .id(model.screenKey)   // a different section or tab — a new screen: the old one disappears right away, the new one's blocks slide in as a cascade (Rise)
                    .transition(.identity)   // the old screen leaves immediately: two live screens at once (especially with the 3D board and charts) slowed down switching
                    if model.waiting { PlaceholderView().card().transition(.opacity.combined(with: .scale(scale: Style.Motion.splashExit))) }
                }
                .motion(Style.Motion.rise, value: model.waiting)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .modifier(SectionCard(on: !(model.section == .chat && model.tab(.chat) == 0)))   // the section area is a white card with no gaps; the conversation lives without it
            }
            .overlayPreferenceValue(ActionsKey.self, alignment: .top) { actions in
                // the section header is a separate white block, the same on every screen: the section's title, its tab toggles,
                // and the screen's actions on the right. Statuses don't live here: status and spending are in Sena's block at the bottom of the menu
                HeaderBar(actions: actions)
                .padding(.horizontal, Style.Space.gapSmall)   // the standard gap from the block's edge
                .frame(height: Style.Size.header)
                .card()
                .motion(Style.Motion.quick, value: model.section)
            }
        }
        .padding(Style.Space.xl)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// App theme from settings: nil — follow the system
func appScheme(_ theme: String) -> ColorScheme? { theme == "dark" ? .dark : theme == "light" ? .light : nil }

/// A white card behind the section: covers the whole area so the glass underneath doesn't show through. Chat lives without it
struct SectionCard: ViewModifier {
    let on: Bool
    func body(content: Content) -> some View {
        // the standard padding from the card's edge to the blocks — set here, one for all sections (screens don't set their own outer padding)
        if on { content.padding(Style.Space.inset).card() } else { content }
    }
}

/// Header content: shared search, tab toggles, the screen's actions on the right,
/// and shared actions (reply to Sena, stop all tasks, mini panel). The section's title isn't here: the menu shows that
struct HeaderBar: View {
    @EnvironmentObject var model: AppModel
    let actions: AnyView?
    var body: some View {
        let s = model.section
        HStack(spacing: Style.Space.gapSmall) {   // every header element is the same height, in one row, with one gap
            GlobalSearch()
            // on a section change, tabs and actions only cross-fade (opacity), no scaling or shifting: neighbors in the header stay put.
            // Within a section, the same toggle moves itself with a pill (Segments), and the screen's actions smoothly swap one another
            if !s.tabs.isEmpty { Segments(selection: binding(s), options: options(s)).fixedSize().id(s).transition(.opacity) }   // tab labels never shrink to "…": search (an icon in a narrow window) and the screen's actions give way instead
            Spacer(minLength: Style.Space.gapSmall)
            if let actions = actions { actions.id(model.screenKey).transition(.opacity) }
            GlobalActions()
        }
        .motion(Style.Motion.fade, value: model.screenKey)
        .motion(Style.Motion.quick, value: model.ownTasks.isEmpty)
        .motion(value: model.question != nil)
        .motion(value: model.pendingSends.count)
    }
    func binding(_ s: Screen) -> Binding<Int> { Binding(get: { model.tab(s) }, set: { model.switchTab(s, to: $0) }) }
    func options(_ s: Screen) -> [(Int, String)] { Array(s.tabs.enumerated()) }
}

/// Shared header actions, the same in every section: reply to Sena or confirm a send (when she's waiting and the conversation
/// isn't open), stop all tasks (when there's something to stop), and the mini panel over other windows
struct GlobalActions: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        if model.section != .chat || model.tab(.chat) != 0 {
            if model.question != nil {
                Button(L("Ответить Сене")) { model.open(.chat, tab: 0) }.buttonStyle(.primary).help(L("Сена задала вопрос и ждёт ответа"))
                    .transition(Style.Motion.appear)
            }
            if !model.pendingSends.isEmpty {
                Button(String(format: L("Ждут «да» · %d"), model.pendingSends.count)) { model.open(.chat, tab: 0) }.buttonStyle(.primary)
                    .help(L("Сена подготовила действие и ждёт твоего «да»")).transition(Style.Motion.appear)
            }
        }
        if !model.ownTasks.isEmpty {   // his own tasks: "Stop all" leaves Sena's service ones running, a button for them would stop nothing
            Button(L("Стоп"), role: .destructive) { model.stopAllAsking() }.help(L("Остановить все задачи в работе (⌘⇧.)"))
                .transition(Style.Motion.appear)
        }
        IconButton(icon: "rectangle.portrait.on.rectangle.portrait") { MiniPanel.shared.toggle(model) }.help(L("Мини-панель поверх других окон (⌘⇧M)"))
    }
}

/// Shared search over everything Sena knows: tasks, notes, memory, people, conversations (the server's /search — the memory index by meaning and words).
/// Searches itself after a pause in typing; results appear in a popover list, a click goes to the right section. In a narrow window the field
/// collapses into an icon, and the field with results opens below it: this keeps tab labels and the screen's actions readable
struct GlobalSearch: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.colorScheme) private var scheme
    @State private var text = ""
    @State private var found: [JSON] = []   // what was found
    @State private var searched = ""   // what found was searched for: the label above the list doesn't change while typing something new
    @State private var open = false    // the popover: results (wide window) or the field with results (narrow)
    @State private var busy = false
    @FocusState private var focused: Bool

    var body: some View {
        // width depends on space: full width, then narrower, and when there's a lot nearby (tabs, filters, "Reply to Sena") — an icon
        ViewThatFits(in: .horizontal) {
            field.frame(width: Style.Size.search).popover(isPresented: $open, arrowEdge: .bottom) { list }
            field.frame(width: Style.Size.field).popover(isPresented: $open, arrowEdge: .bottom) { list }
            icon
        }
        .task(id: text) {   // search after a pause in typing
            guard text.trimmingCharacters(in: .whitespaces).count >= 2 else { return }
            try? await Task.sleep(nanoseconds: 450_000_000)
            if !Task.isCancelled { await search() }
        }
    }

    private var list: some View { SearchResults(query: searched, results: found) { open = false }.environmentObject(model) }

    /// Collapsed view: an icon, the field and results open below it
    private var icon: some View {
        Button { open = true } label: {
            Glyph("magnifyingglass").font(Style.Icon.inline).frame(width: Style.Size.controlHeight, height: Style.Size.controlHeight).modifier(Pill(scheme: scheme))
        }
        .buttonStyle(PressStyle())
        .help(L("Найти: задачи, заметки, память, люди")).accessibilityLabel(L("Поиск"))
        .popover(isPresented: $open, arrowEdge: .bottom) {
            VStack(spacing: 0) {
                field.padding(Style.Space.inset).frame(width: Style.Size.columnWide)
                if !searched.isEmpty { list }
            }
            .onAppear { focused = true }
        }
    }

    /// The field itself: a dark pill, like every field in the app
    private var field: some View {
        HStack(spacing: Style.Space.s) {
            Glyph("magnifyingglass").font(Style.Icon.inline).foregroundStyle(Style.Colors.secondaryText)
            TextField(L("Найти: задачи, заметки, память, люди"), text: $text).textFieldStyle(.plain).font(Style.Text.body).focused($focused)
                .onSubmit { Task { await search() } }
            if busy { ArcSpinner().transition(Style.Motion.appear) }
            else if !text.isEmpty {
                Button { text = ""; found = []; searched = ""; open = false } label: { Glyph("xmark.circle.fill").foregroundStyle(Style.Colors.secondaryText) }
                    .buttonStyle(.plain).accessibilityLabel(L("Очистить поиск")).transition(Style.Motion.appear)
            }
        }
        .padding(.horizontal, Style.Space.xl)
        .frame(height: Style.Size.controlHeight)
        .modifier(Pill(scheme: scheme))
        .modifier(FieldHover())
        .motion(value: busy)
        .motion(value: text.isEmpty)
    }

    /// A dark pill (light in dark mode) with its own inverted theme inside
    private struct Pill: ViewModifier {
        let scheme: ColorScheme
        func body(content: Content) -> some View {
            content.background(Style.Colors.leadBlock(scheme), in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
                .environment(\.colorScheme, Style.Colors.opposite(scheme))
        }
    }

    func search() async {
        let q = text.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return }
        busy = true; defer { busy = false }
        do {
            let r = try await model.backend.api("/search?q=\(Backend.enc(q))").array
            guard q == text.trimmingCharacters(in: .whitespaces) else { return }   // something different was typed while the request was in flight: the old reply doesn't override the new one
            found = r; searched = q; open = true
        } catch {
            if Task.isCancelled || (error as? URLError)?.code == .cancelled { return }   // another letter was typed: the request was canceled, that's not an error
            model.say(String(format: L("Поиск: %@"), error.localizedDescription))
        }
    }
}

/// The kind of a found result, for display: on the server the kind is a key of the memory index (index.KINDS); tests/test_mac.py
/// checks that every kind search can return is named here and has a place in SearchResults.go
let SEARCH_KIND_RU = ["задача": L("задача"), "факт": L("память"), "человек": L("человек"), "заметка": L("заметка"), "правило": L("правило"),
                      "навык": L("навык"), "урок": L("урок агента"), "цель": L("цель"), "сообщение": L("сообщение"), "услышано": L("услышано"),
                      "файл": L("файл"), "проект": L("проект"), "организация": L("организация"), "место": L("место")]

struct SearchResults: View {
    @EnvironmentObject var model: AppModel
    let query: String
    let results: [JSON]
    let close: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.gapSmall) {
            Text(results.isEmpty ? String(format: L("По «%@» ничего не нашлось"), query)
                                 : "\(results.count) \(Fmt.plural(results.count, "находка", "находки", "находок")) " + String(format: L("по «%@»"), query))
                .font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
            ScrollView {
                VStack(spacing: Style.Space.xxs) {
                    ForEach(Array(results.enumerated()), id: \.offset) { _, r in
                        HStack(alignment: .top, spacing: Style.Space.m) {
                            Tag(text: SEARCH_KIND_RU[r["kind"].str] ?? r["kind"].str, color: Style.Colors.tint(r["kind"].str))
                            VStack(alignment: .leading, spacing: Style.Space.xxs) {
                                if let title = r["title"].string { Text(title).font(Style.Text.heading) }
                                Text(r["text"].str).font(Style.Text.small).lineLimit(3)
                                Text(Fmt.when(r["time"])).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
                            }
                            Spacer(minLength: 0)
                        }
                        .row()
                        .onTapGesture { go(r); close() }
                    }
                }
            }
            .scrollIndicators(.never)
            .frame(maxHeight: Style.Size.flowHeight * 4)
        }
        .padding(Style.Space.inset)
        .frame(width: Style.Size.columnWide)
    }
    /// Where a found result leads: to the screen that holds it, with the record itself selected there (model.focus).
    /// The kinds and their refs are the memory index's: a person "id" or "id:fact", a note or an entity "Папка/Имя.md#часть",
    /// a file its path under data/, a message "chat:id", a goal or a lesson its id (they live in the server's panels)
    func go(_ r: JSON) {
        let ref = r["ref"].str
        switch r["kind"].str {
        case "задача": model.openTask(ref)
        case "факт": model.open(.memory, tab: 0)
        case "человек": reveal(.memory, tab: 1, String(ref.split(separator: ":").first ?? ""))
        case "заметка", "проект", "организация", "место": reveal(.memory, tab: 2, r["path"].string ?? String(ref.split(separator: "#").first ?? ""))
        case "навык": reveal(.memory, tab: 3, ref)
        case "правило": model.open(.rules)
        case "цель": reveal(.panels, tab: 0, "goals")
        case "урок": reveal(.panels, tab: 0, "lessons")
        case "файл": reveal(.chat, tab: 1, ref)
        default:   // a message: the conversation scrolls to it; a long one is indexed in pieces ("chat:id", "#2", "#3"…), each leads to the message
            let id = ref.split(separator: ":", maxSplits: 1).last ?? ""
            reveal(.chat, tab: 0, String(id.split(separator: "#", maxSplits: 1).first ?? ""))
        }
    }
    private func reveal(_ s: Screen, tab: Int, _ target: String) { if model.open(s, tab: tab) { model.focus = target } }
}

/// A separate block above the menu: window buttons and Sena's leaf. In a narrow window — just the window buttons
struct MenuHeader: View {
    @EnvironmentObject var model: AppModel
    @State private var leafReplay = 0
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        HStack(spacing: Style.Space.m) {
            WindowLights()
            if !model.narrow {
                LeafMark(size: Style.Icon.leafMenu, color: Color(nsColor: Style.Colors.leaf(scheme)), mode: model.alive ? .alive : .still, replay: leafReplay)
                    .padding(.leading, Style.Space.xs).hoverScale(Style.Motion.leafHover)
                    .onTapGesture { leafReplay += 1 }   // tapped the leaf — petals fly apart and reassemble
                    .onChange(of: model.alive) { leafReplay += 1 }   // Sena turned on or off — the leaf reassembles
                Spacer()
            }
        }
        .padding(.leading, model.narrow ? Style.Space.xs : Style.Space.gap).padding(.trailing, model.narrow ? Style.Space.xs : Style.Space.gapSmall)
        .frame(maxWidth: .infinity, alignment: .leading)
        .frame(height: Style.Size.header)
        .card()
    }
}

/// Left block: sections, with Sena's card and button at the bottom. In a narrow window — just icons (tabs become pills in the header)
struct SidebarCard: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.colorScheme) private var scheme
    @Namespace private var ns   // a shared "pill" for every row: it moves between them instead of appearing anew
    var body: some View {
        let compact = model.narrow
        VStack(alignment: .leading, spacing: 0) {
            ScrollView {
                VStack(spacing: Style.Space.xxs) {
                    ForEach(Screen.allCases) { s in SidebarRow(screen: s, count: count(for: s), compact: compact, ns: ns) }
                }
                .padding(.horizontal, compact ? Style.Space.xs : Style.Space.gapSmall).padding(.vertical, Style.Space.gapSmall)
                .motion(Style.Motion.spring, value: model.section)
                            }
            if compact { compactStatus } else { fullStatus }
        }
        .card(Style.Colors.sidebar(scheme))
    }
    /// Sena's card: status, today's spending, a full-width on/off button
    var fullStatus: some View {
        VStack(alignment: .leading, spacing: Style.Space.s) {
            HStack(spacing: Style.Space.s) {
                StatusDot(ok: model.alive, bad: model.lost)
                Text(model.stateText).font(Style.Text.heading).lineLimit(1).fixedSize().id(model.stateText).transition(.opacity.combined(with: .offset(y: Style.Space.xs)))
            }
            HStack(alignment: .firstTextBaseline) {
                TimelineView(.periodic(from: .now, by: 1)) { ctx in   // the uptime runs on its own: an idle overview isn't republished
                    Text(model.alive ? String(format: L("%@ · %@"), Fmt.dur(ctx.date.timeIntervalSince1970 - model.overview["core"]["started"].dbl),
                                             model.ownTasks.isEmpty ? L("свободна") : model.inWorkText)
                                     : model.offText)
                }
                    .font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
                if model.alive {   // off — no spending, nothing to show "$0.0000" for
                    Spacer()
                    Text(Fmt.usd(model.overview["today"]["usd"].dbl)).font(Style.Text.caption.monospacedDigit()).foregroundStyle(Style.Colors.tertiaryText).rolling(model.overview["today"]["usd"].dbl)
                        .help(L("расход сегодня") + (model.overview["budget"]["limit"].dbl > 0 ? String(format: L(" из %@"), Fmt.usd(model.overview["budget"]["limit"].dbl)) : ""))
                }
            }
            if model.up {
                Button { model.toggleCore() } label: {
                    HStack(spacing: Style.Space.s) {
                        Glyph("power", trigger: model.alive ? 1 : 0)   // turned on or off — the icon spins up
                        Text(model.alive ? L("Остановить Сену") : L("Запустить Сену"))
                    }
                    .frame(maxWidth: .infinity)
                        .id(model.alive).transition(.asymmetric(insertion: .move(edge: .bottom).combined(with: .opacity), removal: .move(edge: .top).combined(with: .opacity)))
                }
                .buttonStyle(.primary)
                .padding(.top, Style.Space.xs)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Style.Space.gap)
        .blockSurface(dark: true)   // Sena's block at the bottom of the menu is contrasting, like the sections' main blocks
        .padding(Style.Space.gapSmall)
        .motion(Style.Motion.quick, value: model.alive)
    }
    /// Same in a narrow window: a status dot and an icon-button to turn it on
    var compactStatus: some View {
        VStack(spacing: Style.Space.s) {
            StatusDot(ok: model.alive, bad: model.lost)
            if model.up {
                IconButton(icon: "power") { model.toggleCore() }
                    .help(model.alive ? L("Остановить Сену") : L("Запустить Сену"))
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Style.Space.gapSmall)
        .blockSurface()
        .padding(Style.Space.xs)
    }
    func count(for s: Screen) -> Int {
        switch s {
        case .tasks: return model.ownTasks.count
        case .chat: return (model.question == nil ? 0 : 1) + model.pendingSends.count
        case .panels: return model.panelBadge   // the server's panels: what waits for him there
        default: return 0
        }
    }
}

/// A section row: a thin icon, a title, a round counter on the right. The selected section is a black "pill" with white text
/// (reversed in dark mode); clicking the row selects the section. In a narrow window — just the icon
struct SidebarRow: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.colorScheme) private var scheme
    let screen: Screen
    let count: Int
    var compact = false
    let ns: Namespace.ID
    @State private var hover = false
    @State private var taps = 0   // the icon plays its motion on every tap
    @State private var plays = 0  // and when the section becomes selected (the menu, the ⌘1… keys)
    var body: some View {
        let on = model.section == screen
        let fg: Color = on ? Style.Colors.pillText(scheme) : .primary
        HStack(spacing: 0) {
            Button { taps += 1; model.open(screen) } label: {
                HStack(spacing: Style.Space.m) {
                    Glyph(screen.icon, trigger: taps + plays).font(Style.Icon.menu).frame(width: Style.Icon.menuWidth)
                        .overlay(alignment: .topTrailing) {   // in a narrow window the counter is a dot next to the icon
                            if compact && count > 0 { Circle().fill(Style.Colors.warn).frame(width: Style.Size.lineDot, height: Style.Size.lineDot) }
                        }
                    if !compact {
                        Text(screen.title).font(Style.Text.body.weight(on ? .medium : .regular))
                        Spacer()
                        if count > 0 {
                            Text("\(count)").font(Style.Text.tiny.weight(.semibold))
                                .contentTransition(.numericText())
                                .foregroundStyle(on ? Style.Colors.pill(scheme) : Style.Colors.pillText(scheme))
                                .frame(minWidth: Style.Size.counter, minHeight: Style.Size.counter)
                                .background(on ? Style.Colors.pillText(scheme) : Style.Colors.pill(scheme), in: Circle())
                                .transition(.scale.combined(with: .opacity))
                        }
                    }
                }
                .padding(.horizontal, compact ? 0 : Style.Space.xl).padding(.vertical, Style.Space.gapSmall)
                .frame(maxWidth: .infinity, alignment: compact ? .center : .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(PressStyle())
            .accessibilityLabel(screen.title)
            .help(compact ? screen.title : "")
        }
        .foregroundStyle(fg)
        .environment(\.hovering, hover)
        .onChange(of: on) { _, v in if v { plays += 1 } }
        .offset(x: hover && !on && !compact ? Style.Motion.hoverShift : 0)
        .background {
            if on { RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).fill(Style.Colors.pill(scheme)).matchedGeometryEffect(id: "pill", in: ns) }
            else { RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).fill(Style.Colors.hover(scheme)).opacity(hover ? 1 : 0) }
        }
        .onHover { hover = $0 }
        .motion(Style.Motion.spring, value: hover)
        .motion(Style.Motion.spring, value: count)
    }
}

/// While the server isn't up yet: a tile with a leaf, petals fly in and breathe with a wave, rings spread from the tile; if there's no access to the folder — buttons.
/// Once the server has answered, the splash fades out and grows slightly, and the app opens behind it as a cascade
struct PlaceholderView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var still
    @State private var shown = false
    var body: some View {
        VStack(spacing: Style.Space.xxl) {
            ZStack {
                if model.accessProblem == nil && !still { SonarRings() }
                RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).fill(Style.Colors.tile).frame(width: Style.Size.tile, height: Style.Size.tile)
                    .shadow(color: .black.opacity(Style.Motion.liftShadow), radius: Style.Size.shadowRadius * 2, y: Style.Size.shadowY * 2)
                LeafMark(size: Style.Icon.leafTile, mode: model.accessProblem == nil ? .wave : .assemble)
            }
            .frame(height: Style.Size.tile * Style.Motion.sonarReach)
            .scaleEffect(shown || still ? 1 : Style.Motion.splashEnter).opacity(shown || still ? 1 : 0)
            if let problem = model.accessProblem {
                Text(model.accessNeedsKey ? L("В .env нет ключа приложения") : L("Нужен доступ к папке проекта")).font(Style.Text.title)
                Text(problem).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText).multilineTextAlignment(.center).frame(maxWidth: Style.Size.columnWide + Style.Size.column)
                HStack {
                    if model.accessNeedsKey { Button(L("Папка проекта")) { NSWorkspace.shared.open(model.backend.root) } }
                    else { Button(L("Открыть настройки")) { model.openPrivacy() } }
                    Button(L("Повторить")) { model.start() }.buttonStyle(.primary)
                }
            } else {
                Text(model.status).font(Style.Text.title).multilineTextAlignment(.center)
                    .id(model.status).transition(.asymmetric(insertion: .opacity.combined(with: .offset(y: Style.Space.m)), removal: .opacity))
                if model.launchFailed { Button(L("Открыть лог")) { model.openLog() } }
            }
        }
        .motion(value: model.status)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.background)
        .onAppear { withAnimation(Style.Motion.rise.delay(Style.Motion.emptyDelay)) { shown = true } }
    }
}

/// Rings from the tile: spread out and fade, one after another
struct SonarRings: View {
    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 60)) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate / Style.Motion.sonarPeriod
            ZStack {
                ForEach(0..<3, id: \.self) { i in
                    let phase = (t + Double(i) / 3) - (t + Double(i) / 3).rounded(.down)
                    RoundedRectangle(cornerRadius: Style.Radius.all * (1 + phase), style: .continuous)
                        .strokeBorder(Color(nsColor: YELLOW).opacity((1 - phase) * Style.Motion.sonarOpacity), lineWidth: Style.Size.line * 2)
                        .frame(width: Style.Size.tile * (1 + phase * (Style.Motion.sonarReach - 1)), height: Style.Size.tile * (1 + phase * (Style.Motion.sonarReach - 1)))
                }
            }
        }
        .allowsHitTesting(false)
    }
}
