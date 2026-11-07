// Files (a chat tab): what Tema sent, what was downloaded, and what Sena produced. A folder filter and name search are in the section header.
// A list of rows grouped by day on the left (a photo thumbnail or icon, name, kind and size, time); a file card on the right: details,
// actions (send to Sena in chat, open, show in Finder, copy path), and content — a photo as an image,
// a PDF as its first page, text as text; everything else — an icon and the kind's name. The list refreshes itself every 10 seconds.
import ImageIO
import SwiftUI

struct FilesView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var still
    @Environment(\.narrowWindow) private var narrow
    @State private var rows: [JSON] = []
    @State private var lastRaw = JSON(nil)   // what came in last time: if it's the same, redraw nothing
    @State private var loaded = false
    @State private var loadError: String? = nil   // the list didn't arrive: instead of an endless placeholder — a reason and "Retry"
    @State private var selected: String? = nil
    @State private var folder = ""
    @State private var search = ""

    static let folders = ["присланное", "скачанное", "сделанное"]   // server protocol: FILE_DIRS in desk.py; the display translation is FOLDER_RU
    static let serverLimit = 300   // this many files the server returns (files_view in desk.py): older ones aren't in the list
    var spring: Animation? { still ? nil : Style.Motion.spring }   // for withAnimation; modifiers use .motion
    var query: String { search.trimmingCharacters(in: .whitespaces).lowercased() }
    var shown: [JSON] {
        let q = query
        return rows.filter { (folder.isEmpty || $0["folder"].str == folder) && (q.isEmpty || $0["name"].str.lowercased().contains(q) || $0["file"].str.lowercased().contains(q)) }
    }
    func summary(_ list: [JSON]) -> String {
        let n = list.count
        let more = rows.count >= FilesView.serverLimit && folder.isEmpty && query.isEmpty ? "+" : ""   // the server returned as many as it can: there may be more files
        return "\(n)\(more) \(Fmt.plural(n, "файл", "файла", "файлов")) · \(Fmt.bytes(list.reduce(0) { $0 + $1["size"].dbl }))"   // Fmt.plural translates the word itself
    }

    var body: some View {
        let list = shown   // computed once per redraw: below it is read by the list, the card and the selection
        Columns {
            listBlock(list)
        } right: {
            detail(list)
        }
        .motion(value: selected)
        .actions {
            Segments(selection: $folder, options: [("", L("Все"))] + FilesView.folders.map { ($0, FOLDER_RU[$0] ?? $0) }, collapses: true)
            TextField(L("Поиск по имени"), text: $search).frame(width: narrow ? Style.Size.searchNarrow : Style.Size.field)
        }
        .task { while !Task.isCancelled { await refresh(); try? await Task.sleep(nanoseconds: 10_000_000_000) } }
        .onChange(of: model.focus) { pick() }
        .onChange(of: list.map { $0["path"].str }) { _, paths in   // the selected file disappeared from the list (filter, search, deleted) — select the first one
            if let s = selected, paths.contains(s) { return }
            selected = paths.first
        }
    }

    /// A search result points at a file: the memory index names it by its path under data/ ("inbox/…"), the list by the full one
    private func pick() {
        guard loaded, let f = model.focus else { return }
        _ = model.take()
        guard let row = rows.first(where: { $0["path"].str == f || $0["path"].str.hasSuffix("/" + f) }) else { model.say(L("Этого файла уже нет в папках Сены")); return }
        folder = ""; search = ""
        selected = row["path"].str
    }

    @ViewBuilder func detail(_ list: [JSON]) -> some View {
        if let f = list.first(where: { $0["path"].str == selected }) {
            FileCard(file: f).id(f["path"].str)
                .transition(Style.Motion.appear)
        } else if loaded && rows.isEmpty {
            Empty(title: L("Файлов пока нет"), icon: "folder")
        } else {
            Color.clear   // loading or nothing found: already said on the left
        }
    }

    // ---- list: a block the full height, rows grouped by day ----
    @ViewBuilder func listBlock(_ list: [JSON]) -> some View {
        Block(folder.isEmpty ? L("Все файлы") : (FOLDER_RU[folder] ?? folder), content: {
            if !loaded {
                if let e = loadError {
                    Empty(title: L("Не загрузилось"), text: e, icon: "exclamationmark.triangle", button: L("Повторить")) { Task { await refresh() } }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    SkeletonList(rows: 5)
                }
            } else if rows.isEmpty {
                Text(L("Пусто")).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
            } else if list.isEmpty {
                Empty(title: L("Ничего не нашлось"), icon: "magnifyingglass").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: Style.Space.s) {
                        ForEach(Array(list.enumerated()), id: \.element["path"].str) { i, f in
                            if i == 0 || Fmt.dayGroup(list[i - 1]["mtime"]) != Fmt.dayGroup(f["mtime"]) {
                                Text(Fmt.dayTitle(f["mtime"])).font(Style.Text.caption.weight(.semibold)).foregroundStyle(Style.Colors.tertiaryText)
                                    .padding(.horizontal, Style.Space.l).padding(.top, i == 0 ? 0 : Style.Space.m).padding(.bottom, Style.Space.xxs)
                            }
                            FileRow(file: f, selected: selected == f["path"].str, showFolder: folder.isEmpty, select: { selected = f["path"].str }, open: { openFile(model, f["path"].str) })
                                .transition(Style.Motion.remove)
                        }
                    }
                    .motion(value: list.count)
                }
            }
        }, trailing: {
            if loaded && !rows.isEmpty { Text(summary(list)).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).contentTransition(.numericText()) }
        })
    }

    func refresh() async {
        do {
            let r = try await model.backend.get("files")
            loadError = nil
            if r.isSame(lastRaw) { loaded = true } else { lastRaw = r; withAnimation(spring) { rows = r.array; loaded = true } }
            pick()
        } catch { loadError = error.localizedDescription }
    }
}

