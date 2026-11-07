package com.tema.ia.ui

import com.tema.ia.MainDispatcherRule
import com.tema.ia.R
import com.tema.ia.autoStop
import com.tema.ia.watch
import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import com.tema.ia.data.DemoApi
import com.tema.ia.data.Overview
import com.tema.ia.data.Question
import com.tema.ia.data.ScriptedApi
import com.tema.ia.ui.screens.home.HomeConfirm
import com.tema.ia.ui.screens.home.HomeViewModel
import com.tema.ia.ui.screens.home.Power
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class HomeViewModelTest {
    @get:Rule val main = MainDispatcherRule()

    private fun overview(alive: Boolean) = Overview(alive, null, emptyList(), emptyList(), 0.0, 0.0, 0, 0, emptyMap())

    @Test fun `power follows the server state`() = runTest {
        val vm = HomeViewModel(ScriptedApi(DemoApi(coreAlive = false, latencyMs = 0))).also { autoStop(it.loader) }
        watch(vm.ui)
        assertEquals(Power.Unknown, vm.ui.value.power)
        vm.start()
        assertEquals(Power.Off, vm.ui.value.power)
    }

    @Test fun `no connection shows offline and keeps the last data`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = HomeViewModel(api).also { autoStop(it.loader) }
        watch(vm.ui)
        vm.start()
        assertEquals(Power.On, vm.ui.value.power)
        api.failures["overview"] = ApiException(ApiError.NoServer, "x")
        vm.refresh()
        assertEquals(Power.Offline, vm.ui.value.power)
        assertTrue(vm.ui.value.load.valueOrNull != null)
    }

    @Test fun `first load failing shows offline`() = runTest {
        val api = ScriptedApi().also { it.failures["overview"] = ApiException(ApiError.NoServer, "x") }
        val vm = HomeViewModel(api).also { autoStop(it.loader) }
        watch(vm.ui)
        vm.start()
        assertEquals(Power.Offline, vm.ui.value.power)
    }

    @Test fun `turning on enters Starting until the server confirms`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        // first answer is "core off", then the connection drops (the port hands over to the core), then the core comes up
        var step = 0
        api.answers["overview"] = { when (step) { 0 -> overview(false); 1 -> throw ApiException(ApiError.NoServer, "x"); else -> overview(true) } }
        api.answers["coreStart"] = { com.tema.ia.data.ActionResult("Запускаю", handover = true) }
        val vm = HomeViewModel(api).also { autoStop(it.loader) }
        watch(vm.ui)
        vm.start()
        assertEquals(Power.Off, vm.ui.value.power)
        step = 1
        vm.onPower()
        assertEquals(Power.Starting, vm.ui.value.power)      // no connection, but the screen doesn't scare the user with "no connection"
        advanceTimeBy(2_100); runCurrent()
        assertEquals(Power.Starting, vm.ui.value.power)
        step = 2
        advanceTimeBy(2_100); runCurrent()
        assertEquals(Power.On, vm.ui.value.power)
        assertEquals(1, api.count("coreStart"))
    }

    @Test fun `start that never completes gives a message and offline`() = runTest {
        val api = ScriptedApi(DemoApi(coreAlive = false, latencyMs = 0))
        var step = 0
        api.answers["overview"] = { if (step == 0) overview(false) else throw ApiException(ApiError.NoServer, "x") }
        api.answers["coreStart"] = { com.tema.ia.data.ActionResult("Запускаю", handover = true) }
        val vm = HomeViewModel(api, startTimeoutMs = 10_000).also { autoStop(it.loader) }
        val seen = mutableListOf<UiText>()
        watch(vm.messages) { seen += it }
        watch(vm.ui)
        vm.start(); step = 1
        vm.onPower()
        advanceTimeBy(10_500); runCurrent()
        assertTrue(seen.contains(UiText.Res(R.string.home_wait_timeout)))
        assertEquals(Power.Offline, vm.ui.value.power)
    }

    @Test fun `turning off asks first and then stops`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        api.answers["coreStop"] = { com.tema.ia.data.ActionResult("Останавливаю", handover = true) }
        val vm = HomeViewModel(api).also { autoStop(it.loader) }
        watch(vm.ui)
        vm.start()
        vm.onPower()
        assertEquals(HomeConfirm.TurnOff, vm.ui.value.confirm)
        assertEquals(0, api.count("coreStop"))
        vm.dismissConfirm()
        assertEquals(null, vm.ui.value.confirm)
        vm.onPower(); vm.confirmed()
        assertEquals(1, api.count("coreStop"))
        assertEquals(Power.Stopping, vm.ui.value.power)
    }

    @Test fun `stop all needs confirmation`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = HomeViewModel(api).also { autoStop(it.loader) }
        watch(vm.ui)
        vm.start()
        vm.onStopAll()
        assertEquals(HomeConfirm.StopAll, vm.ui.value.confirm)
        vm.confirmed()
        assertEquals(1, api.count("stopAll"))
    }

    @Test fun `an action error becomes a message and frees the buttons`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        api.failures["stopAll"] = ApiException(ApiError.Server, "Нечего останавливать")
        val vm = HomeViewModel(api).also { autoStop(it.loader) }
        val seen = mutableListOf<UiText>()
        watch(vm.messages) { seen += it }
        watch(vm.ui)
        vm.start(); vm.onStopAll(); vm.confirmed()
        runCurrent()   // the message is delivered to the subscriber on the test dispatcher
        assertEquals("calls=${api.calls}", listOf<UiText>(UiText.Raw("Нечего останавливать")), seen)
        assertFalse(vm.ui.value.busy)
    }

    @Test fun `answer ignores blank text and trims real text`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        var sent: String? = null
        api.answers["answer"] = { com.tema.ia.data.ActionResult("ok") }
        val vm = HomeViewModel(api).also { autoStop(it.loader) }
        vm.answer("   ")
        assertEquals(0, api.count("answer"))
        vm.answer(" 1234 ")
        assertEquals(1, api.count("answer"))
    }

    @Test fun `the answer goes to the question on screen`() = runTest {
        val asked = mutableListOf<Pair<String, String?>>()
        val api = object : com.tema.ia.data.SenaApi by ScriptedApi(DemoApi(latencyMs = 0)) {
            override suspend fun answer(text: String, id: String?): com.tema.ia.data.ActionResult {
                asked += text to id
                return com.tema.ia.data.ActionResult("Ответ передан Сене")
            }
        }
        val vm = HomeViewModel(api).also { autoStop(it.loader) }
        vm.answer(" 1234 ", "q7")
        runCurrent()
        assertEquals(listOf("1234" to "q7"), asked)   // two agents asking: the words reach the one he read, not the oldest
    }

    @Test fun `pending send can be confirmed and cancelled`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = HomeViewModel(api).also { autoStop(it.loader) }
        val seen = mutableListOf<UiText>()
        watch(vm.messages) { seen += it }
        watch(vm.ui)
        vm.start()
        assertEquals(1, vm.ui.value.load.valueOrNull?.pendingSends?.size)
        vm.confirmSend("s1")
        runCurrent()
        assertEquals(1, api.count("confirmSend"))
        assertEquals(0, vm.ui.value.load.valueOrNull?.pendingSends?.size)
        assertEquals("the server's own answer is said", listOf<UiText>(UiText.Raw("Отправлено")), seen)
    }

    @Test fun `a long confirmed action that outlasts the wait is still at work, not a lost Mac`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        api.failOnce["confirmSend"] = ApiException(ApiError.NoServer, "timeout", timedOut = true)
        val vm = HomeViewModel(api).also { autoStop(it.loader) }
        val seen = mutableListOf<UiText>()
        watch(vm.messages) { seen += it }
        watch(vm.ui)
        vm.start()
        vm.confirmSend("s1")
        runCurrent()
        assertEquals(listOf<UiText>(UiText.Res(R.string.home_send_running)), seen)
        assertFalse(vm.ui.value.busy)
    }

    @Test fun `the question's picture is fetched once for that question`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        api.answers["overview"] = { overview(true).copy(question = Question("q1", "Какая из картинок с котом?", hasImage = true)) }
        api.answers["questionImage"] = { byteArrayOf(1, 2, 3) }
        val vm = HomeViewModel(api).also { autoStop(it.loader) }
        watch(vm.ui)
        vm.start(); runCurrent()
        assertEquals("q1", vm.image.value?.first)
        assertEquals(3, vm.image.value?.second?.size)
        vm.refresh(); runCurrent()
        assertEquals(1, api.count("questionImage"))
        api.answers["overview"] = { overview(true) }   // answered: the picture goes with the question
        vm.refresh(); runCurrent()
        assertEquals(null, vm.image.value)
    }
}
