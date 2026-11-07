// Widgets: small live blocks for keeping an eye on Sena while doing other work. They live in one place — the floating mini
// panel over all windows (MiniPanel; ⌘⇧M inside the app and a menu bar icon), which shows all kinds at once. Data comes from
// model.overview (every 2 s), the event feed has its own polling. The question and sends widgets show up only when there's something to decide.
import SwiftUI

enum WidgetKind: String, CaseIterable, Identifiable {
    case state, question, sends, tasks
    var id: String { rawValue }
    var title: String {
        switch self {
        case .state: return L("Состояние"); case .question: return L("Вопрос Сены"); case .sends: return L("На подтверждение"); case .tasks: return L("В работе")
        }
    }
}

/// A stack of widgets with the given composition
struct WidgetStack: View {
    @EnvironmentObject var model: AppModel
    let kinds: [WidgetKind]
    var body: some View {
        VStack(spacing: Style.Space.gap) {
            ForEach(kinds) { k in Widget(kind: k).transition(Style.Motion.appear) }
        }
        .motion(value: model.question == nil)
        .motion(value: model.pendingSends.count)
        .motion(value: model.ownTasks.count)
    }
}

/// The main window's own SwiftUI action, kept by ContentView when it appears: the shelf and the mini panel are AppKit panels
/// outside the app's scenes, and a closed window can only be built again by SwiftUI
@MainActor var openMainWindow: (() -> Void)? = nil

/// Bring the main window up: unhidden, deminiaturized, in front — or built again if it was closed. false — nothing could show it
@MainActor @discardableResult func showMainWindow() -> Bool {
    NSApp.unhide(nil)
    NSApp.activate(ignoringOtherApps: true)
    if let w = NSApp.windows.first(where: { $0.canBecomeMain && !($0 is NSPanel) && ($0.isVisible || $0.isMiniaturized) }) {
        if w.isMiniaturized { w.deminiaturize(nil) }
        w.makeKeyAndOrderFront(nil)
        return true
    }
    guard let open = openMainWindow else { return false }
    open()
    return true
}

/// Open the main window to the given section and tab
@discardableResult @MainActor func openApp(_ model: AppModel, _ section: Screen, tab: Int = 0) -> Bool {
    showMainWindow()
    return model.open(section, tab: tab)
}

struct Widget: View {
    @EnvironmentObject var model: AppModel
    let kind: WidgetKind
    var body: some View {
        switch kind {
        case .state: StateWidget()
        case .question: if let q = model.question { QuestionWidget(question: q) }
        case .sends: if !model.pendingSends.isEmpty { SendsWidget() }
        case .tasks: TasksWidget()
        }
    }
}

// ---- state: dot, uptime, start/stop ----
struct StateWidget: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        let o = model.overview
        Block(WidgetKind.state.title, lead: true, content: {
            HStack(spacing: Style.Space.s) {
                StatusDot(ok: model.alive, bad: model.lost)
                Text(model.stateText).font(Style.Text.heading)
                Spacer()
                if model.alive {   // the uptime runs on its own: an idle overview isn't republished
                    TimelineView(.periodic(from: .now, by: 1)) { ctx in
                        Text(Fmt.dur(ctx.date.timeIntervalSince1970 - o["core"]["started"].dbl)).font(Style.Text.caption.monospacedDigit()).foregroundStyle(Style.Colors.secondaryText).contentTransition(.numericText())
                    }
                }
            }
            Text(model.alive ? (model.ownTasks.isEmpty ? L("свободна") : model.inWorkText) : model.offText)
                .font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
            HStack(spacing: Style.Space.gapSmall) {
                if model.up {
                    Button(model.alive ? L("Остановить") : L("Запустить")) { model.toggleCore() }.buttonStyle(.primary)
                }
                if model.alive { Button(L("Перезапустить")) { model.restartAsking() } }
                Spacer()
                if !model.ownTasks.isEmpty { Button(L("Остановить все"), role: .destructive) { model.stopAllAsking() } }
            }
        }, trailing: { Button(L("Задачи")) { openApp(model, .tasks) }.buttonStyle(.quiet) })
    }
}

// ---- Sena's question: answer without opening the window (with an image if there is one: captcha, screenshot) ----
struct QuestionWidget: View {
    let question: JSON
    var body: some View {
        Block(WidgetKind.question.title, lead: true, content: {
            QuestionBody(question: question, lines: 5).id(question["time"].dbl)
        }, trailing: { Tag(text: L("ждёт ответа"), color: Style.Colors.warn) })
    }
}

// ---- what Sena prepared and waits for a "yes" on: the whole text, confirmed by the user ----
struct SendsWidget: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        Block(WidgetKind.sends.title) {
            ForEach(model.pendingSends, id: \.["id"].str) { p in
                VStack(alignment: .leading, spacing: Style.Space.xs) {
                    Text(sendTitle(p)).font(Style.Text.heading)
                    SendBody(send: p)
                }
            }
        }
    }
}

