// Chat with Sena: a conversation as bubbles, photos, videos and files (a paperclip, drag and drop, pasting from the clipboard), voice
// messages (recording from the mic -> server transcription -> sending), live steps and "Sena is typing", Sena's question and sends
// awaiting confirmation right in the feed. In the header — search through the conversation (the feed keeps only matches), jump to a day, and clear.
// The screen gets rebuilt from scratch on every section change, so anything that shouldn't disappear (attachments, the addressee, sent-but-unanswered,
// waiting for a reply) lives in ChatSession, and the typed text lives in model.chatDraft. The "Files" tab is FilesView.
import SwiftUI
import UniformTypeIdentifiers

struct ChatView: View {
    @EnvironmentObject var model: AppModel
    @ObservedObject private var session = ChatSession.shared
    @Environment(\.accessibilityReduceMotion) private var still
    @Environment(\.narrowWindow) private var narrow
    @StateObject private var recorder = Recorder()
    @FocusState private var typing: Bool
    @State private var text = ""
    @State private var transcribing = false
    @State private var dropping = false
    @State private var pasteMonitor: Any? = nil
    @State private var find = ""                      // search through the conversation: the feed keeps only matches
    @State private var jumpDate = Date()              // jump to a day
    @State private var jumpTo = ""                    // the id of the message to scroll to
    @State private var jumpTick = 0
    @State private var window = 60                    // how many of the latest messages are shown: 300 bubbles at once used to slow down opening chat
    @State private var nearEnd = true                 // the feed is scrolled to the end: a new message carries it down, reading an old one doesn't yank it
    @State private var firstLoad = true               // the first load lands at the end without animating through the whole feed
    static let direct = ["ag_sena", "ag_web", "ag_mac"]
    static let page = 80                              // how many older messages "Show earlier" adds
    static let batch = 300                            // how many of the latest messages are normally requested from the server
    static let deepest = 5000                         // and how many while searching through the whole history
    static let followGap = Style.Size.thumb           // closer than this to the end, the feed is considered scrolled to the end

    var messages: [JSON] { session.messages }         // the conversation lives in ChatSession: the shelf shows the very same one
    var total: Int { session.total }                  // how many messages the server holds; messages only has the latest of them
    var loadedOnce: Bool { session.loaded }           // the history arrived at least once: before that, loading instead of "empty"
    var loadError: String? { session.loadError }      // the history never arrived: a reason and "Retry" instead of endless loading
    var query: String { find.trimmingCharacters(in: .whitespaces) }
    var reach: Int { query.isEmpty ? min(ChatView.deepest, max(ChatView.batch, window + ChatView.page)) : ChatView.deepest }
    var canSend: Bool { (!text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !session.files.isEmpty) && session.uploading == 0 }
    var spring: Animation? { still ? nil : Style.Motion.spring }   // for withAnimation; modifiers use .motion
    var placeholder: String {
        if !model.alive { return model.up ? L("Сена выключена — включи её кнопкой в меню") : L("Нет связи с сервером") }
        if model.question != nil { return L("Ответ на вопрос Сены…") }
        return session.agent == "ag_sena" ? L("Напиши Сене…") : String(format: L("Поручение для %@…"), session.agent)
    }

    var pending: [JSON] { session.pending }
    func visible(_ all: [JSON]) -> [JSON] {
        if !query.isEmpty { return all.filter { $0["content"].str.localizedCaseInsensitiveContains(query) } }   // search runs over the whole loaded history
        return all.count > window ? Array(all.suffix(window)) : all
    }

    var body: some View {
        let all = messages + pending   // computed once per redraw: the feed, counters and scrolling are all based on this one list
        let rows = visible(all)
        VStack(spacing: Style.Space.xl) {
            HStack(alignment: .top, spacing: 0) {   // the conversation sits on a white card; her thoughts in a dark block on its left
                if !narrow { Thoughts(steps: session.thoughts, working: !session.sent.isEmpty).padding(Style.Space.inset) }
                feed(rows, count: all.count)
            }
            .blockSurface(radius: Style.Radius.all)
            composer
        }
        .defaultFocus($typing, true)
        .overlay { if dropping { DropHint().transition(.opacity.combined(with: .scale(scale: Style.Motion.pressScale))) } }
        .motion(Style.Motion.quick, value: dropping)
        .onDrop(of: [.fileURL], isTargeted: $dropping) { providers in dropFiles(providers) { session.upload(model, $0) } }
        .task {
            if let a = model.pendingAttachment { session.add(a); model.pendingAttachment = nil }
            session.reach = reach
            await refresh()
            pickMessage()
        }
        .onChange(of: reach) { session.reach = reach }
        .onChange(of: model.focus) { pickMessage() }
        .onAppear {
            session.watch(model)   // the shared poll runs while the chat or the shelf's chat is on screen
            watchPaste()
            if text.isEmpty { text = model.chatDraft }
            if let t = model.chatTarget { session.agent = t; model.chatTarget = nil }   // "Assign in chat" from an agent card
            takeRestored()
        }
        .onDisappear {
            session.unwatch()
            if let m = pasteMonitor { NSEvent.removeMonitor(m); pasteMonitor = nil }
            model.chatDraft = text
            if recorder.recording { recorder.cancel(); model.say(L("Запись отменена: ты ушёл из чата")) }
        }
        .onChange(of: session.restored) { takeRestored() }
        .onChange(of: recorder.recording) { if !recorder.recording { typing = true } }
        .onChange(of: transcribing) { if !transcribing { typing = true } }
        .onExitCommand { if recorder.recording { recorder.cancel() } else if !find.isEmpty { find = "" } }   // Esc: cancel recording or clear the search
        .actions {
            TextField(L("Поиск в переписке"), text: $find).frame(width: narrow ? Style.Size.searchNarrow : Style.Size.field)
            if !narrow {
                DateField(date: $jumpDate, range: DateField.earliest...Date()).help(L("Перейти к сообщениям этого дня")).onChange(of: jumpDate) { jump() }
            }
            IconButton(icon: "trash", label: L("Очистить переписку")) { confirmClear() }.help(L("Очистить переписку"))
        }
    }

