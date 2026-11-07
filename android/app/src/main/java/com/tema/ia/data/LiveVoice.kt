package com.tema.ia.data

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** Where a live conversation is: connecting, listening to the owner, her brain at work, over, or it failed. */
enum class VoicePhase { Connecting, Listening, Thinking, Ended, Failed }

/**
 * What the voice sheet shows: the phase, what Sena heard and what she said in this turn, why it failed (the server, the
 * microphone or the speaker), and why it ended, in the server's words ("тишина", "попрощались").
 */
data class VoiceState(
    val phase: VoicePhase = VoicePhase.Connecting,
    val heard: String = "",
    val said: String = "",
    val error: ApiException? = null,
    val micFailed: Boolean = false,
    val speakerFailed: Boolean = false,
    val reason: String? = null,
    val senaOff: Boolean = false,   // the stream didn't open because Sena herself is off (the Mac answers, its live voice doesn't)
)

/** The phone's microphone: 16 kHz 16-bit mono pieces of about 100 ms go to the callback until [stop]. False — it didn't start. */
interface Mic {
    fun start(onPiece: (ByteArray) -> Unit): Boolean
    fun stop()
}

/** The phone's speaker for her voice: 24 kHz 16-bit mono pieces, played one after another; [flush] silences her at once. */
interface Speaker {
    fun play(pcm: ByteArray)
    fun flush()
    fun release()
}

/**
 * A live conversation with Sena, the "talk" button (sena/phone/voice.py, mode=talk): the microphone streams to her Mac, she
 * answers in about a second in her own voice, and her brain answers the questions. No name is needed: pressing the button is
 * the call. The server hangs up when the conversation is over (a goodbye or a quiet pause); [stop] ends it by hand.
 * While she speaks the server doesn't pass the microphone on, so her voice from the speaker doesn't come back to her.
 */
class LiveVoice(
    private val url: String,
    private val mic: Mic,
    private val speaker: Speaker,
    private val client: OkHttpClient = voiceClient(),
    private val headers: Map<String, String> = emptyMap(),   // the password (Authorization), see SenaApi.voiceHeaders
) {
    private val _state = MutableStateFlow(VoiceState())
    val state: StateFlow<VoiceState> = _state.asStateFlow()
    @Volatile private var socket: WebSocket? = null
    @Volatile private var over = false
    @Volatile private var silenced = false   // ended by hand or failed: nothing more of her voice is played

    fun start() {
        val request = Request.Builder().url(url).header("X-Sena-Zone", zoneOffset()).apply { headers.forEach { (k, v) -> header(k, v) } }.build()
        socket = client.newWebSocket(request, listener)
    }

    /** The owner ends it: the microphone stops, her voice too, the stream closes. */
    fun stop() {
        silenced = true
        socket?.send("""{"type":"stop"}""")
        finish(VoicePhase.Ended)
        speaker.flush()
        socket?.close(1000, null)
    }

    /** The sheet is gone: nothing of the conversation keeps running. The microphone is stopped again whatever state it was left in. */
    fun release() {
        stop()
        mic.stop()
        speaker.release()
    }

    private fun finish(phase: VoicePhase, error: ApiException? = null, micFailed: Boolean = false, reason: String? = null) {
        if (over) return
        over = true
        mic.stop()
        if (phase == VoicePhase.Failed) {
            silenced = true
            speaker.flush()
        }
        _state.update { it.copy(phase = phase, error = error, micFailed = micFailed, reason = reason) }
    }

    private val listener = object : WebSocketListener() {
        override fun onMessage(webSocket: WebSocket, text: String) {
            val event = runCatching { parseJson(text).obj() }.getOrNull() ?: return
            when (event.str("type")) {
                "ready" -> if (!over) {
                    val started = mic.start { piece -> webSocket.send(piece.toByteString()) }
                    when {
                        !started -> {
                            finish(VoicePhase.Failed, micFailed = true)
                            webSocket.close(1000, null)
                        }
                        // the owner ended it while the microphone was starting (that takes a moment): it must not stay on
                        over -> mic.stop()
                    }
                }
                "wake" -> _state.update { if (it.phase == VoicePhase.Connecting) it.copy(phase = VoicePhase.Listening) else it }
                "heard" -> _state.update { it.copy(heard = event.str("text")) }
                "said" -> _state.update { it.copy(said = event.str("text")) }
                "thinking" -> _state.update { if (over) it else it.copy(phase = if (event.bool("on")) VoicePhase.Thinking else VoicePhase.Listening) }
                "flush" -> speaker.flush()
                "end" -> finish(VoicePhase.Ended, reason = event.strOrNull("reason"))   // her last words still play: the speaker isn't cut off
            }
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            if (!silenced) speaker.play(bytes.toByteArray())
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(1000, null)
            finish(VoicePhase.Ended)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            finish(VoicePhase.Failed, refusal(response))
        }
    }

    companion object {
        fun voiceClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.SECONDS)        // a conversation may be quiet for a while: the server's pings keep it alive
            .pingInterval(20, TimeUnit.SECONDS)
            .retryOnConnectionFailure(false)
            .build()

        /** Why the server didn't take the stream, in the same words as the app's API (the reason comes from the server). */
        fun refusal(response: Response?): ApiException {
            val code = response?.code ?: return ApiException(ApiError.NoServer, "Mac не отвечает")
            val text = runCatching { response.body.string().trim() }.getOrDefault("")
            return when {
                code == 403 && "подбор" in text -> ApiException(ApiError.Locked, text, code)
                code == 403 -> ApiException(ApiError.BadToken, text.ifBlank { "Пароль не подошёл" }, code)
                else -> ApiException(ApiError.Server, text.ifBlank { "HTTP $code" }, code)
            }
        }
    }
}