/// Open the file with its default app. The server doesn't open apps, scripts or pages (it replies why, the toast shows that):
/// a double click shouldn't run anything. "Show in Finder" works for any file
@MainActor func openFile(_ model: AppModel, _ path: String) {
    model.run(L("Открыть"), { try await model.backend.act("open_file", ["path": path]) })
}

// ---- list row ----

struct FileRow: View {
    let file: JSON
    let selected: Bool
    var showFolder = true
    let select: () -> Void
    let open: () -> Void   // double click
    var body: some View {
        RecordCard(icon: fileIcon(file), tint: folderColor(file["folder"].str), title: file["name"].str, lines: 1, selected: selected, action: select,
                   leading: AnyView(FileIcon(file: file, side: Style.Size.badge).clipShape(Circle())), meta: {   // icon and tint are required by the card but aren't drawn when it has its own icon
            if showFolder { Tag(text: FOLDER_RU[file["folder"].str] ?? file["folder"].str, color: folderColor(file["folder"].str)) }
            Text([fileKind(file), Fmt.bytes(file["size"].dbl)].joined(separator: " · ")).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText).lineLimit(1)
        }, trailing: { _ in
            Text(Fmt.time(file["mtime"])).font(Style.Text.caption.monospacedDigit()).foregroundStyle(Style.Colors.secondaryText)
        })
        .simultaneousGesture(TapGesture(count: 2).onEnded { _ in open() })   // the card itself handles a single click: a double one is added alongside it, not on top
    }
}