    // ---- feed ----
    @ViewBuilder func feed(_ rows: [JSON], count: Int) -> some View {
        let searching = !query.isEmpty
        let older = searching ? 0 : max(0, count - window) + max(0, total - messages.count)   // hidden by the window and not yet loaded
        GeometryReader { outer in
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: Style.Space.m) {
                        if searching { cards }   // the question and confirmations don't hide behind search results
                        if count == 0 && session.sent.isEmpty && !loadedOnce {
                            if let e = loadError {
                                Empty(title: L("Не загрузилось"), text: e, icon: "exclamationmark.triangle", button: L("Повторить")) { Task { await refresh() } }.frame(height: Style.Size.tile * 3)
                            } else {
                                Loading().frame(height: Style.Size.tile * 2)
                            }
                        }
                        if older > 0 {
                            Button(String(format: L("Показать раньше · %d"), min(older, ChatView.page))) { showEarlier(proxy, first: rows.first, fetch: count <= window) }
                                .buttonStyle(.quiet).frame(maxWidth: .infinity)
                        }
                        if searching {   // search is active: a summary on top, only matches in the feed
                            Text(rows.isEmpty ? String(format: L("«%@» в переписке нет"), query)
                                               : String(format: L("найдено %@"), "\(rows.count) \(Fmt.plural(rows.count, "сообщение", "сообщения", "сообщений"))"))
                                .font(Style.Text.caption.weight(.semibold)).foregroundStyle(Style.Colors.tertiaryText).frame(maxWidth: .infinity)
                        }
                        ForEach(Array(rows.enumerated()), id: \.element["id"].str) { i, m in
                            if i == 0 || Fmt.dayGroup(rows[i - 1]["time"]) != Fmt.dayGroup(m["time"]) {
                                Text(Fmt.dayTitle(m["time"])).font(Style.Text.caption.weight(.semibold)).foregroundStyle(Style.Colors.tertiaryText)
                                    .frame(maxWidth: .infinity).padding(.top, Style.Space.m)
                            }
                            Bubble(message: m, open: { openFile(model, $0) })
                                .transition(.asymmetric(insertion: .scale(scale: 0.92, anchor: m["role"].str == "user" ? .bottomTrailing : .bottomLeading).combined(with: .opacity), removal: .opacity))
                        }
                        if !searching {
                            if let last = session.sent.last { LiveBubble(steps: narrow ? last.steps : []).transition(.scale(scale: 0.92, anchor: .bottomLeading).combined(with: .opacity)) }   // her thoughts are in the block on the left
                            cards
                        }
                        Color.clear.frame(height: Style.Size.line).id("end")
                            .background(GeometryReader { g in Color.clear.preference(key: EndGap.self, value: g.frame(in: .named("feed")).minY - outer.size.height) })
                    }
                    .padding(Style.Space.xxl)
                    .frame(maxWidth: Style.Size.chatWidth)   // a narrow centered column: Tema's and her messages side by side
                    .frame(maxWidth: .infinity)
                    .motion(value: count)
                    .motion(value: session.sent.isEmpty)
                    .motion(value: model.question?["time"].dbl)
                    .motion(value: model.pendingSends.count)
                }
                .defaultScrollAnchor(.bottom)   // the feed opens at the end and stays there while rows are loading in (in a lazy list, scrolling "to the end" by estimated heights used to fall short)
                .coordinateSpace(name: "feed")
                .onPreferenceChange(EndGap.self) { gap in let near = gap < ChatView.followGap; if near != nearEnd { nearEnd = near } }
                .onChange(of: count) {
                    let animated = !firstLoad
                    firstLoad = false
                    guard !searching, nearEnd || rows.last?["role"].str == "user" else { return }   // Tema's own message always carries down, someone else's only if already reading the end
                    if animated { withAnimation(spring) { proxy.scrollTo("end", anchor: .bottom) } } else { proxy.scrollTo("end", anchor: .bottom) }
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { if nearEnd { proxy.scrollTo("end", anchor: .bottom) } }   // the new bubble's real height isn't known yet — finish scrolling once it is
                }
                .onChange(of: session.sent.last?.steps.count) { if !searching && nearEnd { toEnd(proxy) } }
                .onChange(of: session.sent.count) { if !searching { toEnd(proxy) } }   // sent or answered: the feed always shows its very end
                .onChange(of: model.pendingSends.count) { if !searching && nearEnd { withAnimation(spring) { proxy.scrollTo("end", anchor: .bottom) } } }
                .onChange(of: model.question?["time"].dbl) { if !searching && nearEnd { withAnimation(spring) { proxy.scrollTo("end", anchor: .bottom) } } }
                .onChange(of: jumpTick) { withAnimation(spring) { proxy.scrollTo(jumpTo, anchor: .top) } }
                .onChange(of: query) { old, q in
                    // search the whole history: it's loaded once, right when the search starts (after that the filter runs over what's already loaded); done searching — back to the end
                    if q.isEmpty { proxy.scrollTo("end", anchor: .bottom) } else if old.isEmpty { Task { await refresh() } }
                }
                .onAppear {
                    proxy.scrollTo("end", anchor: .bottom)
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { proxy.scrollTo("end", anchor: .bottom) }   // row heights are known by now — finish scrolling all the way to the end
                }
            }
        }
        .overlay { if loadedOnce && count == 0 && !searching && session.sent.isEmpty && model.question == nil && model.pendingSends.isEmpty { EmptyChat().transition(.opacity) } }
        .motion(value: loadedOnce && count == 0)
    }

    /// To the very end of the feed, once now and once more when the new rows have their real heights: one scroll by the
    /// estimated heights stopped short, and the last lines stayed under the card's edge
    func toEnd(_ proxy: ScrollViewProxy) {
        withAnimation(spring) { proxy.scrollTo("end", anchor: .bottom) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { proxy.scrollTo("end", anchor: .bottom) }
    }

    /// Sena's question and sends awaiting confirmation: at the end of the feed, or at the start while searching
    @ViewBuilder var cards: some View {
        if let q = model.question { QuestionCard(question: q).id(q["time"].dbl).transition(.move(edge: .bottom).combined(with: .opacity)) }
        ForEach(model.pendingSends, id: \.["id"].str) { p in SendCard(send: p).transition(.move(edge: .bottom).combined(with: .opacity)) }
    }

    // ---- input field: a row of blocks — "+", who to write to, the field itself (with attachments above the text), mic, send ----
    var composer: some View {
        HStack(alignment: .bottom, spacing: Style.Space.m) {
            ComposerBox { IconButton(icon: recorder.recording ? "xmark" : "plus", label: recorder.recording ? L("Отменить запись") : L("Фото, видео или файл")) { recorder.recording ? recorder.cancel() : attach() } }
                .help(recorder.recording ? L("Отменить запись") : L("Фото, видео или файл"))
            ComposerBox(width: Style.Size.field * 0.75) {   // addressee: Sena or directly an agent
                HStack(spacing: Style.Space.s) {
                    AgentIcon(code: session.agent)
                    Select(selection: $session.agent, options: ChatView.direct.map { ($0, agentName($0)) }, width: Style.Size.selectShort - Style.Space.xl, framed: false, capitalize: false)
                }
            }
            .help(session.agent == "ag_sena" ? L("Пишешь Сене. Выбери агента, чтобы поручить ему напрямую") : String(format: L("Пишешь напрямую %@: %@"), session.agent, agentRole(session.agent)))
            VStack(alignment: .leading, spacing: Style.Space.m) {
                if !session.files.isEmpty || session.uploading > 0 {
                    ScrollView(.horizontal) {
                        HStack(spacing: Style.Space.m) {
                            ForEach(session.files, id: \.["path"].str) { f in
                                AttachmentChip(file: f) { withAnimation(spring) { session.files.removeAll { $0["path"].str == f["path"].str } } }
                                    .transition(.scale(scale: 0.8).combined(with: .opacity))
                            }
                            if session.uploading > 0 { UploadingChip() }
                        }
                        .padding(.vertical, Style.Space.xxs)
                    }
                }
                if recorder.recording {
                    RecordingBar(meter: recorder.meter).transition(.scale(scale: 0.96, anchor: .bottom).combined(with: .opacity))
                } else if transcribing {
                    HStack(spacing: Style.Space.m) { ArcSpinner(); Text(L("Расшифровываю…")).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText) }
                        .frame(minHeight: Style.Size.button)
                } else if let audio = session.failedAudio {   // transcription failed: the recording is intact, it can be retried
                    HStack(spacing: Style.Space.m) {
                        Text(L("Голосовое не расшифровалось")).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
                        Spacer()
                        Button(L("Повторить")) { transcribe(audio) }.buttonStyle(.quiet)
                        Button(L("Отбросить")) { session.failedAudio = nil }.buttonStyle(.quiet)
                    }
                    .frame(minHeight: Style.Size.button)
                } else {
                    TextField(placeholder, text: $text, axis: .vertical)
                        .lineLimit(1...8).textFieldStyle(.plain).font(Style.Text.body)
                        .focused($typing)
                        .frame(minHeight: Style.Size.button)
                        .help(L("Enter — отправить, ⌥Enter — новая строка"))
                        .onSubmit(send)
                }
            }
            .padding(.horizontal, Style.Space.xl).padding(.vertical, Style.Space.m)
            .frame(maxWidth: .infinity, alignment: .leading)
            .blockSurface(dark: true)
            if !recorder.recording && !transcribing {   // always in its place: hidden while Sena was off or text was typed, it looked like there was no voice at all
                ComposerBox { IconButton(icon: "mic", label: L("Записать голосовое")) { startVoice() } }
                    .help(model.alive ? L("Записать голосовое") : L("Сена выключена — включи её кнопкой в меню"))
                    .transition(.scale.combined(with: .opacity))
            }
            ComposerBox { SendButton(active: (canSend && model.alive) || recorder.recording, label: recorder.recording ? L("Отправить голосовое") : L("Отправить")) { recorder.recording ? finishVoice() : send() } }
                .help(recorder.recording ? L("Отправить голосовое") : L("Отправить (Enter)"))
                .keyboardShortcut(.return, modifiers: .command)
        }
        .frame(maxWidth: .infinity)
        .motion(Style.Motion.quick, value: canSend)
        .motion(value: recorder.recording)
        .motion(value: transcribing)
        .motion(value: session.files.count)
        .motion(value: session.uploading)
        .motion(value: model.alive)
    }

    // ---- actions ----
    func refresh(limit: Int? = nil) async { await session.refresh(model, limit: limit) }
    /// Older messages: first the ones hidden by the window, and once those run out — fetch more from the server
    func showEarlier(_ proxy: ScrollViewProxy, first: JSON?, fetch: Bool) {
        let anchor = first?["id"].str ?? ""
        window += ChatView.page
        DispatchQueue.main.async { proxy.scrollTo(anchor, anchor: .top) }   // the feed stays put, the older messages appear on top
        if fetch { Task { await refresh(); proxy.scrollTo(anchor, anchor: .top) } }
    }
    /// To the first message of the chosen day (or the closest one after it); if it's older than what's loaded — fetch the full history first
    func jump() {
        let day = Calendar.current.startOfDay(for: jumpDate)
        find = ""
        Task {
            if let first = messages.first.flatMap({ Fmt.date($0["time"]) }), first > day, total > messages.count { await refresh(limit: ChatView.deepest) }
            let rows = messages + pending
            guard let i = rows.firstIndex(where: { (Fmt.date($0["time"]) ?? .distantPast) >= day }) else { model.say(L("Сообщений с этого дня нет")); return }
            window = max(window, rows.count - i + ChatView.page)   // the target day could be above the shown window; after this, polling requests exactly that much
            jumpTo = rows[i]["id"].str; jumpTick += 1
        }
    }
    /// A search result points at a message: the feed scrolls to it (an old one is fetched with the whole history first)
    func pickMessage() {
        guard loadedOnce, let id = model.take(), !id.isEmpty else { return }
        find = ""
        Task {
            if !messages.contains(where: { $0["id"].str == id }) && total > messages.count { await refresh(limit: ChatView.deepest) }
            let rows = messages + pending
            guard let i = rows.firstIndex(where: { $0["id"].str == id }) else { model.say(L("Этого сообщения уже нет в переписке")); return }
            window = max(window, rows.count - i + ChatView.page)
            jumpTo = id; jumpTick += 1
        }
    }
    func takeRestored() {
        guard let r = session.restored else { return }
        session.restored = nil
        text = text.isEmpty ? r : r + "\n" + text   // words that weren't sent go back into the field
    }
    func attach() {
        let panel = NSOpenPanel(); panel.allowsMultipleSelection = true; panel.canChooseDirectories = false
        panel.message = L("Фото, видео и файлы уйдут Сене вместе с сообщением")
        if panel.runModal() == .OK { session.upload(model, panel.urls) }
    }
    /// ⌘V with an image or Finder files on the clipboard becomes an attachment; plain text still goes into the field as usual.
    /// The key is matched by code (V in any layout), only a plain ⌘ and only in the main window: the mini panel and ⌘⇧V stay untouched
    func watchPaste() {
        guard pasteMonitor == nil else { return }
        pasteMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { e in
            guard e.keyCode == 9, e.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command, model.section == .chat, model.tab(.chat) == 0,
                  let w = e.window, w.canBecomeMain, !(w is NSPanel) else { return e }
            let pb = NSPasteboard.general
            if let urls = pb.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL], !urls.isEmpty {
                session.upload(model, urls)
                return nil
            }
            if pb.string(forType: .string) == nil {
                if let png = pb.data(forType: .png) { session.uploadImage(model, png, isPNG: true); return nil }
                if let tiff = pb.data(forType: .tiff) { session.uploadImage(model, tiff, isPNG: false); return nil }
            }
            return e
        }
    }
    func send() { send(text, voice: false) }
    func send(_ raw: String, voice: Bool) {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if session.submit(model, t, voice: voice) { if !voice { text = "" } }   // sent but didn't arrive — the words come back (restored)
        else if voice { text = raw }   // didn't go out: the transcription (which was paid for) ends up in the field instead of disappearing
    }
    func startVoice() {
        guard model.alive else { model.say(L("Сена выключена — включи её кнопкой в меню")); return }
        session.failedAudio = nil
        Task { if let why = await recorder.start() { model.say(why) } }
    }
    func finishVoice() {
        guard recorder.recording else { return }   // a second tap while the recording has already stopped does nothing
        let seconds = recorder.seconds
        guard let audio = recorder.stop() else { model.say(L("Не удалось сохранить запись")); return }
        guard seconds >= 0.5 else { model.say(L("Слишком коротко")); return }
        transcribe(audio)
    }
    func transcribe(_ audio: Data) {
        transcribing = true
        session.failedAudio = nil
        Task {
            defer { transcribing = false }
            do {
                let spoken = try await model.backend.transcribe(audio)
                if spoken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    session.failedAudio = audio   // nothing made out: the recording stays, with "Retry", instead of vanishing with a toast
                    model.say(L("Не разобрала слов"))
                } else { send(spoken, voice: true) }
            } catch {
                session.failedAudio = audio   // the recording isn't lost: it can be retried
                model.say(String(format: L("Расшифровка не удалась: %@"), error.localizedDescription), hold: 7)
            }
        }
    }
    func confirmClear() {
        guard confirm(L("Очистить переписку?"), L("Переписка общая с Телеграмом: Сена забудет её и там. Вернуть нельзя; сводка старой части тоже удалится."), button: L("Очистить"), destructive: true) else { return }
        model.run(L("Переписка"), { try await self.model.backend.act("history_clear") }, then: { session.forget(); Task { await refresh() } })
    }
}

