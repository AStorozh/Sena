// Recording voice from the Mac's microphone: AAC 16 kHz mono into a temp file. The volume level is for the live wave in chat.
// Transcription is done by the server (/voice); here only the audio is recorded.
import AVFoundation
import Foundation

@MainActor
final class Recorder: NSObject, ObservableObject, AVAudioRecorderDelegate {
    /// Time and levels change every 60 ms: only the recording bar (RecordingBar) watches them, not the whole chat, which would otherwise redraw 16 times a second
    final class Meter: ObservableObject {
        @Published fileprivate(set) var seconds: Double = 0
        @Published fileprivate(set) var levels: [CGFloat] = Array(repeating: 0, count: Style.Size.waveBars)   // the latest levels, 0…1
    }
    @Published private(set) var recording = false
    let meter = Meter()
    var seconds: Double { meter.seconds }
    private var rec: AVAudioRecorder?
    private var timer: Timer?
    private var starting = false   // waiting for microphone permission: a second tap during this does nothing
    private let url = FileManager.default.temporaryDirectory.appendingPathComponent("sena-voice.m4a")

    /// Start recording; nil means it started, otherwise the reason it didn't
    func start() async -> String? {
        guard !recording, !starting else { return nil }
        Speaker.shared.stop()   // he starts talking: her previous answer stops
        starting = true
        defer { starting = false }
        guard await AVCaptureDevice.requestAccess(for: .audio) else {
            return L("Нет доступа к микрофону: разреши его в Системных настройках → Конфиденциальность и безопасность → Микрофон")
        }
        let settings: [String: Any] = [AVFormatIDKey: Int(kAudioFormatMPEG4AAC), AVSampleRateKey: 16000, AVNumberOfChannelsKey: 1,
                                       AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue]
        do {
            let r = try AVAudioRecorder(url: url, settings: settings)
            r.delegate = self
            r.isMeteringEnabled = true
            guard r.record() else { return L("Микрофон не запустился") }
            rec = r
            recording = true
            Ears.shared.hold(true)   // the live voice doesn't hear the dictation: the message goes to Sena once, as a message
            meter.seconds = 0
            meter.levels = Array(repeating: 0, count: Style.Size.waveBars)
            timer?.invalidate()
            let t = Timer(timeInterval: 0.06, repeats: true) { [weak self] t in
                MainActor.assumeIsolated { if let self = self { self.tick() } else { t.invalidate() } }
            }
            RunLoop.main.add(t, forMode: .common)   // doesn't freeze while a menu is open or the window is being resized
            timer = t
            return nil
        } catch {
            return String(format: L("Запись не началась: %@"), error.localizedDescription)
        }
    }

    private func tick() {
        guard let r = rec else { return }
        r.updateMeters()
        let db = r.averagePower(forChannel: 0)                    // from -160 (silence) to 0
        let level = CGFloat(max(0, min(1, (db + 50) / 50)))       // silence is around -50 dB, speech closer to 0
        meter.levels.removeFirst()
        meter.levels.append(level)
        meter.seconds = r.currentTime
    }

    /// Stop and return the audio (m4a); the voice file isn't left lying around in the temp folder afterward
    func stop() -> Data? {
        finish()
        defer { try? FileManager.default.removeItem(at: url) }
        return try? Data(contentsOf: url)
    }

    func cancel() {
        finish()
        try? FileManager.default.removeItem(at: url)
    }

    /// The recording stopped on its own (headphones disconnected, sleep, codec error): without this the screen would keep showing "recording 0:00"
    nonisolated func audioRecorderDidFinishRecording(_ r: AVAudioRecorder, successfully flag: Bool) {
        Task { @MainActor in if !flag, self.rec === r { self.finish() } }
    }
    nonisolated func audioRecorderEncodeErrorDidOccur(_ r: AVAudioRecorder, error: Error?) {
        Task { @MainActor in if self.rec === r { self.finish() } }
    }

    private func finish() {
        timer?.invalidate()
        timer = nil
        rec?.stop()
        rec = nil
        recording = false
        Ears.shared.hold(false)
    }
}

/// Her answer to a dictated message, said aloud: the server turns the text into her voice (/speak), this plays it. While she
/// speaks the live voice doesn't pass the microphone on — her own voice from the speaker must not wake her or be overheard
@MainActor
final class Speaker: NSObject, AVAudioPlayerDelegate {
    static let shared = Speaker()
    private var player: AVAudioPlayer?
    private var turn = 0   // a newer answer replaces the one still playing; a late reply for an old one is dropped

    func say(_ model: AppModel, _ text: String) {
        let words = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !words.isEmpty else { return }
        turn += 1
        let mine = turn
        Task {
            do {
                let mp3 = try await model.backend.speak(words)
                guard mine == turn else { return }
                stop()
                let p = try AVAudioPlayer(data: mp3)
                p.delegate = self
                player = p
                Ears.shared.hold(true)
                if !p.play() { stop() }
            } catch {
                model.say(String(format: L("Ответ не озвучился: %@"), error.localizedDescription), hold: 7)
            }
        }
    }

    func stop() {
        player?.stop()
        player = nil
        Ears.shared.hold(false)
    }

    nonisolated func audioPlayerDidFinishPlaying(_ p: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in if self.player === p { self.stop() } }
    }
}
