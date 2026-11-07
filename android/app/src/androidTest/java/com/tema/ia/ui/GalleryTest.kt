package com.tema.ia.ui

import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.tema.ia.AppTest
import com.tema.ia.R
import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import com.tema.ia.data.AskStatus
import com.tema.ia.data.DemoApi
import com.tema.ia.data.FactSection
import com.tema.ia.data.RuleEntry
import com.tema.ia.data.ScriptedApi
import com.tema.ia.data.TaskItem
import com.tema.ia.data.TasksSnapshot
import com.tema.ia.data.ThemeMode
import org.junit.Test
import org.junit.runner.RunWith
import java.util.Locale

/**
 * Screenshots of every screen for eyeballing. Run parameters: theme=dark|light, scale=1.0|2.0, locale=ru|en.
 * Files: /data/local/tmp/sena_shots/<theme>_<scale>_<number>_<name>.png
 */
@RunWith(AndroidJUnit4::class)
class GalleryTest : AppTest() {
    private val args = InstrumentationRegistry.getArguments()
    private val themeName = args.getString("theme") ?: "dark"
    private val scale = (args.getString("scale") ?: "1.0").toFloat()
    private val big = scale > 1.2f
    private val tag = "${themeName}_${if (big) "big" else "std"}"

    private fun demo(coreAlive: Boolean = true) = ScriptedApi(DemoApi(coreAlive = coreAlive, latencyMs = 0))

    private fun start(api: ScriptedApi = demo(), connected: Boolean = true) = launch(
        api, theme = if (themeName == "light") ThemeMode.Light else ThemeMode.Dark, fontScale = scale,
        locale = Locale.forLanguageTag(args.getString("locale") ?: "ru"), connected = connected,
    )

    private fun snap(n: Int, name: String) {
        rule.waitForIdle()
        Thread.sleep(700)     // let the transition animations finish
        shot("${tag}_%02d_$name".format(n))
    }

    // ---------- connection ----------
    @Test fun a01_connect() { start(connected = false); waitTag("connect_address"); snap(1, "connect") }

    @Test fun a02_connect_error() {
        val api = demo().also { it.failures["overview"] = ApiException(ApiError.NoServer, "x") }
        start(api, connected = false)
        waitTag("connect_address")
        tag("connect_address").performTextInput("192.168.1.20")
        tag("connect_token").performTextInput("secret")
        hideKeyboard()
        tag("connect_submit").performClick()
        waitTag("connect_error")
        snap(2, "connect_error")
    }

    // ---------- home ----------
    @Test fun b01_home_on() { start(demo()); waitTag("home_state"); snap(3, "home_on") }

    @Test fun b02_home_off() { start(demo(coreAlive = false)); waitTag("home_state"); snap(4, "home_off") }

    @Test fun b03_home_question() {
        val inner = DemoApi(latencyMs = 0)
        val api = ScriptedApi(inner).also { it.answers["overview"] = { inner.overview().copy(question = com.tema.ia.data.Question("q1", "Какой код пришёл в СМС от банка?", false)) } }
        start(api); waitTag("home_question"); snap(5, "home_question")
    }

    @Test fun b04_home_offline() {
        start(demo().also { it.failures["overview"] = ApiException(ApiError.NoServer, "x") })
        waitText(str(R.string.action_retry)); snap(6, "home_offline")
    }

    // ---------- chat ----------
    @Test fun c01_chat() { start(); openTab("chat"); waitTag("chat_list"); snap(7, "chat") }

    @Test fun c02_chat_working() {
        val api = demo().also { it.answers["askStatus"] = { AskStatus(listOf("Открываю страницу Авиасейлс"), null, done = false, failed = false) } }
        start(api); openTab("chat"); waitTag("chat_input")
        tag("chat_input").performTextInput("Найди билеты в Лиссабон на май")
        hideKeyboard()
        tag("chat_send").performClick(); waitTag("chat_step"); snap(8, "chat_working")
    }

    @Test fun c04_chat_error() {
        val api = demo().also { it.failOnce["ask"] = ApiException(ApiError.Server, "Сена выключена: ядро не запущено") }
        start(api); openTab("chat"); waitTag("chat_input")
        tag("chat_input").performTextInput("Привет"); hideKeyboard(); tag("chat_send").performClick()
        waitText(str(R.string.chat_error_retry)); snap(9, "chat_error")
    }

