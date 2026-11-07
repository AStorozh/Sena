package com.tema.ia.ui

import com.tema.ia.MainDispatcherRule
import com.tema.ia.R
import com.tema.ia.viewModelOf
import com.tema.ia.data.ActionResult
import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import com.tema.ia.data.AskStatus
import com.tema.ia.data.ChatMessage
import com.tema.ia.data.DemoApi
import com.tema.ia.data.ScriptedApi
import com.tema.ia.data.TaskItem
import com.tema.ia.data.TasksSnapshot
import com.tema.ia.ui.screens.chat.ChatViewModel
import com.tema.ia.watch
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ChatViewModelTest {
    @get:Rule val main = MainDispatcherRule()

    private fun status(vararg steps: String, answer: String? = null, done: Boolean = false, failed: Boolean = false) = AskStatus(steps.toList(), answer, done, failed)

    private fun kotlinx.coroutines.test.TestScope.chat(api: ScriptedApi) = viewModelOf { ChatViewModel(api, pollMs = 100) }.also { watch(it.ui); it.start() }

    @Test fun `history is shown without asking for the whole overview`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = chat(api)
        assertEquals(4, vm.ui.value.bubbles.size)
        assertEquals("the chat polls only its history", 0, api.count("overview"))
    }

    @Test fun `an old message of mine never comes back at the bottom after a few exchanges`() = runTest {
        // the real server records the owner's message and Sena's answer together when the task ends
        val server = mutableListOf<ChatMessage>()
        var clock = 1_792_490_400.0
        var asked = 0
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        api.answers["ask"] = { "t${asked++}" }
        api.answers["askStatus"] = {
            val text = "m${asked - 1}"
            server += ChatMessage("u$asked", true, text, clock++)
            server += ChatMessage("a$asked", false, "ответ на $text", clock++)
            status("Готово", answer = "ответ на $text", done = true)
        }
        api.answers["history"] = { server.toList() }
        val vm = chat(api)
        repeat(7) { i ->
            vm.send("m$i")
            advanceTimeBy(150); runCurrent()
        }
        val texts = vm.ui.value.bubbles.map { it.text }
        assertEquals((0 until 7).flatMap { listOf("m$it", "ответ на m$it") }, texts)
    }

    @Test fun `the same words sent again show a bubble of their own`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        api.answers["history"] = { listOf(ChatMessage("1", true, "да", 1_792_490_400.0), ChatMessage("2", false, "Отправить?", 1_792_490_401.0)) }
        api.answers["ask"] = { "t1" }
        api.answers["askStatus"] = { status("Принято") }
        val vm = chat(api)
        vm.send("да")
        assertEquals(listOf("да", "Отправить?", "да"), vm.ui.value.bubbles.map { it.text })   // the old "да" doesn't hide the new one
    }

    @Test fun `sending shows my bubble and the current step, then the answer`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        var polls = 0
        api.answers["ask"] = { "t1" }
        api.answers["askStatus"] = {
            polls++
            when (polls) { 1 -> status("Принято"); 2 -> status("Принято", "Открываю Chrome"); else -> status("Принято", answer = "Готово", done = true) }
        }
        // like the real server: the owner's message enters the history together with the answer
        api.answers["history"] = { if (polls >= 3) listOf(ChatMessage("1", true, "Привет", 1_792_490_400.0), ChatMessage("2", false, "Готово", 1_792_490_405.0)) else emptyList() }
        val vm = chat(api)
        vm.send("  Привет  ")
        assertTrue(vm.ui.value.working)
        assertEquals("Привет", vm.ui.value.bubbles.last().text)
        advanceTimeBy(101); runCurrent()
        assertEquals("Принято", vm.ui.value.step)
        advanceTimeBy(100); runCurrent()
        assertEquals("Открываю Chrome", vm.ui.value.step)
        advanceTimeBy(100); runCurrent()
        assertFalse(vm.ui.value.working)
        assertNull(vm.ui.value.step)
        assertEquals(listOf("Привет", "Готово"), vm.ui.value.bubbles.map { it.text })   // own bubble isn't duplicated
    }

    @Test fun `blank text and a second send while working are ignored`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        api.answers["ask"] = { "t1" }
        api.answers["askStatus"] = { status("Принято") }
        val vm = chat(api)
        vm.send("   ")
        assertEquals(0, api.count("ask"))
        vm.send("Первое"); vm.send("Второе")
        assertEquals(1, api.count("ask"))
    }

    @Test fun `a failed task becomes an error bubble with retry, and retry resends`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        api.answers["ask"] = { "t1" }
        api.answers["askStatus"] = { status("Принято", answer = "Что-то сломалось, пока отвечала", done = true, failed = true) }
        val vm = chat(api)
        vm.send("Сделай")
        advanceTimeBy(101); runCurrent()
        val error = vm.ui.value.bubbles.last()
        assertNotNull(error.error)
        assertEquals("Сделай", error.retryText)
        assertEquals(UiText.Raw("Что-то сломалось, пока отвечала"), error.error)
        vm.retry(error)
        assertEquals(2, api.count("ask"))
        assertEquals(1, vm.ui.value.bubbles.count { it.text == "Сделай" })   // old error bubble is removed
    }

    @Test fun `ask refused by the server`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0)).also { it.failures["ask"] = ApiException(ApiError.Server, "Сена выключена") }
        val vm = chat(api)
        vm.send("Привет")
        assertFalse(vm.ui.value.working)
        assertEquals(UiText.Raw("Сена выключена"), vm.ui.value.bubbles.last().error)
    }

    @Test fun `offline while sending gives a readable error`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0)).also { it.failures["ask"] = ApiException(ApiError.NoServer, "x") }
        val vm = chat(api)
        vm.send("Привет")
        assertTrue(vm.ui.value.bubbles.last().error is UiText.Res)
    }

    @Test fun `status polling survives a few lost requests`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        var n = 0
        api.answers["ask"] = { "t1" }
        api.answers["askStatus"] = { if (++n <= 3) throw ApiException(ApiError.NoServer, "x") else status("ok", answer = "Готово", done = true) }
        val vm = chat(api)
        vm.send("Привет")
        advanceTimeBy(150); runCurrent()
        assertTrue("the request is still waited for", vm.ui.value.working)
        assertTrue("and the screen says the connection is lost", vm.ui.value.offline)
        advanceTimeBy(20_000); runCurrent()   // it looks again, slower and slower
        assertFalse(vm.ui.value.working)
        assertFalse(vm.ui.value.offline)
        assertNull(vm.ui.value.bubbles.last().error)
    }

    @Test fun `a long lost connection keeps the request on, with no error and no retry that would run it twice`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        var back = false
        api.answers["ask"] = { "t1" }
        api.answers["askStatus"] = { if (!back) throw ApiException(ApiError.NoServer, "x") else status("ok", answer = "Нашла", done = true) }
        val vm = chat(api)
        vm.send("Найди билеты")
        advanceTimeBy(5 * 60_000); runCurrent()   // five minutes without the Mac: the task goes on there
        assertTrue(vm.ui.value.working)
        assertTrue(vm.ui.value.offline)
        assertTrue(vm.ui.value.bubbles.none { it.error != null })
        assertEquals(1, api.count("ask"))
        back = true
        advanceTimeBy(10_001); runCurrent()
        assertFalse(vm.ui.value.working)
        assertTrue(vm.ui.value.bubbles.none { it.error != null })
    }

    @Test fun `a task the server doesn't know ends the wait with a retry`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        api.answers["ask"] = { "t1" }
        api.answers["askStatus"] = { throw ApiException(ApiError.Server, "нет задачи") }
        val vm = chat(api)
        vm.send("Привет")
        advanceTimeBy(150); runCurrent()
        assertFalse(vm.ui.value.working)
        val error = vm.ui.value.bubbles.last()
        assertEquals(UiText.Raw("нет задачи"), error.error)
        assertEquals("Привет", error.retryText)
    }

    @Test fun `a message lost on its way says so without retry and goes once its answer is in the history`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val server = mutableListOf<ChatMessage>()
        api.answers["ask"] = { throw ApiException(ApiError.NoServer, "timeout", timedOut = true) }   // it left, no answer came
        api.answers["history"] = { server.toList() }
        val vm = chat(api)
        vm.send("Напомни про отчёт")
        val lost = vm.ui.value.bubbles.last()
        assertEquals(UiText.Res(R.string.chat_lost_body), lost.error)
        assertNull("no retry: the Mac may already be at it", lost.retryText)
        server += ChatMessage("u1", true, "Напомни про отчёт", 1_792_490_400.0)   // it got through after all
        server += ChatMessage("a1", false, "Записала", 1_792_490_405.0)
        vm.refresh(); runCurrent()
        assertEquals(listOf("Напомни про отчёт", "Записала"), vm.ui.value.bubbles.map { it.text })
    }

    @Test fun `behind the closed lock the request isn't polled, and it goes on when the lock opens`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val open = kotlinx.coroutines.flow.MutableStateFlow(true)
        api.answers["ask"] = { "t1" }
        api.answers["askStatus"] = { status("Принято") }
        val vm = viewModelOf { ChatViewModel(api, pollMs = 100, open = open) }.also { watch(it.ui); it.start() }
        vm.send("Найди билеты")
        advanceTimeBy(250); runCurrent()
        val seen = api.count("askStatus")
        assertTrue(seen > 0)
        open.value = false
        advanceTimeBy(5_000); runCurrent()
        assertTrue("one look at most after the lock closed", api.count("askStatus") <= seen + 1)
        val paused = api.count("askStatus")
        open.value = true
        advanceTimeBy(250); runCurrent()
        assertTrue(api.count("askStatus") > paused)
        assertTrue(vm.ui.value.working)   // the answer is still waited for
    }

    @Test fun `stop goes to the task the server names, by its entry`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        api.answers["ask"] = { "t1" }
        api.answers["askStatus"] = { status("Принято").copy(tid = "ab12cd34", thread = "6170939392") }
        api.answers["stopTask"] = { ActionResult("Задача остановлена") }
        val vm = chat(api)
        val seen = mutableListOf<UiText>()
        watch(vm.messages) { seen += it }
        vm.send("Найди билеты")
        advanceTimeBy(101); runCurrent()
        vm.stopTask()
        runCurrent()
        assertEquals(1, api.count("stopTask"))
        assertEquals(0, api.count("tasks"))   // no guessing among the running tasks
        assertEquals(0, api.count("stopAll"))
        assertTrue(seen.contains(UiText.Raw("Задача остановлена")))
    }

    @Test fun `a server that stops only by thread gets the thread after refusing the entry`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val stopped = mutableListOf<String>()
        api.answers["ask"] = { "t1" }
        api.answers["askStatus"] = { status("Принято").copy(tid = "ab12cd34", thread = "6170939392") }
        val vm = viewModelOf { ChatViewModel(object : com.tema.ia.data.SenaApi by api {
            override suspend fun stopTask(id: String): ActionResult {
                stopped += id
                if (id == "ab12cd34") throw ApiException(ApiError.Server, "Поле «id» должно быть номером задачи", code = 400)
                return ActionResult("Задача остановлена")
            }
        }, pollMs = 100) }.also { watch(it.ui); it.start() }
        vm.send("Найди билеты")
        advanceTimeBy(101); runCurrent()
        vm.stopTask()
        runCurrent()
        assertEquals(listOf("ab12cd34", "6170939392"), stopped)
    }

    @Test fun `without the server's names, stop finds the one running task with exactly these words`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        api.answers["askStatus"] = { status("Принято") }
        val vm = chat(api)
        vm.send("Найди билеты")        // the demo server starts a running task with this text
        vm.stopTask()
        runCurrent()
        assertEquals(1, api.count("stopTask"))
        assertTrue(api.tasks().active.none { it.text == "Найди билеты" })
        assertEquals(0, api.count("stopAll"))
    }

    @Test fun `stop never stops another task or all of them`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val other = TaskItem("x1", "По расписанию: сводка новостей", 1.0, null, emptyList(), null, emptyList(), false, 0.0, null, running = true, thread = "77")
        api.answers["ask"] = { "t1" }
        api.answers["askStatus"] = { status("Принято") }
        api.answers["tasks"] = { TasksSnapshot(100.0, listOf(other), emptyList()) }
        val vm = chat(api)
        val seen = mutableListOf<UiText>()
        watch(vm.messages) { seen += it }
        vm.send("Найди билеты")
        vm.stopTask()
        runCurrent()
        assertEquals(0, api.count("stopTask"))
        assertEquals(0, api.count("stopAll"))
        assertEquals(listOf<UiText>(UiText.Res(R.string.chat_stop_not_found)), seen)
    }

    @Test fun `two running tasks with the same words are not guessed between`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        fun twin(id: String) = TaskItem(id, "да", 99.0, null, emptyList(), null, emptyList(), false, 0.0, null, running = true, thread = id)
        api.answers["ask"] = { "t1" }
        api.answers["askStatus"] = { status("Принято") }
        api.answers["tasks"] = { TasksSnapshot(100.0, listOf(twin("1"), twin("2")), emptyList()) }
        val vm = chat(api)
        vm.send("да")
        vm.stopTask()
        runCurrent()
        assertEquals(0, api.count("stopTask"))
    }

    @Test fun `clear asks first`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = chat(api)
        vm.askClear()
        assertTrue(vm.ui.value.confirmClear)
        vm.dismissClear()
        assertEquals(0, api.count("clearHistory"))
        vm.askClear(); vm.clear()
        runCurrent()
        assertEquals(1, api.count("clearHistory"))
        assertTrue(vm.ui.value.bubbles.isEmpty())
    }
}
