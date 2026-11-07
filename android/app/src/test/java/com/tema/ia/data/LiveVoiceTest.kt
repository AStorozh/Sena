package com.tema.ia.data

import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** The "talk" button's live voice against a fake Sena: what the phone sends, how it follows her events, how it ends. */
class LiveVoiceTest {
    private lateinit var server: MockWebServer
    private val got = LinkedBlockingQueue<Any>()   // what the phone sent: a String (event) or a ByteString (the microphone)
    @Volatile private var sena: WebSocket? = null
    private val opened = CountDownLatch(1)

    class FakeMic(private val works: Boolean = true) : Mic {
        @Volatile var feed: ((ByteArray) -> Unit)? = null
        @Volatile var stopped = false
        override fun start(onPiece: (ByteArray) -> Unit): Boolean {
            if (works) feed = onPiece
            return works
        }
        override fun stop() { stopped = true }
    }

    class FakeSpeaker : Speaker {
        val played = mutableListOf<ByteArray>()
        @Volatile var flushed = 0
        @Volatile var released = false
        override fun play(pcm: ByteArray) { synchronized(played) { played += pcm } }
        override fun flush() { flushed++ }
        override fun release() { released = true }
    }

    private val voices = mutableListOf<LiveVoice>()

    @Before fun setUp() { server = MockWebServer().also { it.start() } }
    @After fun tearDown() {
        voices.forEach { it.release() }   // both ends closed: the fake server shuts down without waiting for an open stream
        sena?.close(1000, null)
        server.shutdown()
    }

    private fun voice(mic: Mic = FakeMic(), speaker: Speaker = FakeSpeaker(), url: String = url()) = LiveVoice(url, mic, speaker).also { voices += it }

    private fun url() = server.url("/ear").toString().replaceFirst("http://", "ws://") + "?t=secret&device=phone&mode=talk"

