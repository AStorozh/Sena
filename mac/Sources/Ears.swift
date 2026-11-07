// The live voice from the Mac's microphone (sena/phone/voice.py): the microphone streams to Sena all the time, she listens for
// her name here on the Mac (nothing leaves it before the name), answers in about a second in her own voice (Gemini Live), and her
// brain answers the questions. This file: capturing the microphone (converted to 16 kHz mono PCM), sending it, playing her voice
// as it comes (24 kHz PCM), and the events for the shelf: her name opens "Chat", what was heard and said shows there, "flush"
// stops her voice at once. Her voice goes from the network thread straight to the speaker: the main thread (the shelf, polling)
// never delays it, and the app says when it has really played ("played"), so the microphone opens right after her and not on
// a guess. While she speaks the server doesn't pass the microphone on; her name said over her stops her. Turned on and off in
// the shelf's settings (macEars); her voice can be muted there (macVoiceReply) — the conversation then shows as text.
import AVFoundation
import Foundation

let micFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!     // what Sena hears
let voiceFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false)!   // what she says
let VOICE_PORT = 8767   // the live voice's own port beside PORT (sena/config.py VOICE_PORT)

/// The stream to Sena. The microphone's audio thread sends into it and the main thread opens and closes it: its own lock.
/// held — a voice message is being recorded in chat: the microphone's sound isn't sent, so the live voice neither wakes on the
/// dictated "Sena, …" (the message itself goes to her) nor keeps it as overheard speech
final class VoiceLink: @unchecked Sendable {
    private let lock = NSLock()
    private var task: URLSessionWebSocketTask?
    private var paused = false
    var current: URLSessionWebSocketTask? { lock.lock(); defer { lock.unlock() }; return task }
    var held: Bool {
        get { lock.lock(); defer { lock.unlock() }; return paused }
        set { lock.lock(); paused = newValue; lock.unlock() }
    }
    func set(_ t: URLSessionWebSocketTask?) { lock.lock(); task = t; lock.unlock() }
    func send(_ data: Data) { if !held { current?.send(.data(data)) { _ in } } }   // a lost piece is no error: the stream goes on
    func send(_ text: String) { current?.send(.string(text)) { _ in } }
}

/// One piece of the microphone, in the device's own format -> 16 kHz mono 16-bit PCM. Outside the main actor on purpose:
/// it runs on the audio thread, and the converter keeps the resampler's state from piece to piece
func micPCM(_ buffer: AVAudioPCMBuffer, _ converter: AVAudioConverter) -> Data? {
    let capacity = AVAudioFrameCount(Double(buffer.frameLength) * micFormat.sampleRate / buffer.format.sampleRate) + 64
    guard let out = AVAudioPCMBuffer(pcmFormat: micFormat, frameCapacity: capacity) else { return nil }
    var given = false
    var error: NSError?
    let status = converter.convert(to: out, error: &error) { _, state in
        if given { state.pointee = .noDataNow; return nil }
        given = true
        state.pointee = .haveData
        return buffer
    }
    guard status != .error, out.frameLength > 0, let samples = out.int16ChannelData else { return nil }
    return Data(bytes: samples[0], count: Int(out.frameLength) * MemoryLayout<Int16>.size)
}

/// Her voice: 24 kHz 16-bit little-endian pieces -> a buffer the player takes
func voiceBuffer(_ data: Data) -> AVAudioPCMBuffer? {
    let frames = AVAudioFrameCount(data.count / MemoryLayout<Int16>.size)
    guard frames > 0, let buffer = AVAudioPCMBuffer(pcmFormat: voiceFormat, frameCapacity: frames), let out = buffer.floatChannelData?[0] else { return nil }
    buffer.frameLength = frames
    data.withUnsafeBytes { raw in
        let samples = raw.bindMemory(to: Int16.self)
        for i in 0..<Int(frames) { out[i] = Float(Int16(littleEndian: samples[i])) / 32768 }
    }
    return buffer
}