/// A photo thumbnail or an icon by file kind in a rounded square (in the list, the card, and the attachment above the input field)
struct FileIcon: View {
    let file: JSON
    let side: CGFloat
    var symbol: Font = Style.Icon.chip
    @State private var thumb: NSImage? = nil
    var body: some View {
        ZStack {
            if let t = thumb {
                Image(nsImage: t).resizable().scaledToFill()
            } else {
                RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).fill(Style.Colors.theirs)
                Glyph(fileIcon(file)).font(symbol).foregroundStyle(Style.Colors.secondaryText)
            }
        }
        .frame(width: side, height: side)
        .clipShape(RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
        .motion(Style.Motion.quick, value: thumb == nil)
        .task(id: file["path"].str) { thumb = file["kind"].str == "image" ? await Thumbs.make(file["path"].str, side: side) : nil }
    }
}

/// Photo thumbnails: computed in the background from the file on disk (ImageIO doesn't decode the whole image), once per path and size
enum Thumbs {
    static let cache = NSCache<NSString, NSImage>()
    private static let scale: CGFloat = 2   // for Retina screens

    /// A side × side square, filled by the short side (file list, attachment above the input field)
    static func make(_ path: String, side: CGFloat) async -> NSImage? {
        await load(path, key: "\(path)|\(Int(side))") { w, h in side * scale * max(w, h) / max(1, min(w, h)) }
    }
    /// The whole image, fit into a frame (a photo in a chat bubble)
    static func make(_ path: String, fit box: CGSize) async -> NSImage? {
        await load(path, key: "\(path)|\(Int(box.width))x\(Int(box.height))") { w, h in max(w, h) * min(1, min(box.width / max(w, 1), box.height / max(h, 1))) * scale }
    }
    /// pixels gets the image's dimensions (accounting for the shot's rotation) and returns how many points its longest side should occupy. Small images aren't stretched up
    private static func load(_ path: String, key: String, pixels: (CGFloat, CGFloat) -> CGFloat) async -> NSImage? {
        if let c = cache.object(forKey: key as NSString) { return c }
        if Task.isCancelled { return nil }   // the row has already scrolled past: nothing to compute for
        guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
              let info = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [CFString: Any],
              let pw = info[kCGImagePropertyPixelWidth] as? NSNumber, let ph = info[kCGImagePropertyPixelHeight] as? NSNumber else { return nil }
        let turned = ((info[kCGImagePropertyOrientation] as? NSNumber)?.intValue ?? 1) >= 5   // shot sideways: width and height are swapped
        let w = CGFloat(turned ? ph.doubleValue : pw.doubleValue), h = CGFloat(turned ? pw.doubleValue : ph.doubleValue)
        let longest = max(w, h)
        let options: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceCreateThumbnailWithTransform: true,
                                        kCGImageSourceShouldCacheImmediately: true, kCGImageSourceThumbnailMaxPixelSize: Int(min(longest, max(1, pixels(w, h)).rounded(.up)))]
        guard let cg = CGImageSourceCreateThumbnailAtIndex(src, 0, options as CFDictionary) else { return nil }
        let img = NSImage(cgImage: cg, size: NSSize(width: CGFloat(cg.width) / scale, height: CGFloat(cg.height) / scale))
        cache.setObject(img, forKey: key as NSString)
        return img
    }
}

// ---- file card: details, actions, content ----

struct FileCard: View {
    @EnvironmentObject var model: AppModel
    let file: JSON
    @State private var image: NSImage? = nil
    @State private var text: String? = nil
    @State private var cut = false        // the text isn't shown in full
    @State private var lines = 0
    @State private var loading = false
    @State private var problem: String? = nil   // why there's no content, when known for sure (too big, empty, server refused)