private extension View {
    /// An attachment/file chip: an accent pastel (Style.Colors.theirs) with the shared corner radius
    func plate() -> some View { background(Style.Colors.theirs, in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous)) }
}

/// How far the end of the feed sits below the bottom edge (in points): close to zero or less — the feed is scrolled to the end. No end on screen — a huge number
private struct EndGap: PreferenceKey {
    static var defaultValue: CGFloat = .greatestFiniteMagnitude
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = min(value, nextValue()) }
}

// ---- chat session: what survives the screen being rebuilt ----

/// Attachments, the addressee, sent-but-unanswered messages, and waiting for a reply. Polling for the task happens here, not in the screen: leave for
/// another section and come back — Tema's message and "Sena is typing" are still there, the answer isn't lost
@MainActor final class ChatSession: ObservableObject {
    static let shared = ChatSession()
    static let waitLimit: TimeInterval = 15 * 60   // we don't wait longer than this for a reply: it'll arrive in the conversation anyway

    /// A sent message: until Sena answers, it's shown from this list, not from the history (the server hides a message with no answer)
    struct Sent: Identifiable {
        let id = UUID().uuidString
        let message: JSON
        let voice: Bool                         // dictated: her answer is said aloud too (when "Отвечать голосом" is on)
        let sent = Date()
        var steps: [String] = []
    }
    @Published var files: [JSON] = []
    @Published var agent = "ag_sena"            // who it's addressed to: Sena or directly an agent (ag_web, ag_mac)
    @Published var restored: String?            // words that weren't sent: the screen puts them back in the field
    @Published var failedAudio: Data?           // a voice message that failed to transcribe: it can be retried
    @Published private(set) var sent: [Sent] = []
    @Published private(set) var uploading = 0   // how many attachments are still uploading
    @Published private(set) var answering = false
    @Published private(set) var finished = 0    // how many tasks have finished
    @Published private(set) var thoughts: [String] = []   // the steps of her latest task: shown while she works and after, until the next one
    private var pasted = 0