/// The microphone and her voice on one engine. Every engine and player call goes through one serial queue, in order: the network
/// thread's pieces of her voice, the main thread's start, stop and a restart on a new device — a piece played on an engine that a
/// device change had just stopped raised an exception and killed the app. The audio thread only sends the microphone. The lock
/// guards only the count of her pieces still to play and is never held while the engine is called: an engine call can wait for
/// the audio threads, and those take the lock (a hang of the whole app, 25.10.2026 — the second engine with voice processing
/// locked up this way, so it is gone until it is done without that risk).
final class VoiceAudio: @unchecked Sendable {
    private let lock = NSLock()
    private let link: VoiceLink
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let serial = DispatchQueue(label: "sena.voice.engine")
    private var queued = 0              // pieces of her voice scheduled and not yet played
    private var muted = false           // the owner turned her voice off: the conversation is text only (read and written on serial)
    static let lead = 0.12              // s of silence before her first words: a late piece from the network doesn't break her speech

    init(link: VoiceLink) { self.link = link }

    /// The microphone and the player. False — the microphone didn't start
    func start() -> Bool { serial.sync { startNow() } }
    func stop() { serial.sync { stopNow() } }
    /// Again on a new device (headphones plugged in); false — the microphone is gone
    func restart() -> Bool { serial.sync { stopNow(); return startNow() } }
    func mute(_ on: Bool) { serial.async { self.muted = on } }