    static let previewMax = Style.Size.bubbleMax        // the image and the PDF's first page go no higher than this
    nonisolated static let textLimit = 30_000            // characters of text on screen: beyond this, only the start
    static let fetchLimit: Double = 20 * 1024 * 1024     // bytes: beyond this, we don't fetch for preview
    var path: String { file["path"].str }
    var kind: String { file["kind"].str }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Style.Space.xl) {
                info
                content
            }
            .motion(value: loading)
            .motion(value: image == nil)
            .motion(value: text == nil)
        }
        .task(id: path) { await load() }
    }

    var info: some View {
        Block(L("Файл"), lead: true, content: {
            HStack(alignment: .top, spacing: Style.Space.l) {
                FileIcon(file: file, side: Style.Size.chip, symbol: Style.Icon.big)
                VStack(alignment: .leading, spacing: Style.Space.xs) {
                    Text(file["name"].str).font(Style.Text.title).lineLimit(2).truncationMode(.middle).textSelection(.enabled)
                    HStack(spacing: Style.Space.s) {
                        Tag(text: FOLDER_RU[file["folder"].str] ?? file["folder"].str, color: folderColor(file["folder"].str))
                        Tag(text: fileKind(file))
                        Text(Fmt.bytes(file["size"].dbl)).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
                    }
                }
            }
            KV(rows: [(L("Изменён"), Fmt.whenFull(file["mtime"])), (L("Давность"), Fmt.ago(file["mtime"].dbl)), (L("На диске"), file["file"].str)])
            HStack(spacing: Style.Space.s) {
                Text(L("Путь")).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText)
                Text(path).font(Style.Text.monoSmall).lineLimit(1).truncationMode(.middle).textSelection(.enabled)
                IconButton(icon: "doc.on.doc", label: L("Скопировать путь")) { model.copy(path, toast: L("Путь скопирован")) }.help(L("Скопировать путь"))
            }
            HStack(spacing: Style.Space.m) {
                Button(L("Отправить Сене")) { model.pendingAttachment = file; model.open(.chat, tab: 0) }.buttonStyle(.primary).help(L("Прикрепить к сообщению в чате"))
                Button(L("Открыть")) { openFile(model, path) }.help(L("Открыть в программе по умолчанию"))
                Button(L("Показать в Finder")) { model.run(L("Finder"), { try await self.model.backend.act("reveal", ["path": self.path]) }) }
            }
            .padding(.top, Style.Space.xs)
        })
    }

    var content: some View {
        Block(L("Содержимое"), content: {
            if loading {
                Loading().frame(height: Style.Size.tile)
            } else if let img = image {
                Image(nsImage: img).resizable().scaledToFit()
                    .frame(maxWidth: .infinity, maxHeight: FileCard.previewMax, alignment: .leading)
                    .clipShape(RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
                    .onTapGesture(count: 2) { openFile(model, path) }
                    .help(L("Двойной щелчок — открыть"))
            } else if let t = text {
                Text(t).font(Style.Text.mono).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
            } else {
                HStack(spacing: Style.Space.l) {
                    Glyph(fileIcon(file)).font(Style.Icon.big).foregroundStyle(Style.Colors.secondaryText).hoverScale(Style.Motion.hoverScale)
                    Text(noPreviewTitle).font(Style.Text.heading)
                }
                .padding(.vertical, Style.Space.m)
            }
        }, trailing: {
            if let note = note { Text(note).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText) }
        })
    }

    var note: String? {
        if loading { return nil }
        if let img = image {
            if kind == "pdf" { return L("первая страница") }
            let rep = img.representations.first   // size in pixels, not in points
            return "\(rep?.pixelsWide ?? Int(img.size.width)) × \(rep?.pixelsHigh ?? Int(img.size.height))"
        }
        if text != nil { return cut ? L("показано начало") : "\(lines) \(Fmt.plural(lines, "строка", "строки", "строк"))" }
        return nil
    }
    var noPreviewTitle: String {
        if let p = problem { return p }
        switch kind {
        case "sheet": return L("Таблица")
        case "doc": return L("Документ")
        case "pdf": return L("PDF не открылся")
        case "image": return L("Картинка не открылась")
        default: return isVideo(file) ? L("Видео") : isAudio(file) ? L("Аудио") : L("Предпросмотра нет")
        }
    }
    func load() async {
        image = nil; text = nil; cut = false; problem = nil
        guard ["image", "pdf", "text"].contains(kind) else { return }
        guard file["size"].dbl <= FileCard.fetchLimit else { problem = L("Слишком большой для просмотра"); return }
        loading = true
        defer { loading = false }
        do {
            let d = try await model.backend.bytes("/desk/api/file?path=" + Backend.enc(path))   // not 200 — an error, not "content" from the server's response text
            if d.isEmpty { problem = L("Файл пустой"); return }
            if kind == "text" {
                let r = await Task.detached(priority: .userInitiated) { FileCard.decode(d) }.value   // parsing up to 20 MB of text happens off the main thread
                text = r.text; cut = r.cut; lines = r.lines
            } else {
                image = NSImage(data: d)   // PDF too: AppKit draws the first page
            }
        } catch {
            if !Task.isCancelled { problem = String(format: L("Не открылся: %@"), error.localizedDescription) }
        }
    }
    /// Start of the text: only the needed bytes; UTF-8 encoding, and if it's not that — Windows-1251 (Russian texts come from there)
    nonisolated static func decode(_ d: Data) -> (text: String, cut: Bool, lines: Int) {
        let head = d.prefix(textLimit * 4)   // up to four bytes per character in UTF-8: no more of the start is needed
        var s = String(decoding: head, as: UTF8.self)
        let broken = s.unicodeScalars.filter { $0 == "\u{FFFD}" }.count
        if broken > (head.count < d.count ? 1 : 0), let win = String(data: head, encoding: .windowsCP1251) { s = win }   // one "broken" character at the truncated end isn't an encoding issue
        let cut = head.count < d.count || s.count > textLimit
        if s.count > textLimit { s = String(s.prefix(textLimit)) }
        return (cut ? s + "\n…" : s, cut, s.split(separator: "\n", omittingEmptySubsequences: false).count)
    }
}