    // ---- the conversation: one copy for the chat and the shelf ----
    // Both used to poll on their own every 3 s: the shelf showed a different moment than the chat, never the message just sent,
    // and lost replies when Sena answered in several steps. Now one poll feeds both, faster while an answer is awaited.
    @Published private(set) var messages: [JSON] = []
    @Published private(set) var total = 0           // how many messages the server holds; messages has only the latest of them
    @Published private(set) var loaded = false      // the history arrived at least once
    @Published private(set) var loadError: String?  // the history never arrived: why
    var reach = ChatView.batch                      // how many of the latest messages the chat wants (more while searching or scrolled back)
    private var seen = ""                           // "count|id of the last one": the same — nothing is republished
    private var sig = ("", 0)                       // the server's signature of the last reply and for how many: the next poll names it and gets "same"
    private var ticket = 0                          // a late reply to an older request is dropped
    private var watchers = 0
    private var poller: Task<Void, Never>?
    static let calm: UInt64 = 3_000_000_000         // ns between polls when nothing is awaited
    static let eager: UInt64 = 1_000_000_000        // and while an answer or a live conversation is on

    /// The chat or the shelf's chat came on screen: the shared poll runs while at least one of them is shown
    func watch(_ model: AppModel) {
        watchers += 1
        guard poller == nil else { return }
        poller = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refresh(model)
                let busy = !self.sent.isEmpty || Ears.shared.talking || Ears.shared.thinking
                try? await Task.sleep(nanoseconds: busy ? ChatSession.eager : ChatSession.calm)
            }
        }
    }
    func unwatch() {
        watchers = max(0, watchers - 1)
        if watchers == 0 { poller?.cancel(); poller = nil }
    }

    /// The latest history from the server; "changed" is decided by the count and the last id (the count alone stops growing at the ceiling)
    func refresh(_ model: AppModel, limit: Int? = nil) async {
        ticket += 1
        let mine = ticket
        let n = limit ?? reach
        do {
            let r = try await model.backend.get("history", ["limit": "\(n)", "same": sig.1 == n ? sig.0 : ""])
            guard mine == ticket else { return }
            loaded = true; loadError = nil
            if r["same"].bool { return }
            let rows = r["messages"].array
            total = r["total"].int
            sig = (r["sig"].str, n)
            let s = "\(rows.count)|\(rows.last?["id"].str ?? "")"
            if s != seen { seen = s; messages = rows }
        } catch {
            if !loaded { loadError = error.localizedDescription }
        }
    }
    /// The conversation was cleared: the next poll takes it whole
    func forget() { seen = ""; sig = ("", 0) }

    /// Sent messages the history doesn't have yet (the server writes a message down a moment after taking it): shown from
    /// here until then, and not twice once it is there
    var pending: [JSON] {
        guard let first = sent.first else { return [] }
        let from = first.sent.timeIntervalSince1970.rounded(.down)   // the server's time has one-second precision
        var stored = 0
        for m in messages.reversed() {
            guard let d = Fmt.date(m["time"]), d.timeIntervalSince1970 >= from else { break }
            if m["role"].str == "user" { stored += 1 }
        }
        return sent.dropFirst(min(stored, sent.count)).map(\.message)
    }

    func add(_ f: JSON) {
        if !files.contains(where: { $0["path"].str == f["path"].str }) { files.append(f) }
    }

    // ---- attachments ----
    /// Files upload one after another, in the order they were picked; sending is disabled while they're uploading
    func upload(_ model: AppModel, _ urls: [URL]) {
        uploading += urls.count
        Task {
            for u in urls {
                defer { uploading -= 1 }
                do { add(try await model.backend.upload(u)) }
                catch { model.say(String(format: L("Не загрузился «%@»: %@"), u.lastPathComponent, error.localizedDescription), hold: 7) }
            }
        }
    }
    /// An image from the clipboard (a screenshot): each one gets its own name, TIFF is converted to PNG off the main thread
    func uploadImage(_ model: AppModel, _ data: Data, isPNG: Bool) {
        pasted += 1
        let name = "снимок \(pasted).png"
        uploading += 1
        Task {
            defer { uploading -= 1 }
            let png: Data? = isPNG ? data : await Task.detached { ChatSession.png(fromTIFF: data) }.value
            do { add(try await model.backend.upload(png ?? data, name: name)) }
            catch { model.say(String(format: L("Не загрузился «%@»: %@"), name, error.localizedDescription), hold: 7) }
        }
    }
    nonisolated static func png(fromTIFF d: Data) -> Data? { NSBitmapImageRep(data: d)?.representation(using: .png, properties: [:]) }

    // ---- sending ----
    /// Send words and attachments to Sena (or an agent). false — didn't go out, leave the field as is. If Sena herself is waiting
    /// for an answer to a question, the words become a reply to it. If the server rejects it, words and attachments come back (restored, files) instead of vanishing
    func submit(_ model: AppModel, _ t: String, voice: Bool = false) -> Bool {
        guard !t.isEmpty || !files.isEmpty else { return false }
        guard uploading == 0 else { model.say(L("Вложение ещё загружается…")); return false }
        guard model.alive else { model.say(L("Сена выключена — включи её кнопкой в меню")); return false }
        if let q = model.question, files.isEmpty {   // the question on screen: his words are its answer
            Task { if await answer(model, t, to: q) == false { restored = t } }
            return true
        }
        let sending = files, to = agent
        files = []
        let shown = (sending.map { $0["note"].str.isEmpty ? "[\($0["name"].str)]" : $0["note"].str } + [t]).filter { !$0.isEmpty }.joined(separator: "\n")
        thoughts = []
        let item = Sent(message: JSON(["id": "local-\(UUID().uuidString)", "role": "user", "content": shown, "time": ISO8601DateFormatter().string(from: Date())]), voice: voice)
        sent.append(item)
        Task {
            do {
                let r = try await model.backend.api("/ask", body: ["text": t, "files": sending.map { $0["path"].str }, "agent": to])
                await follow(model, item.id, r["id"].str)
            } catch {
                sent.removeAll { $0.id == item.id }
                files = sending + files
                if !t.isEmpty { restored = t }
                model.say(String(format: L("Не получилось: %@"), error.localizedDescription), hold: 7)
            }
        }
        return true
    }
    /// An answer to Sena's question (from the chat field, a card, or the mini panel). true — accepted; false — don't clear the answer field.
    /// The question is named: when two agents ask, the one on screen may be answered elsewhere meanwhile, and the words must not
    /// go to the other one (without an id the server answers the oldest)
    func answer(_ model: AppModel, _ t: String, to question: JSON) async -> Bool {
        guard !answering else { return false }
        answering = true
        defer { answering = false }
        var body: [String: Any] = ["text": t]
        if let id = question["id"].string, !id.isEmpty { body["id"] = id }
        do {
            let r = try await model.backend.act("answer", body)
            model.say(r["result"].string ?? String(format: L("%@: готово"), L("Ответ")))
            await model.poll()   // the question card disappears right away, not a couple of seconds later
            return true
        } catch {
            model.say(String(format: L("%@: %@"), L("Ответ"), error.localizedDescription), hold: 7)
            return false
        }
    }
    /// Poll the task to completion, no longer than waitLimit; a server "error" reply or several connection drops in a row — give up (the server may have lost track of the task)
    private func follow(_ model: AppModel, _ id: String, _ task: String) async {
        defer { Task { await refresh(model); sent.removeAll { $0.id == id }; finished += 1 } }   // the answer is in the feed before the local copy goes: no blink
        let until = Date().addingTimeInterval(ChatSession.waitLimit)
        var misses = 0
        while Date() < until {
            do { try await Task.sleep(nanoseconds: 700_000_000) } catch { return }
            do {
                let s = try await model.backend.api("/task?id=\(Backend.enc(task))")
                misses = 0
                if s["done"].bool {
                    if s["failed"].bool { model.say(s["answer"].str, hold: 9) }   // it failed in a way that kept the answer out of the conversation: otherwise silence
                    else if sent.first(where: { $0.id == id })?.voice == true && Ears.shared.voiceReply { Speaker.shared.say(model, s["answer"].str) }   // asked by voice — answered by voice
                    return
                }
                if let i = sent.firstIndex(where: { $0.id == id }) { sent[i].steps = s["steps"].array.map { $0.str }; thoughts = sent[i].steps }
            } catch let e as APIError {
                model.say(String(format: L("Не получилось: %@"), e.message), hold: 7)
                return
            } catch {
                misses += 1
                if misses >= 5 { model.say(String(format: L("Не получилось: %@"), error.localizedDescription), hold: 7); return }
            }
        }
        model.say(L("Сена отвечает дольше 15 минут: ответ придёт в переписку, когда будет готов"), hold: 7)
    }
}