// ---- tasks in progress: step, agent, time, stop ----
struct TasksWidget: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        Block(WidgetKind.tasks.title, content: {
            if model.ownTasks.isEmpty {
                Text(model.alive ? L("Сена свободна") : L("Сена выключена")).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
            }
            ForEach(model.ownTasks, id: \.["tid"].str) { t in
                let last = t["steps"].array.last
                HStack(alignment: .top, spacing: Style.Space.m) {
                    AgentIcon(code: last?["who"].str ?? "ag_sena")
                    VStack(alignment: .leading, spacing: Style.Space.xxs) {
                        Text(t["text"].str).font(Style.Text.body.weight(.medium)).lineLimit(2)
                        HStack(spacing: Style.Space.s) {
                            if last == nil { Text(L("начало")).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText) }
                            else { Text(last?["text"].str ?? "").font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(1) }
                        }
                        HStack(spacing: Style.Space.m) {
                            Text(Fmt.dur(model.overview["now"].dbl - t["start"].dbl)).contentTransition(.numericText())
                            if t["cost"].dbl > 0 { Text(Fmt.usd(t["cost"].dbl)) }
                            Text("\(t["steps"].array.count) \(Fmt.plural(t["steps"].array.count, "шаг", "шага", "шагов"))")
                        }
                        .font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
                    }
                    Spacer()
                    Button(L("Стоп"), role: .destructive) { model.stopTask(AppModel.taskID(t), title: t["text"].str) }.buttonStyle(.quiet)
                }
                .contentShape(Rectangle())
                .onTapGesture { openTask(t) }
                .help(L("Открыть"))
                .accessibilityAddTraits(.isButton)
                .accessibilityAction(.default) { openTask(t) }
            }
        }, trailing: { if !model.ownTasks.isEmpty { TypingDots() } })
    }
    func openTask(_ t: JSON) { if openApp(model, .tasks) { model.selectedTask = AppModel.taskID(t) } }
}

// ---- mini panel: a floating window over all apps with the chosen widgets ----
/// Header in one row: leaf and title, on the right the widget picker, "open app" and "close" as icon buttons (the system window buttons are hidden).
/// The panel lives without the main window, so the action-result toast and "no connection" are shown here on their own
struct MiniPanelView: View {
    @EnvironmentObject var model: AppModel
    @AppStorage("appTheme") private var theme = "system"   // same as in the window: system | light | dark
    var body: some View {
        VStack(spacing: Style.Space.gap) {
            HStack(spacing: Style.Space.gapSmall) {
                Image(nsImage: leafImage(size: Style.Icon.leafToolbar, color: YELLOW, template: false))
                Text(L("Мини-панель")).font(Style.Text.heading).lineLimit(1)
                Spacer()
                IconButton(icon: "arrow.up.forward.app", label: L("Открыть приложение")) { openApp(model, .chat) }.help(L("Открыть приложение"))
                IconButton(icon: "xmark", label: L("Закрыть мини-панель")) { MiniPanel.shared.toggle(model) }.help(L("Закрыть мини-панель"))
            }
            if model.lost {   // the server isn't responding and nobody asked for that: what's shown below is the last known state
                Text("\(L("Нет связи")): \(L("сервер не отвечает"))").font(Style.Text.caption).foregroundStyle(Style.Colors.badText).frame(maxWidth: .infinity, alignment: .leading)
            }
            ScrollView { WidgetStack(kinds: WidgetKind.allCases).padding(.bottom, Style.Space.gap) }
        }
        .padding(Style.Space.inset)
        .frame(minWidth: Style.Size.columnWide, minHeight: Style.Size.flowHeight * 2)
        .background(Style.Colors.paper)   // the same paper as the window's cards; theme follows the app
        .overlay(alignment: .top) {
            if let t = model.toast {
                ToastBubble(text: t, failed: model.toastFailed).id(model.toast).transition(Style.Motion.appear)
            }
        }
        .motion(value: model.toast)
        .sheetStyle()
        .preferredColorScheme(appScheme(theme))
    }
}

/// Mini panel window: doesn't take focus away from the current app, floats over all windows and all desktops, remembers position and size.
/// A closed panel tears down its content: a hidden window shouldn't keep polling the server
@MainActor final class MiniPanel: ObservableObject {
    static let shared = MiniPanel()
    private var panel: NSPanel?
    var isOpen: Bool { panel?.isVisible == true }
    @Published private(set) var shown = false   // the same, for the menu bar's item: it reads "hide" while the panel is up

    func toggle(_ model: AppModel) {
        defer { shown = isOpen }
        if isOpen { panel?.orderOut(nil); panel?.contentView = nil; return }
        if panel == nil {
            let p = NSPanel(contentRect: NSRect(x: 0, y: 0, width: Style.Size.columnWide, height: Style.Size.flowHeight * 5),
                            styleMask: [.titled, .closable, .resizable, .fullSizeContentView, .nonactivatingPanel, .utilityWindow], backing: .buffered, defer: false)
            p.title = L("Мини-панель Сены")
            p.titleVisibility = .hidden
            p.titlebarAppearsTransparent = true
            for kind in [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton] { p.standardWindowButton(kind)?.isHidden = true }   // there's our own "close" button in the header
            p.isFloatingPanel = true
            p.level = .floating
            p.hidesOnDeactivate = false
            p.becomesKeyOnlyIfNeeded = true
            p.isMovableByWindowBackground = true
            p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            p.isReleasedWhenClosed = false
            p.setFrameAutosaveName("MiniPanel.v2")   // new name: the old saved size was narrower than the current one
            if p.frame.origin == .zero { p.center() }
            panel = p
        }
        panel?.contentView = NSHostingView(rootView: MiniPanelView().environmentObject(model))
        panel?.orderFront(nil)
    }
}