// ---- file appearance: icon, label, folder color ----

private func isVideo(_ f: JSON) -> Bool { let p = f["path"].str.lowercased(); return [".mp4", ".mov", ".m4v", ".webm"].contains { p.hasSuffix($0) } }
private func isAudio(_ f: JSON) -> Bool { let p = f["path"].str.lowercased(); return [".m4a", ".mp3", ".ogg", ".oga", ".wav", ".aac"].contains { p.hasSuffix($0) } }
func fileIcon(_ f: JSON) -> String {
    if isVideo(f) { return "film" }
    if isAudio(f) { return "waveform" }
    let p = f["path"].str.lowercased()
    if [".zip", ".gz", ".tar", ".7z", ".rar"].contains(where: { p.hasSuffix($0) }) { return "doc.zipper" }
    switch f["kind"].str {
    case "image": return "photo"; case "pdf": return "doc.richtext"; case "sheet": return "tablecells"; case "doc": return "doc.text"; case "text": return "doc.plaintext"
    default: return "doc"
    }
}
private func fileKind(_ f: JSON) -> String {
    switch f["kind"].str {
    case "image": return L("фото"); case "pdf": return "PDF"; case "sheet": return L("таблица"); case "doc": return L("документ"); case "text": return L("текст")
    default:
        if isVideo(f) { return L("видео") }
        if isAudio(f) { return L("аудио") }
        let ext = (f["name"].str as NSString).pathExtension
        return ext.isEmpty ? L("файл") : ext.uppercased()
    }
}
private func folderColor(_ folder: String) -> Color {
    switch folder { case "присланное": return Style.Colors.info; case "скачанное": return Style.Colors.warn; case "сделанное": return Style.Colors.ok; default: return Style.Colors.ink }
}
/// Translation of folder names for display; comparisons with the server (folderColor, filter) use the original Russian keys — that's a protocol, not text
private let FOLDER_RU = ["присланное": L("присланное"), "скачанное": L("скачанное"), "сделанное": L("сделанное")]