/// Files dragged in from Finder: their addresses, each however the system packs it (data, a URL object, text), then `take`
/// on the main thread. The same for the chat window and the notch shelf
func dropFiles(_ providers: [NSItemProvider], take: @escaping @MainActor ([URL]) -> Void) -> Bool {
    let files = providers.filter { $0.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) }
    for p in files {
        p.loadItem(forTypeIdentifier: UTType.fileURL.identifier) { item, _ in
            let url: URL? = switch item {
            case let d as Data: URL(dataRepresentation: d, relativeTo: nil)
            case let u as URL: u
            case let s as String: URL(string: s)
            default: nil
            }
            guard let url, url.isFileURL else { return }
            let file = (url as NSURL).filePathURL ?? url   // a file reference (file:///.file/id=…) becomes the real path: the name shows right
            Task { @MainActor in take([file]) }
        }
    }
    return !files.isEmpty
}

// ---- feed details ----

/// A file note inside a message: "[Фото «cat.jpg», 120 KB, сохранён: /path]" — we show it as an image or a chip, not as text
struct FileNote: Identifiable {
    let label: String, name: String, size: String, path: String
    var id: String { path }   // stable: with a new id on every redraw, the photo in the bubble used to flicker and bounce again
    var isImage: Bool { label == "Фото" }
    var icon: String {
        var kind = "other"
        switch label { case "Фото": kind = "image"; case "PDF": kind = "pdf"; case "Таблица": kind = "sheet"; case "Документ": kind = "doc"; case "Текстовый файл": kind = "text"; default: break }
        return fileIcon(JSON(["path": path, "kind": kind]))
    }
    static let pattern = try! NSRegularExpression(pattern: "\\[(Фото|PDF|Таблица|Документ|Текстовый файл|Файл) «([^»]+)», ([^,\\]]+), сохранён: ([^\\]]+)\\]")
    static func split(_ s: String) -> (notes: [FileNote], text: String) {
        let ns = s as NSString
        let found = pattern.matches(in: s, range: NSRange(location: 0, length: ns.length))
        let notes = found.map { FileNote(label: ns.substring(with: $0.range(at: 1)), name: ns.substring(with: $0.range(at: 2)), size: ns.substring(with: $0.range(at: 3)), path: ns.substring(with: $0.range(at: 4))) }
        var text = s
        for m in found.reversed() { text = (text as NSString).replacingCharacters(in: m.range, with: "") }
        return (notes, text.trimmingCharacters(in: .whitespacesAndNewlines))
    }
}