    private func startNow() -> Bool {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)   // the device's own: often 48 kHz float, sometimes several channels
        guard format.sampleRate > 0, format.channelCount > 0, let converter = AVAudioConverter(from: format, to: micFormat) else { return false }
        converter.downmix = true
        let link = self.link
        input.installTap(onBus: 0, bufferSize: AVAudioFrameCount(format.sampleRate / 10), format: format) { buffer, _ in
            if let data = micPCM(buffer, converter) { link.send(data) }
        }
        if player.engine == nil { engine.attach(player) }
        engine.connect(player, to: engine.mainMixerNode, format: voiceFormat)
        engine.prepare()
        do { try engine.start() } catch {
            input.removeTap(onBus: 0)
            return false
        }
        player.play()
        return true
    }

    private func stopNow() {
        engine.inputNode.removeTap(onBus: 0)
        player.stop()
        engine.stop()
        setQueued(0)
    }

    private func setQueued(_ n: Int) { lock.lock(); queued = n; lock.unlock() }

    /// Her voice: played in order; a fresh start gets a moment of silence first, so a late piece doesn't break her speech
    func play(_ data: Data) { serial.async { self.playNow(data) } }

    private func playNow(_ data: Data) {
        if muted { link.send(#"{"type":"played"}"#); return }   // nothing plays: the microphone opens now, not after her unheard voice
        guard engine.isRunning, let buffer = voiceBuffer(data) else { return }   // the engine is between devices: the piece is dropped
        lock.lock()
        let fresh = queued == 0
        queued += fresh ? 2 : 1
        lock.unlock()
        if fresh, let lead = AVAudioPCMBuffer(pcmFormat: voiceFormat, frameCapacity: AVAudioFrameCount(voiceFormat.sampleRate * VoiceAudio.lead)) {
            lead.frameLength = lead.frameCapacity   // zeros
            player.scheduleBuffer(lead, completionCallbackType: .dataPlayedBack) { [weak self] _ in self?.donePlaying() }
        }
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in self?.donePlaying() }
        if engine.isRunning && !player.isPlaying { player.play() }   // asked again: a device change stops the engine on its own, off this queue
    }

    private func donePlaying() {
        lock.lock()
        queued = max(0, queued - 1)
        let empty = queued == 0
        lock.unlock()
        if empty { link.send(#"{"type":"played"}"#) }   // the server opens the microphone right after her, not on a guess
    }

    /// She was cut off, or it wasn't her name after all: silence at once
    func flush() {
        serial.async {
            self.player.stop()
            if self.engine.isRunning { self.player.play() }
            self.setQueued(0)
            self.link.send(#"{"type":"played"}"#)
        }
    }

    var engineForChanges: AVAudioEngine { engine }
}

@MainActor
final class Ears: NSObject, ObservableObject {
    static let shared = Ears()
    @Published private(set) var running = false     // the microphone is on
    @Published private(set) var connected = false   // the stream to Sena is up (it isn't while Sena is off or restarting)
    @Published private(set) var talking = false     // a conversation is on: from her name to its end
    @Published private(set) var thinking = false    // her brain is working on a question
    @Published private(set) var lastHeard: String?  // what was heard in the conversation (to show in the shelf)
    @Published private(set) var lastSaid: String?   // and what she said
    @Published private(set) var refused: String?    // why the server didn't take the stream, when it said so (the hearing switch is off)
    private var lastAt: Date?                        // when: the shelf shows a fresh one on top of the server's feed
    private(set) var heardAt: Date?                  // when this turn's words began to be heard
    private(set) var saidAt: Date?                   // and when her answer began
    var fresh: Bool { talking || (lastAt.map { Date().timeIntervalSince($0) < 120 } ?? false) }

    private weak var model: AppModel?
    private let link = VoiceLink()
    private let audio: VoiceAudio
    private var loop: Task<Void, Never>?
    private var changes: NSObjectProtocol?
    static let retry: [UInt64] = [1, 2, 5, 10, 20]   // seconds before knocking again: Sena may be off or restarting

    var enabled: Bool { UserDefaults.standard.bool(forKey: "macEars") }
    var voiceReply: Bool { UserDefaults.standard.object(forKey: "macVoiceReply") as? Bool ?? true }

    override init() {
        audio = VoiceAudio(link: link)
        super.init()
    }

    /// Called when the app starts and after a switch in the shelf's settings is toggled ("Отвечать голосом" applies at once, mid-conversation too)
    func sync(_ model: AppModel) {
        self.model = model
        audio.mute(!voiceReply)
        if enabled && !running { Task { await start() } }
        if !enabled && running { stop() }
    }

    /// A voice message is being recorded in chat (Recorder): the live voice doesn't hear it until it ends — the dictated
    /// "Sena, …" goes to her as the message, not a second time as a spoken command or as overheard speech
    func hold(_ on: Bool) {
        link.held = on
        if on && talking { link.send(#"{"type":"stop"}"#) }   // a conversation was on: the owner moved to typing a message
    }

    private func start() async {
        guard !running else { return }
        guard await AVCaptureDevice.requestAccess(for: .audio) else {
            model?.say(L("Нет доступа к микрофону: разреши его в Системных настройках → Конфиденциальность и безопасность → Микрофон"), hold: 9)
            UserDefaults.standard.set(false, forKey: "macEars")
            return
        }
        guard enabled, !running else { return }   // while waiting for permission, the switch could have been turned back off
        guard audio.start() else { model?.say(L("Микрофон не запустился")); return }
        running = true
        // another microphone or speaker (headphones plugged in): the engine stops by itself — start it again on the new device
        changes = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: audio.engineForChanges, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.restartEngine() }
        }
        loop = Task { await self.keepConnected() }
    }

    func stop() {
        loop?.cancel()
        loop = nil
        if let changes { NotificationCenter.default.removeObserver(changes) }
        changes = nil
        link.current?.cancel(with: .goingAway, reason: nil)
        link.set(nil)
        audio.stop()
        running = false
        connected = false
        talking = false
        thinking = false
        refused = nil
    }

    private func restartEngine() {
        guard running else { return }
        if !audio.restart() {   // the microphone is gone (unplugged): really off, not a dead "Listening" on screen
            stop()
            model?.say(L("Микрофон пропал: фоновый слух выключен"))
        }
    }

    /// The stream to Sena: opened, read until it breaks, opened again. Her voice goes straight to the speaker from the network
    /// thread; only the events for the shelf come to the main thread
    private func keepConnected() async {
        var attempt = 0
        while !Task.isCancelled && running {
            if let task = await open() {
                connected = true
                attempt = 0
                link.set(task)
                let audio = self.audio
                await Task.detached { await Ears.pump(task, audio) { e in Task { @MainActor in Ears.shared.handle(e) } } }.value
                link.set(nil)
                task.cancel(with: .goingAway, reason: nil)
                connected = false
                if talking { handle(JSON(["type": "end"])) }   // the stream broke mid-conversation: the shelf doesn't hang on "talking"
            }
            let pause = Ears.retry[min(attempt, Ears.retry.count - 1)]
            attempt += 1
            try? await Task.sleep(nanoseconds: pause * 1_000_000_000)
        }
    }

    /// Reads the stream until it breaks: her voice to the speaker at once, events to the shelf
    private nonisolated static func pump(_ task: URLSessionWebSocketTask, _ audio: VoiceAudio, _ event: @escaping @Sendable (JSON) -> Void) async {
        while true {
            guard let message = try? await task.receive() else { return }
            switch message {
            case .data(let d): audio.play(d)
            case .string(let s):
                let e = JSON(try? JSONSerialization.jsonObject(with: Data(s.utf8)))
                if e["type"].str == "flush" { audio.flush() }   // at once, not after the main thread gets to it
                event(e)
            @unknown default: break
            }
        }
    }

    /// One knock at the live voice: the same password as the app's API; open — when the server says "ready" (its first word;
    /// a ping can't tell: URLSession holds its answer back behind messages not yet read)
    private func open() async -> URLSessionWebSocketTask? {
        guard let model, !model.backend.token.isEmpty else { return nil }
        let backend = model.backend
        guard let url = URL(string: "ws://127.0.0.1:\(VOICE_PORT)/ear?device=mac" + (backend.headerToken ? "" : "&t=\(Backend.enc(backend.token))")) else { return nil }
        var req = URLRequest(url: url)
        if backend.headerToken { req.setValue("Bearer \(backend.token)", forHTTPHeaderField: "Authorization") }   // as every request (Backend.request)
        req.setValue(Backend.zone, forHTTPHeaderField: "X-Sena-Zone")
        let task = URLSession.shared.webSocketTask(with: req)
        task.maximumMessageSize = 4 * 1024 * 1024
        task.resume()
        guard case .string(let first)? = try? await task.receive(), JSON(try? JSONSerialization.jsonObject(with: Data(first.utf8)))["type"].str == "ready" else {
            // 409 from a running Sena: she won't take the Mac's microphone while "Слух" is off in the integrations (sena/phone/voice.py)
            let code = (task.response as? HTTPURLResponse)?.statusCode
            refused = code == 409 && model.alive ? L("Слух выключен в интеграциях") : code == 403 ? L("Сервер не принял ключ") : nil
            task.cancel()
            return nil
        }
        refused = nil
        return task
    }

    private func handle(_ e: JSON) {
        switch e["type"].str {
        case "wake":   // her name: the shelf opens to "Chat"
            talking = true
            lastHeard = nil
            lastSaid = nil
            heardAt = nil
            saidAt = nil
            lastAt = Date()
            audio.mute(!voiceReply)
            if let model { NotchPanel.shared.showVoice(model) }
        case "heard":
            if lastHeard == nil || (saidAt ?? .distantPast) > (heardAt ?? .distantPast) { heardAt = Date() }   // a new turn's words
            lastHeard = e["text"].str
            lastAt = Date()
        case "said":
            if lastSaid == nil || (heardAt ?? .distantPast) > (saidAt ?? .distantPast) { saidAt = Date() }
            lastSaid = e["text"].str
            lastAt = Date()
        case "thinking":
            thinking = e["on"].bool
        case "end":
            talking = false
            thinking = false
            lastAt = Date()
            if let model { Task { await model.poll() } }   // what her brain did shows in the app right away
        default: break
        }
    }
}