/**
 * The microphone through Android's voice path: with its echo canceller and noise suppressor. Start and stop come from different
 * threads (the stream's and the screen's), so they take turns: a stop always finds what a start made.
 */
class AndroidMic : Mic {
    @Volatile private var running = false
    private var record: AudioRecord? = null
    private var reader: Thread? = null

    @Synchronized
    @SuppressLint("MissingPermission")   // the screen asks for the microphone before a conversation starts
    override fun start(onPiece: (ByteArray) -> Unit): Boolean {
        val least = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (least <= 0) return false
        val r = runCatching {
            AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(least, PIECE * 4))
        }.getOrNull() ?: return false
        if (r.state != AudioRecord.STATE_INITIALIZED) {
            r.release()
            return false
        }
        runCatching { r.startRecording() }.onFailure { r.release(); return false }
        record = r
        running = true
        reader = Thread({
            val piece = ByteArray(PIECE)
            while (running) {
                val n = r.read(piece, 0, piece.size)
                if (n > 0) onPiece(piece.copyOf(n)) else if (n < 0) break
            }
        }, "sena-mic").also { it.start() }
        return true
    }

    @Synchronized
    override fun stop() {
        running = false
        record?.let { runCatching { it.stop() } }
        reader?.join(500)
        record?.release()
        record = null
        reader = null
    }

    companion object {
        const val RATE = 16000
        const val PIECE = RATE / 10 * 2   // 100 ms of 16-bit sound
    }
}

/**
 * Her voice through the speaker as an assistant's speech; pieces queue up and play in order on their own thread. While it lives
 * it holds the audio focus, so music pauses under the conversation. Building it throws if the phone has no such output now:
 * the caller turns that into a failed conversation, not a crash.
 */
class AndroidSpeaker(context: Context) : Speaker {
    private val queue = LinkedBlockingQueue<ByteArray>()
    @Volatile private var alive = true
    private val attributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()
    private val track: AudioTrack = AudioTrack.Builder()
        .setAudioAttributes(attributes)
        .setAudioFormat(AudioFormat.Builder().setSampleRate(RATE).setEncoding(AudioFormat.ENCODING_PCM_16BIT).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
        .setTransferMode(AudioTrack.MODE_STREAM)
        .setBufferSizeInBytes(maxOf(AudioTrack.getMinBufferSize(RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT), RATE))   // half a second
        .build()
    private val audio = context.getSystemService(AudioManager::class.java)
    private val focus = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT).setAudioAttributes(attributes).build()
    private val writer = Thread({
        // a failure of the speaker ends its own thread quietly: an exception here would take the whole app down
        runCatching {
            track.play()
            while (alive) {
                val piece = queue.poll(200, TimeUnit.MILLISECONDS) ?: continue
                track.write(piece, 0, piece.size)
            }
        }
    }, "sena-voice")

    init {
        runCatching { audio?.requestAudioFocus(focus) }
        writer.start()
    }

    override fun play(pcm: ByteArray) {
        if (alive) queue.offer(pcm)
    }

    override fun flush() {
        queue.clear()
        runCatching { track.pause(); track.flush(); track.play() }
    }

    /** Doesn't hold the screen: the writer finishes and the track is let go on a thread of their own. */
    override fun release() {
        if (!alive) return
        alive = false
        queue.clear()
        runCatching { audio?.abandonAudioFocusRequest(focus) }
        Thread({
            writer.join(500)
            runCatching { track.stop() }
            track.release()
        }, "sena-voice-end").start()
    }

    companion object {
        const val RATE = 24000
    }
}