/// The bubble doesn't subscribe to the model: it redraws only when the message itself changes
struct Bubble: View {
    @Environment(\.colorScheme) private var scheme
    let message: JSON
    let open: (String) -> Void   // open the file from the note
    var mine: Bool { message["role"].str == "user" }
    var body: some View {
        let parts = FileNote.split(message["content"].str)
        HStack(alignment: .bottom) {
            if mine { Spacer(minLength: Style.Size.bubbleGap) }
            VStack(alignment: .leading, spacing: Style.Space.s) {
                ForEach(parts.notes) { n in
                    if n.isImage {
                        Thumb(path: n.path).onTapGesture { open(n.path) }.help(L("Открыть")).tapAsButton { open(n.path) }
                    } else {
                        HStack(spacing: Style.Space.m) {
                            Image(systemName: n.icon).font(Style.Icon.chip).foregroundStyle(Style.Colors.secondaryText)
                            VStack(alignment: .leading, spacing: 0) { Text(n.name).font(Style.Text.small.weight(.medium)).lineLimit(1); Text(n.size).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText) }
                        }
                        .padding(Style.Space.m).plate()
                        .onTapGesture { open(n.path) }.help(L("Открыть")).tapAsButton { open(n.path) }
                    }
                }
                if !parts.text.isEmpty {
                    if mine { Text(verbatim: parts.text).font(Style.Text.body).textSelection(.enabled) }   // Tema's own words as typed: asterisks and underscores aren't markdown
                    else { MarkdownView(text: parts.text) }   // her answer keeps its headings and lists
                }
                Text(Fmt.time(message["time"]))   // no "dictated" mark: the stored conversation doesn't keep it, it vanished with the reply
                    .font(Style.Text.tiny).foregroundStyle(Style.Colors.tertiaryText)
            }
            .padding(.horizontal, mine ? Style.Space.xl : 0).padding(.vertical, mine ? Style.Space.m : Style.Space.xs)
            .background(mine ? Style.Colors.leadBlock(scheme) : Color.clear, in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))   // Tema's own are contrasting, Sena's replies are just text on the background
            .environment(\.colorScheme, mine ? Style.Colors.opposite(scheme) : scheme)
            .frame(maxWidth: Style.Size.bubbleMax, alignment: mine ? .trailing : .leading)   // the bubble sizes to its content, long text wraps
            if !mine { Spacer(minLength: Style.Size.bubbleGap) }
        }
    }
}

