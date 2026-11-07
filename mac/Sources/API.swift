// Connection to Sena: where the project and the token are, API requests (/desk/api/...), the screen-without-core server (python -m sena.phone).
import Foundation

let PORT = 8765

/// A lightweight wrapper over JSON from the server: o["core"]["alive"].bool, o["active"].array, o["today"]["usd"].dbl
struct JSON {
    let raw: Any?
    init(_ raw: Any?) { self.raw = raw is NSNull ? nil : raw }
    subscript(key: String) -> JSON { JSON((raw as? [String: Any])?[key]) }
    subscript(index: Int) -> JSON { let a = raw as? [Any] ?? []; return JSON(index < a.count ? a[index] : nil) }
    var string: String? { raw as? String }
    var str: String { string ?? "" }
    /// A string, or a number as text: a field's default may be a number (7 days, $3 of budget) — str would make it empty
    var text: String {
        if let s = string { return s }
        guard let d = double else { return "" }
        return d == d.rounded() && abs(d) < 1e15 ? String(Int(d)) : String(d)
    }
    var double: Double? { (raw as? NSNumber)?.doubleValue }
    var dbl: Double { double ?? 0 }
    var int: Int { Int(double ?? 0) }
    var bool: Bool { (raw as? NSNumber)?.boolValue ?? false }
    var array: [JSON] { (raw as? [Any])?.map(JSON.init) ?? [] }
    var dict: [String: JSON] { (raw as? [String: Any])?.mapValues(JSON.init) ?? [:] }
    var exists: Bool { raw != nil }
    /// Same content: screens that poll don't redraw until the data actually changes
    func isSame(_ o: JSON) -> Bool {
        switch (raw, o.raw) {
        case (nil, nil): return true
        case let (a?, b?): return (a as AnyObject).isEqual(b)
        default: return false
        }
    }
    var pretty: String {
        guard let raw = raw, JSONSerialization.isValidJSONObject(raw), let data = try? JSONSerialization.data(withJSONObject: raw, options: [.prettyPrinted, .sortedKeys]) else { return "\(raw ?? "—")" }
        return String(decoding: data, as: UTF8.self)
    }
}

struct APIError: LocalizedError { let message: String; var errorDescription: String? { message } }

final class Backend {
    static let shared = Backend()
    static let defaultPython = "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13"
    static let maxUpload = 50 * 1024 * 1024   // this is what the server accepts (MAX_BODY in sena/phone/server.py; tests/test_mac.py keeps them equal)
    /// Actions that take longer than usual on the server (paid calls, reconciliations, code changes, a confirmed errand, a panel's
    /// check): they get more time for a reply
    static let slowActions: Set<String> = ["embed", "sync_index", "sync_vault", "recost", "change_apply", "backup", "weekly", "tidy", "heartbeat", "comment",
                                           "confirm_send", "panel_act"]   // comment: a remark is reviewed right away by the paid judge
    // reload() writes from a background thread, and requests read from the main one: guarded by a lock
    private let lock = NSLock()
    private var found = (root: URL(fileURLWithPath: NSHomeDirectory()), data: URL(fileURLWithPath: NSHomeDirectory()), token: "", python: Backend.defaultPython)   // the real values are found by reload()
    var root: URL { lock.lock(); defer { lock.unlock() }; return found.root }       // the project folder
    var data: URL { lock.lock(); defer { lock.unlock() }; return found.data }       // Sena's data folder: data/ of the project, or SENA_DATA (as in sena/config.py)
    var token: String { lock.lock(); defer { lock.unlock() }; return found.token }
    var python: String { lock.lock(); defer { lock.unlock() }; return found.python }
    private var screen: Process?   // started off the main thread, read from it: under the lock
    private var startedAt: Double = 0
    var screenStartedAt: Double {
        get { lock.lock(); defer { lock.unlock() }; return startedAt }
        set { lock.lock(); startedAt = newValue; lock.unlock() }
    }
    var screenRunning: Bool { lock.lock(); defer { lock.unlock() }; return screen?.isRunning == true }

    enum Problem { case folder, token }

    /// Looks for the project folder and reads .env. Touches the disk, and on the macOS "Desktop" it may ask for permission and wait for
    /// an answer, so it's called off the main thread (AppModel.start)
    func reload() {
        let fm = FileManager.default
        let candidates = [Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent(),   // Sena/mac/Sena.app -> Sena
                          fm.homeDirectoryForCurrentUser.appendingPathComponent("Desktop/Sena")]
        let root = candidates.first { fm.fileExists(atPath: $0.appendingPathComponent("run.sh").path) } ?? candidates.last!
        let env = Backend.env(root)
        let python = ProcessInfo.processInfo.environment["PYTHON"] ?? env["PYTHON"] ?? Backend.defaultPython
        let named = ProcessInfo.processInfo.environment["SENA_DATA"] ?? env["SENA_DATA"] ?? ""   // the shell wins over .env, as in config.py
        let data = named.isEmpty ? root.appendingPathComponent("data")
            : URL(fileURLWithPath: (named as NSString).expandingTildeInPath, relativeTo: root).standardizedFileURL   // relative — to the project, where run.sh starts
        lock.lock(); found = (root, data, env["APP_TOKEN"] ?? "", python); lock.unlock()
    }
    /// What's missing to talk to the server: nil — everything's there
    var problem: Problem? {
        if !FileManager.default.isReadableFile(atPath: root.appendingPathComponent("run.sh").path) { return .folder }
        return token.isEmpty ? .token : nil
    }

