package com.tema.ia.ui

import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.tema.ia.MainDispatcherRule
import com.tema.ia.data.DemoApi
import com.tema.ia.data.LiveVoiceTest
import com.tema.ia.data.SenaApi
import com.tema.ia.data.VoicePhase
import com.tema.ia.ui.screens.chat.VoiceViewModel
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import kotlinx.coroutines.flow.MutableStateFlow
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/** The chat's talk button: offered only where there is a live voice; the sheet follows the conversation and closing it frees everything. */
class VoiceViewModelTest {
    @get:Rule val main = MainDispatcherRule()
    private lateinit var server: MockWebServer
    @Volatile private var sena: WebSocket? = null

    @Before fun setUp() { server = MockWebServer().also { it.start() } }
    @After fun tearDown() {
        sena?.close(1000, null)   // the fake server's end of the stream: shut without waiting for it
        server.shutdown()
    }

    private fun until(check: () -> Boolean) {
        val deadline = System.currentTimeMillis() + 3000
        while (!check()) {
            if (System.currentTimeMillis() > deadline) fail("не дождались")
            Thread.sleep(10)
        }
    }

    @Test fun `no live voice in the demo`() {
        val vm = VoiceViewModel(DemoApi(latencyMs = 0)) { LiveVoiceTest.FakeMic() to LiveVoiceTest.FakeSpeaker() }
        assertFalse(vm.available)
        vm.start()
        assertNull(vm.state.value)
    }

    @Test fun `a speaker the phone can't open fails the conversation in words, not the app`() {
        val api = object : SenaApi by DemoApi(latencyMs = 0) { override fun voiceUrl() = "ws://127.0.0.1:9/ear" }
        val vm = VoiceViewModel(api) { throw UnsupportedOperationException("no audio output now") }
        vm.start()
        assertEquals(VoicePhase.Failed, vm.state.value?.phase)
        assertTrue(vm.state.value?.speakerFailed == true)
    }

    private fun ready() = server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) { sena = webSocket; webSocket.send("""{"type":"ready"}""") }
        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(1000, null) }
    }))

    @Test fun `when the lock closes the conversation ends, though the screens stay under it`() {
        ready()
        val url = server.url("/ear").toString().replaceFirst("http://", "ws://") + "?mode=talk"
        val api = object : SenaApi by DemoApi(latencyMs = 0) { override fun voiceUrl() = url }
        val mic = LiveVoiceTest.FakeMic()
        val speaker = LiveVoiceTest.FakeSpeaker()
        val open = MutableStateFlow(true)   // AppContainer.unlocked: ON_STOP closes the lock
        val vm = VoiceViewModel(api, open) { mic to speaker }
        vm.start()
        until { mic.feed != null }   // the microphone is on
        open.value = false
        assertTrue("the microphone stops with the lock", mic.stopped)
        assertTrue(speaker.released)
        assertNull("no sheet is left behind the lock", vm.state.value)
    }

    @Test fun `a cleared store (another connection) ends the conversation too`() {
        ready()
        val url = server.url("/ear").toString().replaceFirst("http://", "ws://") + "?mode=talk"
        val api = object : SenaApi by DemoApi(latencyMs = 0) { override fun voiceUrl() = url }
        val mic = LiveVoiceTest.FakeMic()
        val speaker = LiveVoiceTest.FakeSpeaker()
        val screens = ScreensStore()   // what AppShell gives the screens; a new connection clears it (ScreensScope)
        val vm = ViewModelProvider.create(screens, viewModelFactory { initializer { VoiceViewModel(api) { mic to speaker } } })[VoiceViewModel::class]
        vm.start()
        until { mic.feed != null }
        screens.viewModelStore.clear()
        assertTrue(mic.stopped)
        assertTrue(speaker.released)
    }

    @Test fun `nothing at the voice's port while the mac answers means Sena is off, and the sheet says so`() {
        val port = server.port
        server.shutdown()   // nothing listens for the voice: the screen alone, without Sena, doesn't
        val api = object : SenaApi by DemoApi(coreAlive = false, latencyMs = 0) { override fun voiceUrl() = "ws://127.0.0.1:$port/ear?mode=talk" }
        val vm = VoiceViewModel(api) { LiveVoiceTest.FakeMic() to LiveVoiceTest.FakeSpeaker() }
        vm.start()
        until { vm.state.value?.senaOff == true }
        assertEquals(VoicePhase.Failed, vm.state.value?.phase)
        server = MockWebServer().also { it.start() }   // tearDown shuts one down
    }

    @Test fun `the stream asks with the api's own headers`() {
        ready()
        val url = server.url("/ear").toString().replaceFirst("http://", "ws://") + "?mode=talk"
        val api = object : SenaApi by DemoApi(latencyMs = 0) {
            override fun voiceUrl() = url
            override fun voiceHeaders() = mapOf("Authorization" to "Bearer x")
        }
        val vm = VoiceViewModel(api) { LiveVoiceTest.FakeMic() to LiveVoiceTest.FakeSpeaker() }
        vm.start()
        assertEquals("Bearer x", server.takeRequest(3, java.util.concurrent.TimeUnit.SECONDS)?.getHeader("Authorization"))
        vm.close()
    }

    @Test fun `the sheet follows the conversation and closing it frees the microphone and the speaker`() {
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                sena = webSocket
                webSocket.send("""{"type":"ready"}""")
                webSocket.send("""{"type":"wake"}""")
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(1000, null) }
        }))
        val url = server.url("/ear").toString().replaceFirst("http://", "ws://") + "?t=x&mode=talk"
        val api = object : SenaApi by DemoApi(latencyMs = 0) { override fun voiceUrl() = url }
        val mic = LiveVoiceTest.FakeMic()
        val speaker = LiveVoiceTest.FakeSpeaker()
        val vm = VoiceViewModel(api) { mic to speaker }
        assertTrue(vm.available)
        vm.start()
        until { vm.state.value?.phase == VoicePhase.Listening }
        sena!!.send("""{"type":"said","text":"Слышу тебя"}""")
        until { vm.state.value?.said == "Слышу тебя" }
        vm.end()
        until { vm.state.value?.phase == VoicePhase.Ended }
        assertTrue(mic.stopped)
        assertEquals("Слышу тебя", vm.state.value?.said)   // the sheet stays with what was said
        vm.close()
        assertNull(vm.state.value)
        assertTrue(speaker.released)
    }
}