    // ---------- tasks ----------
    @Test fun d01_tasks_running() {
        val api = demo()
        val now = System.currentTimeMillis() / 1000.0
        val running = TaskItem("77", "Ищу самые дешёвые билеты в Лиссабон на май", now - 140, null, emptyList(), null, emptyList(), false, 0.02, null, true)
        val second = TaskItem("78", "Читаю почту и готовлю сводку", now - 30, null, emptyList(), null, emptyList(), false, 0.0, null, true)
        api.answers["tasks"] = { TasksSnapshot(now, listOf(running, second), emptyList()) }
        start(api); openTab("tasks"); waitTag("tasks_list"); snap(10, "tasks_running")
    }

    @Test fun d02_tasks_done() { start(); openTab("tasks"); button(str(R.string.tasks_tab_done)).performScrollTo().performClick(); waitTag("tasks_list"); snap(11, "tasks_done") }

    @Test fun d03_task_detail() {
        start(); openTab("tasks"); button(str(R.string.tasks_tab_done)).performScrollTo().performClick(); waitTag("tasks_list")
        rule.onAllNodes(hasTestTag("task_row"), useUnmergedTree = true).onFirst().performClick(); waitTag("task_detail"); snap(12, "task_detail")
    }

    @Test fun d04_tasks_empty() { start(demo().also { it.answers["tasks"] = { TasksSnapshot(0.0, emptyList(), emptyList()) } }); openTab("tasks"); waitText(str(R.string.tasks_empty_running)); snap(13, "tasks_empty") }

    // ---------- rules ----------
    private fun openMore(route: String, wait: String) { openTab("more"); tag(route).performClick(); waitTag(wait) }

    @Test fun e01_rules() { start(); openMore("more_rules", "rules_list"); snap(14, "rules") }

    @Test fun e02_rules_add() { start(); openMore("more_rules", "rules_list"); tag("rules_add").performClick(); waitTag("rules_add_field"); snap(15, "rules_add") }

    @Test fun e03_rules_empty() { start(demo().also { it.answers["rules"] = { emptyList<RuleEntry>() } }); openMore("more_rules", "rules_add"); snap(16, "rules_empty") }

    // ---------- memory ----------
    @Test fun f01_memory_facts() { start(); openTab("memory"); waitTag("facts_list"); snap(17, "memory_facts") }

    @Test fun f02_memory_people() { start(); openTab("memory"); button(str(R.string.memory_tab_people)).performScrollTo().performClick(); waitTag("people_list"); snap(18, "memory_people") }

    @Test fun f03_memory_notes() { start(); openTab("memory"); button(str(R.string.memory_tab_notes)).performScrollTo().performClick(); waitTag("notes_list"); snap(19, "memory_notes") }

    @Test fun f04_memory_skills() { start(); openTab("memory"); button(str(R.string.memory_tab_skills)).performScrollTo().performClick(); waitTag("skills_list"); snap(20, "memory_skills") }

    @Test fun f05_memory_search() { start(); openTab("memory"); waitTag("memory_search"); tag("memory_search").performTextInput("Лиссабон"); waitTag("memory_results"); hideKeyboard(); snap(21, "memory_search") }

    @Test fun f06_memory_add_fact() { start(); openTab("memory"); waitTag("facts_list"); tag("memory_add").performClick(); waitTag("fact_text"); snap(22, "memory_add_fact") }

    @Test fun f07_note() {
        start(); openTab("memory"); button(str(R.string.memory_tab_notes)).performScrollTo().performClick(); waitTag("notes_list")
        rule.onAllNodes(hasTestTag("note_row"), useUnmergedTree = true).onFirst().performClick(); waitTag("note_body"); snap(23, "note")
    }

    @Test fun f08_memory_empty() {
        start(demo().also { it.answers["facts"] = { emptyList<FactSection>() } }); openTab("memory"); waitText(str(R.string.memory_facts_empty)); snap(24, "memory_empty")
    }

    // ---------- integrations and settings ----------
    @Test fun g01_more() { start(); openTab("more"); waitTag("more_rules"); snap(25, "more") }

    @Test fun g02_integrations() { start(); openMore("more_integrations", "integrations_list"); snap(26, "integrations") }

    @Test fun g03_integration_detail() {
        start(); openMore("more_integrations", "integrations_list")
        rule.onAllNodes(hasTestTag("integration_row"), useUnmergedTree = true).onFirst().performClick(); waitTag("integration_detail"); snap(27, "integration_detail")
    }

    @Test fun g04_settings() { start(); openMore("more_settings", "settings_list"); snap(28, "settings") }

    @Test fun g05_settings_behavior() {
        start(); openMore("more_settings", "settings_list"); tag("switch_voice_reply").performScrollTo(); snap(29, "settings_behavior")
    }
}