    /// A .env line the way the shell understands it: "export KEY=value", quotes around the value, and for an unquoted value a comment after " #"
    static func env(_ root: URL) -> [String: String] {
        guard let text = try? String(contentsOf: root.appendingPathComponent(".env"), encoding: .utf8) else { return [:] }
        var out: [String: String] = [:]
        for line in text.split(separator: "\n") {
            var t = line.trimmingCharacters(in: .whitespaces)
            if t.hasPrefix("export ") { t = String(t.dropFirst(7)) }
            guard !t.hasPrefix("#"), let eq = t.firstIndex(of: "=") else { continue }
            var v = String(t[t.index(after: eq)...]).trimmingCharacters(in: .whitespaces)
            if v.count >= 2, (v.hasPrefix("\"") && v.hasSuffix("\"")) || (v.hasPrefix("'") && v.hasSuffix("'")) { v = String(v.dropFirst().dropLast()) }
            else if let hash = v.range(of: " #") { v = String(v[..<hash.lowerBound]).trimmingCharacters(in: .whitespaces) }
            out[String(t[..<eq]).trimmingCharacters(in: .whitespaces)] = v
        }
        return out
    }

    var base: String { "http://127.0.0.1:\(PORT)" }
    /// A value for the URL: everything but ASCII letters and digits gets encoded (otherwise "&", "+", "#" and "=" in a search would break
    /// the request, and Cyrillic would rely on URL(string:) fixing it up)
    static let plain = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
    static func enc(_ s: String) -> String { s.addingPercentEncoding(withAllowedCharacters: plain) ?? "" }
    /// The password goes in the Authorization header, never in the address (an address lands in logs on the way; the server takes
    /// either, sena/phone/server.given). Only a password a header can't carry — not plain ASCII — still rides in the address
    var headerToken: Bool { token.allSatisfy { $0.isASCII && !$0.isNewline } }
    func url(_ path: String) -> URL {
        let sep = path.contains("?") ? "&" : "?"
        let address = headerToken ? "\(base)\(path)" : "\(base)\(path)\(sep)t=\(Backend.enc(token))"
        return URL(string: address) ?? URL(string: base)!   // every piece is encoded: the fallback is never reached
    }
    /// A request to the server with the app's password and the owner's time zone
    func request(_ path: String) -> URLRequest {
        var req = URLRequest(url: url(path))
        if headerToken { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        req.setValue(Backend.zone, forHTTPHeaderField: "X-Sena-Zone")
        return req
    }

    /// The owner's current UTC offset, "+03:00": the server keeps it so Sena knows what time it is where he is
    static var zone: String {
        let seconds = TimeZone.current.secondsFromGMT()
        return String(format: "%@%02d:%02d", seconds < 0 ? "-" : "+", abs(seconds) / 3600, (abs(seconds) % 3600) / 60)
    }

    /// GET or POST (JSON) to the server; {"error": ...} becomes a thrown error
    func api(_ path: String, body: [String: Any]? = nil, timeout: Double = 8) async throws -> JSON {
        var req = request(path)
        req.timeoutInterval = timeout
        if let body = body {
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        return try await send(req)
    }
    func get(_ name: String, _ params: [String: String] = [:]) async throws -> JSON {
        let q = params.filter { !$0.value.isEmpty }.map { "\($0.key)=\(Backend.enc($0.value))" }.joined(separator: "&")
        return try await api("/desk/api/\(name)" + (q.isEmpty ? "" : "?" + q))
    }
    func act(_ name: String, _ body: [String: Any] = [:]) async throws -> JSON {
        try await api("/desk/api/\(name)", body: body, timeout: Backend.slowActions.contains(name) ? 300 : 30)
    }
    func bytes(_ path: String) async throws -> Data { try await rawBytes(request(path)) }
    func upload(_ file: URL) async throws -> JSON {
        let size = (try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
        if size > Backend.maxUpload { throw APIError(message: L("Файл больше 50 МБ: сервер такой не принимает")) }
        return try await upload(try Data(contentsOf: file), name: file.lastPathComponent)
    }
    /// A file (photo, video, document, a pasted clipboard image) — into data/inbox; reply: path, name, kind, note
    func upload(_ data: Data, name: String) async throws -> JSON {
        if data.count > Backend.maxUpload { throw APIError(message: L("Файл больше 50 МБ: сервер такой не принимает")) }
        return try await post("/desk/upload?name=\(Backend.enc(name).isEmpty ? "file" : Backend.enc(name))", data, type: "application/octet-stream")
    }
    /// Voice -> text (the server transcribes it with a model). A minute of speech can take a while at home: a generous wait
    func transcribe(_ audio: Data) async throws -> String { try await post("/voice", audio, type: "audio/mp4", timeout: 300)["text"].str }
    /// Her answer as speech (mp3, her one voice): said aloud after a dictated message
    func speak(_ text: String) async throws -> Data {
        var req = request("/speak")
        req.httpMethod = "POST"
        req.timeoutInterval = 120
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["text": text])
        return try await rawBytes(req)
    }

    private func post(_ path: String, _ body: Data, type: String, timeout: Double = 120) async throws -> JSON {
        var req = request(path)
        req.httpMethod = "POST"
        req.timeoutInterval = timeout
        req.setValue(type, forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        return try await send(req)
    }

    /// Raw response bytes (a file, mp3), not JSON: a server refusal arrives as JSON with a readable message ("File too large…") — we show that, not just the code
    private func rawBytes(_ req: URLRequest) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(for: req)
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        if code != 200 { throw APIError(message: JSON(try? JSONSerialization.jsonObject(with: data))["error"].string ?? String(format: L("сервер ответил %d"), code)) }
        return data
    }

    /// The server's reply as JSON: a server error, a non-200 code, or a body that can't be parsed (such a reply used to be silently treated as empty data) — all are errors
    private func send(_ req: URLRequest) async throws -> JSON {
        let (data, response) = try await URLSession.shared.data(for: req)
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        let raw = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
        let json = JSON(raw)
        if let err = json["error"].string { throw APIError(message: err) }
        if code != 200 { throw APIError(message: String(format: L("сервер ответил %d"), code)) }
        if raw == nil && !data.isEmpty { throw APIError(message: L("сервер ответил непонятно")) }
        return json
    }

    // ---- screen without core: the same server as the phone's, without the bot; comes up in a second ----
    var launchLog: URL { data.appendingPathComponent("logs/app_launch.log") }
    /// Touches the disk (the launch log) and may wait on the "Desktop" permission: called off the main thread (AppModel.poll)
    func startScreen() {
        lock.lock()
        if screen?.isRunning == true || Date().timeIntervalSince1970 - startedAt < 1 { lock.unlock(); return }   // a second call a moment later starts nothing
        startedAt = Date().timeIntervalSince1970   // before launching: if it fails, retries happen no more than once every 20 seconds
        lock.unlock()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-u", "-m", "sena.phone"]
        p.currentDirectoryURL = root
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        p.environment = env
        let fm = FileManager.default
        try? fm.createDirectory(at: launchLog.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let size = (try? fm.attributesOfItem(atPath: launchLog.path)[.size] as? Int) ?? nil, size > 1_000_000 { try? fm.removeItem(at: launchLog) }   // the launch log doesn't grow forever
        if !fm.fileExists(atPath: launchLog.path) { fm.createFile(atPath: launchLog.path, contents: nil) }
        let log = try? FileHandle(forWritingTo: launchLog)
        log?.seekToEndOfFile()
        log?.write(Data("\n=== экран из приложения \(Date()) ===\n".utf8))
        if let log = log { p.standardOutput = log; p.standardError = log }
        do { try p.run(); lock.lock(); screen = p; lock.unlock() }
        catch {
            NSLog("Не удалось запустить экран: \(error)")
            log?.write(Data("Не удалось запустить \(python): \(error.localizedDescription)\n".utf8))   // this will be shown by the splash screen instead of an endless "Opening…"
        }
    }
    func stopScreen() { lock.lock(); let p = screen; screen = nil; lock.unlock(); p?.terminate() }
    /// The core is on its way up (desk._mark_starting, run.sh before a restart): the app waits as long (desk.STARTING_FOR) instead of raising a screen
    var coreStarting: Bool {
        guard let text = try? String(contentsOf: data.appendingPathComponent("state/core_starting"), encoding: .utf8), let t = Double(text.trimmingCharacters(in: .whitespacesAndNewlines)) else { return false }
        return Date().timeIntervalSince1970 - t < 60
    }
    /// The last lines of the launch log: only the tail of the file is read, not the whole log
    func lastLogLines(_ n: Int = 3) -> String {
        guard let h = try? FileHandle(forReadingFrom: launchLog) else { return "" }
        defer { try? h.close() }
        let size = (try? h.seekToEnd()) ?? 0
        try? h.seek(toOffset: size > 4096 ? size - 4096 : 0)
        let text = String(decoding: (try? h.readToEnd()) ?? Data(), as: UTF8.self)
        return text.split(separator: "\n").suffix(n).joined(separator: "\n")
    }
}