/// A photo in a bubble: a thumbnail from the file on disk (the cache is shared with "Files"), no bigger than the frame. While it's being
/// computed — a placeholder of the same size, so the feed doesn't jump; if the file is gone (removed during cleanup) — that's stated, not an endless shimmer
struct Thumb: View {
    let path: String
    @State private var image: NSImage? = nil
    @State private var missing = false
    static let box = CGSize(width: Style.Size.thumbWide, height: Style.Size.thumb)
    var body: some View {
        Group {
            if let img = image {
                Image(nsImage: img).resizable().frame(width: img.size.width, height: img.size.height)
            } else if missing {
                VStack(spacing: Style.Space.xs) {
                    Glyph("photo").font(Style.Icon.chip)
                    Text(L("Файл недоступен")).font(Style.Text.caption)
                }
                .foregroundStyle(Style.Colors.secondaryText)
                .frame(width: Thumb.box.width, height: Thumb.box.height)
                .background(Style.Colors.theirs, in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
            } else {
                RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).fill(Style.Colors.ink.opacity(Style.Motion.skeletonFill))
                    .frame(width: Thumb.box.width, height: Thumb.box.height).shimmer()
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
        .motion(Style.Motion.quick, value: image == nil)
        .task(id: path) {
            missing = false
            image = await Thumbs.make(path, fit: Thumb.box)
            if image == nil && !Task.isCancelled { missing = true }
        }
    }
}

/// Sena is working: three breathing dots and the task's latest steps
struct LiveBubble: View {
    let steps: [String]
    var body: some View {
        let tail = steps.suffix(4)
        let base = steps.count - tail.count
        let numbered = tail.enumerated().map { (base + $0.offset, $0.element) }   // the step's number within the whole task: identical words in a row are still different rows
        HStack(alignment: .bottom) {
            VStack(alignment: .leading, spacing: Style.Space.xs) {
                TypingDots()
                ForEach(numbered, id: \.0) { n in Text(n.1).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(2).transition(.opacity.combined(with: .move(edge: .bottom))) }
            }
            .padding(.vertical, Style.Space.xs)
            .motion(Style.Motion.quick, value: steps)
            Spacer(minLength: Style.Size.bubbleGap)
        }
    }
}

/// Her thoughts — the steps of the task she is on («Открываю файл», «Смотрю почту»…) — in a dark block on the left of the
/// conversation, not mixed into it. The latest at the bottom; stays after the answer until the next task
struct Thoughts: View {
    let steps: [String]
    let working: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.m) {
            HStack(spacing: Style.Space.s) {
                Text(L("Мысли Сены")).font(Style.Text.heading)
                Spacer(minLength: 0)
                if working { TypingDots() }
            }
            if steps.isEmpty {
                Text(working ? L("Начинаю…") : L("Здесь будет видно, что Сена делает")).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
            } else {
                ViewThatFits(in: .vertical) {   // as tall as its lines; scrolls only once they no longer fit beside the conversation
                    lines
                    ScrollViewReader { proxy in
                        ScrollView { lines }
                            .onChange(of: steps.count) { withAnimation(Style.Motion.spring) { proxy.scrollTo("last", anchor: .bottom) } }
                            .onAppear { proxy.scrollTo("last", anchor: .bottom) }
                    }
                }
            }
        }
        .padding(Style.Space.inset)
        .frame(width: Style.Size.column * 0.8, alignment: .topLeading)
        .blockSurface(dark: true)
        .motion(Style.Motion.quick, value: steps)
    }
    var lines: some View {
        VStack(alignment: .leading, spacing: Style.Space.gapSmall) {
            ForEach(Array(steps.enumerated()), id: \.offset) { i, step in
                Text(step).font(Style.Text.small)
                    .foregroundStyle(i == steps.count - 1 && working ? Style.Colors.ink : Style.Colors.secondaryText)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
            }
            Color.clear.frame(height: 0).id("last")
        }
    }
}

struct TypingDots: View {
    @Environment(\.accessibilityReduceMotion) private var still
    @State private var up = false
    var body: some View {
        HStack(spacing: Style.Space.xs) {
            ForEach(0..<3, id: \.self) { i in
                Circle().fill(Style.Colors.secondaryText).frame(width: Style.Size.lineDot, height: Style.Size.lineDot)
                    .offset(y: up ? -Style.Space.xs : 0)
                    .motion(Style.Motion.typing.delay(Double(i) * Style.Motion.typingStep), value: up)
            }
        }
        .frame(height: Style.Space.xl)
        .onAppear { if !still { up = true } }   // "reduce motion": the dots stay still
    }
}

/// Empty conversation: a centered leaf, what can be done, and a tip about files and voice
struct EmptyChat: View {
    var body: some View {
        VStack(spacing: Style.Space.m) {
            LeafMark(size: Style.Icon.leafTile / 2, mode: .assemble)
            Text(L("Напиши Сене")).font(Style.Text.title)
            Text(L("Можно прикрепить файл или надиктовать голосовое")).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText).multilineTextAlignment(.center)
        }
        .padding(Style.Space.xxl)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .allowsHitTesting(false)
    }
}

/// Sena's question: text, an image (a captcha, a screenshot) and an answer field. Shared by the chat card and the mini panel widget.
/// Callers set .id(question time): a new question means a clean field and a fresh image
struct QuestionBody: View {
    @EnvironmentObject var model: AppModel
    @ObservedObject private var session = ChatSession.shared
    let question: JSON
    var lines = 0   // how many lines of the question to show; 0 — all of it
    @State private var answer = ""
    @State private var image: NSImage? = nil
    var empty: Bool { answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.m) {
            Text(question["question"].str).font(Style.Text.body).lineLimit(lines > 0 ? lines : nil).textSelection(.enabled)
                .help(lines > 0 ? question["question"].str : "")   // in a cramped panel the question is truncated — the full text is in the tooltip
            if let img = image { Image(nsImage: img).resizable().scaledToFit().frame(maxHeight: Style.Size.thumb).clipShape(RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous)) }
            HStack(spacing: Style.Space.gapSmall) {
                TextField(L("Ответ Сене"), text: $answer).onSubmit(reply)
                Button(L("Ответить"), action: reply).buttonStyle(.primary).disabled(empty || session.answering)
            }
            TimelineView(.periodic(from: .now, by: 1)) { ctx in   // runs on its own: an idle overview isn't republished
                Text(String(format: L("ждёт %@"), Fmt.dur(ctx.date.timeIntervalSince1970 - question["time"].dbl))).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
            }
            if question["count"].int > 1 {   // several agents asked: this is the oldest question, the next ones come after it
                Text(String(format: L("ещё вопросов ждут: %d"), question["count"].int - 1)).font(Style.Text.caption).foregroundStyle(Style.Colors.warnText)
            }
        }
        .task(id: question["time"].dbl) { if question["has_image"].bool, let d = try? await model.backend.bytes("/desk/api/question_image") { image = NSImage(data: d) } }
    }
    func reply() {
        let t = answer.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        Task { if await session.answer(model, t, to: question), answer.trimmingCharacters(in: .whitespacesAndNewlines) == t { answer = "" } }   // not accepted — the words stay in the field
    }
}

/// Sena's question right in the feed
struct QuestionCard: View {
    @Environment(\.accessibilityReduceMotion) private var still
    let question: JSON
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.m) {
            Label(L("Сена спрашивает"), systemImage: "questionmark.bubble").symbolEffect(.pulse, options: .repeating, isActive: !still).font(Style.Text.heading).foregroundStyle(Style.Colors.warnText)
            QuestionBody(question: question)
        }
        .padding(Style.Space.xl)
        .frame(maxWidth: .infinity, alignment: .leading)
        .blockSurface(radius: Style.Radius.all, dark: true)
        .overlay(RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).strokeBorder(Style.Colors.warn, lineWidth: Style.Size.line))
    }
}