    private fun upgrade() = server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) { sena = webSocket; opened.countDown() }
        override fun onMessage(webSocket: WebSocket, text: String) { got.add(text) }
        override fun onMessage(webSocket: WebSocket, bytes: ByteString) { got.add(bytes) }
        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(1000, null) }
    }))

    private fun until(check: () -> Boolean) {
        val deadline = System.currentTimeMillis() + 3000
        while (!check()) {
            if (System.currentTimeMillis() > deadline) fail("не дождались")
            Thread.sleep(10)
        }
    }

    private fun say(text: String) { sena!!.send(text) }

    @Test fun `a conversation by the button from ready to end`() {
        upgrade()
        val mic = FakeMic()
        val speaker = FakeSpeaker()
        val voice = voice(mic, speaker)
        voice.start()
        assertTrue(opened.await(3, TimeUnit.SECONDS))
        val request = server.takeRequest()
        assertEquals("/ear", request.requestUrl?.encodedPath)
        assertEquals("talk", request.requestUrl?.queryParameter("mode"))
        assertTrue(request.getHeader("X-Sena-Zone")!!.matches(Regex("[+-]\\d\\d:\\d\\d")))
        assertEquals(VoicePhase.Connecting, voice.state.value.phase)
        assertNull("the microphone waits for the server's ready", mic.feed)

        say("""{"type":"ready"}""")
        until { mic.feed != null }
        mic.feed!!(byteArrayOf(1, 2, 3, 4))
        assertEquals(byteArrayOf(1, 2, 3, 4).toByteString(), got.poll(3, TimeUnit.SECONDS))

        say("""{"type":"wake"}""")
        until { voice.state.value.phase == VoicePhase.Listening }
        say("""{"type":"heard","text":"какая погода завтра"}""")
        say("""{"type":"thinking","on":true}""")
        until { voice.state.value.phase == VoicePhase.Thinking }
        sena!!.send(byteArrayOf(5, 6).toByteString())
        until { synchronized(speaker.played) { speaker.played.size == 1 } }
        say("""{"type":"thinking","on":false}""")
        say("""{"type":"said","text":"Завтра солнечно"}""")
        say("""{"type":"flush"}""")
        until { speaker.flushed == 1 }
        say("""{"type":"end","reason":"тишина"}""")
        until { voice.state.value.phase == VoicePhase.Ended }
        assertEquals("какая погода завтра", voice.state.value.heard)
        assertEquals("Завтра солнечно", voice.state.value.said)
        assertEquals("why it ended, in the server's words", "тишина", voice.state.value.reason)
        assertTrue(mic.stopped)
        voice.release()
        assertTrue(speaker.released)
    }

    /** Like a real microphone: building AudioRecord takes a moment, and a stop that comes meanwhile finds nothing to stop yet. */
    class SlowMic : Mic {
        @Volatile var running = false
        val starting = CountDownLatch(1)
        override fun start(onPiece: (ByteArray) -> Unit): Boolean {
            starting.countDown()
            Thread.sleep(300)
            running = true
            return true
        }
        override fun stop() { running = false }
    }

    @Test fun `finishing while the microphone is still starting leaves it off`() {
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) { sena = webSocket; webSocket.send("""{"type":"ready"}""") }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(1000, null) }
        }))
        val mic = SlowMic()
        val voice = voice(mic)
        voice.start()
        assertTrue(mic.starting.await(3, TimeUnit.SECONDS))
        voice.release()                // the owner presses "Finish" / closes the sheet right now
        Thread.sleep(600)              // the start finishes after the release
        assertEquals(VoicePhase.Ended, voice.state.value.phase)
        assertFalse("the microphone must not stay on after the conversation was let go", mic.running)
    }

    @Test fun `ending by hand tells the server and plays nothing more`() {
        upgrade()
        val mic = FakeMic()
        val speaker = FakeSpeaker()
        val voice = voice(mic, speaker)
        voice.start()
        assertTrue(opened.await(3, TimeUnit.SECONDS))
        say("""{"type":"ready"}""")
        until { mic.feed != null }
        voice.stop()
        assertEquals("""{"type":"stop"}""", got.poll(3, TimeUnit.SECONDS))
        assertEquals(VoicePhase.Ended, voice.state.value.phase)
        assertTrue(mic.stopped)
        runCatching { sena!!.send(byteArrayOf(7, 8).toByteString()) }
        Thread.sleep(200)
        assertTrue(synchronized(speaker.played) { speaker.played.isEmpty() })
    }

    @Test fun `a refusal comes back in the server's words`() {
        server.enqueue(MockResponse().setResponseCode(409).setBody("Сена выключена: слушать без неё не делаю."))
        val voice = voice()
        voice.start()
        until { voice.state.value.phase == VoicePhase.Failed }
        assertEquals(ApiError.Server, voice.state.value.error?.kind)
        assertEquals("Сена выключена: слушать без неё не делаю.", voice.state.value.error?.message)

        server.enqueue(MockResponse().setResponseCode(403).setBody("нет доступа"))
        val denied = voice()
        denied.start()
        until { denied.state.value.phase == VoicePhase.Failed }
        assertEquals(ApiError.BadToken, denied.state.value.error?.kind)

        server.enqueue(MockResponse().setResponseCode(403).setBody("адрес закрыт за подбор пароля"))
        val locked = voice()
        locked.start()
        until { locked.state.value.phase == VoicePhase.Failed }
        assertEquals(ApiError.Locked, locked.state.value.error?.kind)
    }

    @Test fun `no mac at the address is no server`() {
        val port = server.port
        server.shutdown()
        val voice = voice(url = "ws://127.0.0.1:$port/ear?t=x")
        voice.start()
        until { voice.state.value.phase == VoicePhase.Failed }
        assertEquals(ApiError.NoServer, voice.state.value.error?.kind)
        server = MockWebServer().also { it.start() }   // tearDown has its own to shut
    }

    @Test fun `a microphone that won't start fails the conversation in its own words`() {
        upgrade()
        val voice = voice(FakeMic(works = false))
        voice.start()
        assertTrue(opened.await(3, TimeUnit.SECONDS))
        say("""{"type":"ready"}""")
        until { voice.state.value.phase == VoicePhase.Failed }
        assertTrue(voice.state.value.micFailed)
        assertNull(voice.state.value.error)
    }

    @Test fun `the address of the live voice is the same mac on its own port and only on your own network`() {
        val api = HttpSenaApi("http://192.168.1.20:8765", "pass word")
        val url = api.voiceUrl()!!
        assertTrue(url, url.startsWith("ws://192.168.1.20:${Address.VOICE_PORT}/ear?"))
        assertTrue(url, url.contains("device=phone") && url.contains("mode=talk"))
        assertFalse("the password never goes in the stream's address", url.contains("t="))
        assertEquals(mapOf("Authorization" to "Bearer pass word"), api.voiceHeaders())
        assertNull(HttpSenaApi("https://sena.example.com", "x").voiceUrl())   // plain WebSocket never leaves the home network
        assertFalse(DemoApi(latencyMs = 0).voiceUrl() != null)
    }

    @Test fun `the stream carries the password in its header`() {
        upgrade()
        val voice = LiveVoice(url().substringBefore('?') + "?device=phone&mode=talk", FakeMic(), FakeSpeaker(), headers = mapOf("Authorization" to "Bearer secret")).also { voices += it }
        voice.start()
        assertTrue(opened.await(3, TimeUnit.SECONDS))
        val request = server.takeRequest()
        assertEquals("Bearer secret", request.getHeader("Authorization"))
        assertNull(request.requestUrl?.queryParameter("t"))
    }
}
