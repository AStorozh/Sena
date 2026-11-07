package com.tema.ia.ui

import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.tema.ia.AppTest
import com.tema.ia.R
import com.tema.ia.data.ActionResult
import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import com.tema.ia.data.DemoApi
import com.tema.ia.data.Overview
import com.tema.ia.data.ScriptedApi
import com.tema.ia.data.TaskItem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class HomeChatTest : AppTest() {
    private fun api(coreAlive: Boolean = true) = ScriptedApi(DemoApi(coreAlive = coreAlive, latencyMs = 0))

    // ---------- Home ----------

    @Test fun home_is_almost_empty_leaf_state_and_power() {
        launch(api())
        waitTag("home_state")
        tag("home_state").assertTextEquals(str(R.string.home_state_on))
        tag("home_leaf").assertExists()
        tag("home_power").assertIsEnabled()
        assertFalse("На главной нет списков", exists("tasks_list") || exists("chat_list"))
    }

    @Test fun power_off_state_and_turning_on() {
        val api = api(coreAlive = false)
        api.answers["coreStart"] = { ActionResult("Запускаю", handover = true) }
        launch(api)
        waitTag("home_state")
        tag("home_state").assertTextEquals(str(R.string.home_state_off))
        tag("home_power").performClick()
        waitFor(message = "кнопка питания не вызвала запуск") { api.count("coreStart") == 1 }
        // Until the server confirms, the screen says "Starting…" instead of guessing
        waitFor(message = "нет слова «Запускаю»") { runCatching { tag("home_state").assertTextEquals(str(R.string.home_state_starting)) }.isSuccess }
    }

    @Test fun power_on_asks_before_turning_off() {
        val api = api()
        launch(api)
        waitTag("home_power")
        tag("home_power").performClick()
        text(str(R.string.confirm_off_title)).assertExists()
        assertEquals(0, api.count("coreStop"))
        button(str(R.string.action_cancel)).performClick()
        rule.waitForIdle()
        assertEquals(0, api.count("coreStop"))
        tag("home_power").performClick()
        button(str(R.string.confirm_off_action)).performClick()
        waitFor { api.count("coreStop") == 1 }
    }

    @Test fun no_connection_shows_a_retry_and_recovers() {
        val api = api().also { it.failures["overview"] = ApiException(ApiError.NoServer, "x") }
        launch(api)
        waitText(str(R.string.action_retry))
        text(str(R.string.err_no_server)).assertExists()
        api.failures.remove("overview")
        text(str(R.string.action_retry)).performClick()
        waitTag("home_state")
        tag("home_state").assertTextEquals(str(R.string.home_state_on))
    }

    @Test fun question_card_answers_sena() {
        val demo = DemoApi(latencyMs = 0)
        val api = ScriptedApi(demo)
        var answered = false
        api.answers["overview"] = { demo.overview().let { if (answered) it else it.copy(question = com.tema.ia.data.Question("q1", "Какой код из СМС?", false)) } }
        api.answers["answer"] = { answered = true; demo.answer("1234") }
        launch(api)
        waitTag("home_question")
        text("Какой код из СМС?").assertExists()
        button(str(R.string.home_question_send)).assertIsNotEnabled()
        tag("home_question").performClick()
        rule.onNode(androidx.compose.ui.test.hasSetTextAction() and androidx.compose.ui.test.hasAnyAncestor(androidx.compose.ui.test.hasTestTag("home_question")), useUnmergedTree = true).performTextInput("1234")
        button(str(R.string.home_question_send)).performClick()
        waitFor { api.count("answer") == 1 }
        waitFor(message = "карточка вопроса не ушла") { !exists("home_question") }
    }

    @Test fun pending_send_can_be_confirmed() {
        val api = api()
        launch(api)
        waitTag("home_send")
        text("Ольга").assertExists()   // what waits for the "yes" is named as the server names it
        button(str(R.string.home_send_confirm)).performClick()
        waitFor { api.count("confirmSend") == 1 }
        waitFor(message = "карточка не ушла") { !exists("home_send") }
    }

    @Test fun stop_all_appears_with_running_tasks_and_asks_first() {
        val api = api()
        val running = TaskItem("9", "Работаю", 1.0, null, emptyList(), null, emptyList(), false, 0.0, null, true)
        api.answers["overview"] = { Overview(true, null, emptyList(), listOf(running), 0.0, 0.5, 2, 3, emptyMap()) }
        launch(api)
        waitText(str(R.string.home_active, 1))
        button(str(R.string.home_stop_all)).performClick()
        text(str(R.string.confirm_stop_all_title)).assertExists()
        button(str(R.string.confirm_stop_all_action)).performClick()
        waitFor { api.count("stopAll") == 1 }
    }

    // ---------- Chat ----------

    @Test fun chat_shows_history_and_sends_a_message() {
        launch(api())
        openTab("chat")
        waitTag("chat_list")
        text("Привет! Что у меня сегодня?").assertExists()
        tag("chat_send").assertIsNotEnabled()
        tag("chat_input").performTextInput("Что нового?")
        tag("chat_send").assertIsEnabled()
        tag("chat_send").performClick()
        waitText("Что нового?")
        // the demo server answers after a couple of seconds
        waitFor(15_000, "нет ответа Сены") { rule.onAllNodes(androidx.compose.ui.test.hasText("Готово.", substring = true), useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty() }
        assertFalse(exists("chat_stop"))
        tag("chat_input").assertTextEquals("")
    }

    @Test fun chat_shows_the_current_step_and_a_stop_button_while_working() {
        val api = api()
        api.answers["askStatus"] = { com.tema.ia.data.AskStatus(listOf("Открываю Chrome"), null, done = false, failed = false) }
        launch(api)
        openTab("chat")
        waitTag("chat_input")
        tag("chat_input").performTextInput("Найди билеты")
        tag("chat_send").performClick()
        waitTag("chat_step")
        waitText("Открываю Chrome")
        tag("chat_stop").assertExists()
        tag("chat_stop").performClick()
        waitFor(message = "стоп не дошёл до сервера") { api.count("stopTask") + api.count("stopAll") >= 1 }
    }

    @Test fun chat_error_bubble_has_retry() {
        val api = api()
        api.failOnce["ask"] = ApiException(ApiError.Server, "Сена выключена: ядро не запущено")
        launch(api)
        openTab("chat")
        waitTag("chat_input")
        tag("chat_input").performTextInput("Привет")
        tag("chat_send").performClick()
        waitText(str(R.string.chat_error_retry))
        text("Сена выключена: ядро не запущено").assertExists()
        button(str(R.string.chat_error_retry)).performClick()
        waitFor(message = "повтор не отправился") { api.count("ask") == 2 }
    }

    @Test fun chat_clear_asks_and_empties() {
        val api = api()
        launch(api)
        openTab("chat")
        waitTag("chat_list")
        rule.onNode(androidx.compose.ui.test.hasContentDescription(str(R.string.cd_more_actions)), useUnmergedTree = true).performClick()
        text(str(R.string.chat_clear)).performClick()
        text(str(R.string.chat_clear_title)).assertExists()
        button(str(R.string.action_delete)).performClick()
        waitText(str(R.string.chat_empty))
        assertEquals(1, api.count("clearHistory"))
    }

    @Test fun chat_offline_keeps_old_messages_and_shows_banner() {
        val api = api()
        launch(api)
        openTab("chat")
        waitTag("chat_list")
        api.failures["history"] = ApiException(ApiError.NoServer, "x")
        api.failures["overview"] = ApiException(ApiError.NoServer, "x")
        waitText(str(R.string.offline_banner), 12_000)
        text("Привет! Что у меня сегодня?").assertExists()
    }

    @Test fun bottom_bar_switches_and_back_goes_home() {
        launch(api())
        waitTag("home_state")
        openTab("tasks"); waitText(str(R.string.tasks_tab_done))
        openTab("memory"); waitTag("memory_search")
        openTab("more"); waitTag("more_rules")
        tag("more_rules").performClick()
        waitTag("rules_list")
        assertFalse("На втором уровне нижней панели нет", exists("nav_home"))
    }
}