/// Something Sena prepared waits for Tema's "yes" — a message or a mail to someone, a site or GitHub edit, an errand for an agent,
/// a secret to show (confirm.waiting on the server): what it is, its full text (confirming means reading it in full) and «Да» / «Отмена».
/// Shared by the chat card and the mini panel widget
struct SendBody: View {
    @EnvironmentObject var model: AppModel
    let send: JSON
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.m) {
            if !send["text"].str.isEmpty {
                Text(send["text"].str).font(Style.Text.body).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack(spacing: Style.Space.gapSmall) {
                Button(L("Да")) { model.confirmSend(send["id"].str) }.buttonStyle(.primary)
                Button(L("Отмена")) { model.cancelSend(send["id"].str) }
            }
        }
    }
}

/// The card's title: what waits for the "yes", in the server's words ("письмо …", "правка сайта", "поручение ag_web")
func sendTitle(_ send: JSON) -> String { String(format: L("Ждёт твоего «да»: %@"), send["name"].str) }

struct SendCard: View {
    @Environment(\.accessibilityReduceMotion) private var still
    let send: JSON
    @State private var appeared = false   // the mark bounces when the card appears
    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.m) {
            Label(sendTitle(send), systemImage: "checkmark.seal").symbolEffect(.bounce, options: .speed(0.7), value: appeared && !still).font(Style.Text.heading)
            SendBody(send: send)
        }
        .padding(Style.Space.xl)
        .frame(maxWidth: .infinity, alignment: .leading)
        .blockSurface(radius: Style.Radius.all, dark: true)
        .overlay(RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).strokeBorder(Style.Colors.info, lineWidth: Style.Size.line))
        .onAppear { appeared = true }
    }
}

// ---- input field details ----

/// The send arrow: an accent-colored circle, pale while there's nothing to send
struct SendButton: View {
    @State private var hover = false
    @State private var taps = 0   // tapping: the arrow flies up and returns from below
    let active: Bool
    let label: String
    let act: () -> Void
    var body: some View {
        Button { taps += 1; act() } label: {
            Glyph("arrow.up", trigger: (active ? 1 : 0) + taps * 2).font(Style.Icon.send).foregroundStyle(.white)
                .frame(width: Style.Size.button, height: Style.Size.button)
                .background(Style.Colors.accent.opacity(active ? 1 : 0.35), in: Circle())
                .shadow(color: Style.Colors.accent.opacity(active && hover ? Style.Motion.glow : 0), radius: Style.Size.shadowRadius, y: Style.Size.shadowY)
                .scaleEffect(active ? (hover ? Style.Motion.buttonHover : 1) : Style.Motion.sendIdle)
                .contentShape(Circle())
        }
        .buttonStyle(PressStyle())
        .disabled(!active)
        .accessibilityLabel(label)
        .environment(\.hovering, hover && active)
        .onHover { hover = $0 }
        .motion(value: active)
        .motion(value: hover)
    }
}

/// An attachment above the input field: a photo thumbnail or file icon (the shared FileIcon: reads disk off body and keeps a cache), name, x button
struct AttachmentChip: View {
    let file: JSON
    let remove: () -> Void
    @State private var hover = false
    var body: some View {
        HStack(spacing: Style.Space.m) {
            FileIcon(file: file, side: Style.Size.chip)
            Text(file["name"].str).font(Style.Text.caption).lineLimit(1).frame(maxWidth: Style.Size.field / 2, alignment: .leading)
            Button(action: remove) { Glyph("xmark.circle.fill").foregroundStyle(Style.Colors.secondaryText).frame(width: Style.Size.counter, height: Style.Size.counter).contentShape(Circle()) }
                .buttonStyle(.plain).environment(\.hovering, hover).opacity(hover ? 1 : 0.5)
                .accessibilityLabel(L("Убрать вложение"))
        }
        .padding(.horizontal, Style.Space.m).padding(.vertical, Style.Space.xs).plate()
        .onHover { hover = $0 }
        .motion(Style.Motion.quick, value: hover)
    }
}

/// An attachment is still uploading: sending is disabled until it appears
struct UploadingChip: View {
    var body: some View {
        HStack(spacing: Style.Space.m) {
            ArcSpinner()
            Text(L("Загружаю…")).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
        }
        .frame(height: Style.Size.chip)
        .padding(.horizontal, Style.Space.m).plate()
    }
}

/// Recording in progress: a blinking dot, the time, and a live wave from the voice
struct RecordingBar: View {
    @Environment(\.accessibilityReduceMotion) private var still
    @ObservedObject var meter: Recorder.Meter
    @State private var blink = false
    var body: some View {
        HStack(spacing: Style.Space.m) {
            Circle().fill(Style.Colors.recording).frame(width: Style.Size.dot, height: Style.Size.dot)
                .opacity(blink ? 0.3 : 1).motion(Style.Motion.blink, value: blink).onAppear { if !still { blink = true } }
            Text(String(format: "%d:%02d", Int(meter.seconds) / 60, Int(meter.seconds) % 60)).font(Style.Text.small.monospacedDigit()).frame(width: Style.Size.chip, alignment: .leading)
            HStack(spacing: Style.Space.xxs) {
                ForEach(Array(meter.levels.enumerated()), id: \.offset) { _, l in
                    RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).fill(Style.Colors.recording).frame(width: Style.Size.waveBar, height: max(Style.Size.waveBar, l * Style.Size.waveHeight))
                }
            }
            .frame(height: Style.Size.waveHeight)
            .motion(Style.Motion.quick, value: meter.levels)
            Spacer()
            Text(L("говори…")).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
        }
        .padding(.horizontal, Style.Space.xl)
        .frame(minHeight: Style.Size.button)
        .background(Style.Colors.recording.opacity(0.08), in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
    }
}

/// Over chat while files are being dragged
struct DropHint: View {
    @Environment(\.accessibilityReduceMotion) private var still
    var body: some View {
        VStack(spacing: Style.Space.m) {
            Image(systemName: "tray.and.arrow.down").symbolEffect(.pulse, options: .repeating.speed(0.7), isActive: !still).font(Style.Icon.empty).foregroundStyle(Style.Colors.accent)
            Text(L("Отпусти, чтобы прикрепить")).font(Style.Text.title)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).strokeBorder(Style.Colors.accent, style: StrokeStyle(lineWidth: Style.Size.line * 2, dash: [Style.Space.m])).padding(Style.Space.m))
        .allowsHitTesting(false)
    }
}

/// A small block in the input row: a square with one button ("+", addressee, mic, send)
struct ComposerBox<Content: View>: View {
    var width: CGFloat? = nil
    @ViewBuilder let content: () -> Content
    var body: some View {
        content()
            .frame(width: width ?? Style.Size.button, height: Style.Size.button)
            .padding(Style.Space.m)
            .blockSurface(dark: true)
    }
}

