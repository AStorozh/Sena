// Sena for Mac: a native SwiftUI app. A window without a title bar (blocks on glass, see ContentView), a "Control" menu and a
// "Sections" menu (⌘1… — sections), a menu bar icon with a short status and quick actions. Data comes from Sena's server
// (sena/phone/desk.py), which the app starts itself in "screen without core" mode; a button turns Sena herself on. Build: mac/build.sh.
import SwiftUI
import UserNotifications

/// App name in the system's language (Resources/*/InfoPlist.strings): "Сена" or "Sena"
let APP_NAME = (Bundle.main.localizedInfoDictionary?["CFBundleName"] as? String) ?? "Сена"
let MAIN_WINDOW = "main"   // the main window's scene: closed, it is built again by showMainWindow (Widgets.swift)

@main
struct SenaApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var model = AppModel()
    @ObservedObject private var mini = MiniPanel.shared     // the menu bar's "show / hide" follows the panels themselves
    @ObservedObject private var notch = NotchPanel.shared

    init() {
        if CommandLine.arguments.count >= 3, CommandLine.arguments[1] == "--icon" {   // build mode: draw the icon and exit
            makeIcons(dir: URL(fileURLWithPath: CommandLine.arguments[2]))
            exit(0)
        }
        NSScrollView.hideScrollers()
    }

    var body: some Scene {
        WindowGroup(APP_NAME, id: MAIN_WINDOW) {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: Style.Size.minWidth, minHeight: Style.Size.minHeight)
                .onAppear { model.start(); Ears.shared.sync(model); NotchPanel.shared.arm(model) }   // Mac listening and the notch shelf — right away
        }
        .windowStyle(.hiddenTitleBar)   // no title bar: blocks start right at the top edge, window buttons are our own, in the left block
        .defaultSize(width: 1280, height: 820)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandMenu(L("Управление")) {   // not "Sena": the system already keeps the app menu under that name
                Button(model.alive ? L("Остановить Сену") : L("Запустить Сену")) { model.toggleCore() }.disabled(!model.up)
                Button(L("Перезапустить Сену")) { model.restartAsking() }.disabled(!model.alive)
                Button(L("Остановить все задачи")) { model.stopAllAsking() }.keyboardShortcut(".", modifiers: [.command, .shift]).disabled(model.ownTasks.isEmpty)
                Divider()
                Button(L("Мини-панель")) { MiniPanel.shared.toggle(model) }.keyboardShortcut("m", modifiers: [.command, .shift])
                Button(L("Шторка у выреза")) { NotchPanel.shared.toggle(model) }.keyboardShortcut("a", modifiers: [.command, .shift])
                Divider()
                Button(L("Папка проекта")) { NSWorkspace.shared.open(model.backend.root) }
                Button(L("Лог запуска")) { model.openLog() }
            }
            CommandMenu(L("Разделы")) {   // not "View": the system already has a menu with that name (full screen, sidebar)
                ForEach(Array(Screen.allCases.enumerated()), id: \.element) { i, s in
                    let item = Button(s.title) { model.open(s) }
                    if i < 9 { item.keyboardShortcut(KeyEquivalent(Character("\(i + 1)")), modifiers: .command) } else { item }   // ⌘1…⌘9: the digits cover nine sections
                }
            }
        }

        // menu bar icon: a plain menu — status and quick actions; widgets live in one place, the mini panel
        MenuBarExtra {
            Text(model.stateText + (model.alive && !model.ownTasks.isEmpty ? String(format: L(" · задач в работе: %d"), model.ownTasks.count) : ""))
            if model.question != nil { Button(L("Ответить Сене")) { openApp(model, .chat, tab: 0) } }
            Divider()
            Button(L("Открыть приложение")) { openApp(model, .chat) }
            Button(mini.shown ? L("Скрыть мини-панель") : L("Мини-панель")) { MiniPanel.shared.toggle(model) }
            Button(notch.shown ? L("Скрыть шторку") : L("Шторка у выреза")) { NotchPanel.shared.toggle(model) }
            Divider()
            Button(L("Выйти")) { NSApp.terminate(nil) }
        } label: {
            Image(nsImage: leafImage(size: 20, color: .black, template: true))
            if model.question != nil { Text("?") }   // Sena is waiting for an answer
        }
        .menuBarExtraStyle(.menu)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        // the computer agent's clicks and keys count as this app's in macOS: without «Универсальный доступ» they were dropped
        // without a word (03.11.2026). Not granted — the system's own window offers to turn it on; granted — nothing is shown
        if !AXIsProcessTrusted() { AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary) }
    }
    /// ⌘Q: an unsaved edit doesn't disappear silently
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        (MainActor.assumeIsolated { AppModel.current?.discardUnsaved() } ?? true) ? .terminateNow : .terminateCancel
    }
    /// A click on the Dock icon with no window on screen: the same way back as from the menu bar and the shelf (false — done here)
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        flag || !MainActor.assumeIsolated { showMainWindow() }
    }
    func applicationWillTerminate(_ notification: Notification) { Backend.shared.stopScreen() }   // the screen-without-core server is only needed by the window; the core lives on its own
}

// There are no scrollbars anywhere in the app. The main mechanism is `.scrollIndicators(.never)` at the root of the window, sheets and popovers;
// this is a safety net for when SwiftUI sets up an NSScrollView on its own (a table, a text editor) and that setting never reached it: scrollbars
// get turned off for every one that ends up in the window. Scrolling still works as usual — by gesture and wheel
extension NSScrollView {
    static func hideScrollers() {
        // viewDidMoveToWindow is declared on NSView, not NSScrollView: the method is first added to NSScrollView itself, and only then are the
        // implementations swapped — otherwise the call would get replaced for every view in the app
        let cls: AnyClass = NSScrollView.self
        let original = #selector(NSView.viewDidMoveToWindow), mine = #selector(NSScrollView.senaViewDidMoveToWindow)
        guard let o = class_getInstanceMethod(cls, original), let m = class_getInstanceMethod(cls, mine) else { return }
        if class_addMethod(cls, original, method_getImplementation(m), method_getTypeEncoding(m)) {
            class_replaceMethod(cls, mine, method_getImplementation(o), method_getTypeEncoding(o))
        } else {
            method_exchangeImplementations(o, m)
        }
    }
    @objc fileprivate func senaViewDidMoveToWindow() {
        senaViewDidMoveToWindow()   // after the swap this is the original implementation
        hasVerticalScroller = false
        hasHorizontalScroller = false
    }
}
