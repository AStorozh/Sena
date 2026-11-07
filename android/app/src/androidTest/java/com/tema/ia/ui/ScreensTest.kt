package com.tema.ia.ui

import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasAnyAncestor
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToIndex
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.tema.ia.AppTest
import com.tema.ia.R
import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import com.tema.ia.data.DemoApi
import com.tema.ia.data.ScriptedApi
import com.tema.ia.data.TaskItem
import com.tema.ia.data.TasksSnapshot
import com.tema.ia.data.ThemeMode
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

private fun api() = ScriptedApi(DemoApi(latencyMs = 0))

@RunWith(AndroidJUnit4::class)
class TasksScreenTest : AppTest() {
    private fun menu() = rule.onNode(hasContentDescription(str(R.string.cd_more_actions)), useUnmergedTree = true)

    @Test fun running_tab_is_empty_and_done_tab_lists_tasks_without_service_ones() {
        launch(api())
        openTab("tasks")
        waitText(str(R.string.tasks_empty_running))
        text(str(R.string.tasks_tab_done)).performClick()
        waitTag("tasks_list")
        assertEquals(3, count("task_row"))
        text("Что сейчас обсуждают на Hacker News? Топ-3.").assertExists()
        text(str(R.string.verdict_failed)).assertExists()
        text(str(R.string.verdict_remarks)).assertExists()
    }

    @Test fun service_tasks_can_be_shown() {
        launch(api())
        openTab("tasks")
        text(str(R.string.tasks_tab_done)).performClick()
        waitTag("tasks_list")
        menu().performClick()
        text(str(R.string.tasks_show_service)).performClick()
        rule.waitForIdle()
        waitFor { count("task_row") == 4 }
    }

    @Test fun task_detail_shows_steps_answer_and_takes_a_rating_and_a_remark() {
        val api = api()
        launch(api)
        openTab("tasks")
        text(str(R.string.tasks_tab_done)).performClick()
        waitTag("tasks_list")
        rule.onAllNodes(hasTestTag("task_row"), useUnmergedTree = true).onFirst().performClick()
        waitTag("task_detail")
        text(str(R.string.task_steps)).assertExists()
        text(str(R.string.task_answer)).assertExists()
        button("2").performScrollTo().performClick()
        waitFor { api.count("rate") == 1 }
        waitGone(str(R.string.task_rated))
        tag("task_remark").performScrollTo().performTextInput("Не открыл сайт")
        hideKeyboard()
        button(str(R.string.task_comment_send)).performScrollTo().performClick()
        waitFor { api.count("comment") == 1 }
        waitText("Не открыл сайт")
    }

    @Test fun a_running_task_can_be_stopped_from_its_page() {
        val api = api()
        val running = TaskItem("77", "Ищу билеты", System.currentTimeMillis() / 1000.0, null, emptyList(), null, emptyList(), false, 0.0, null, true)
        api.answers["tasks"] = { TasksSnapshot(System.currentTimeMillis() / 1000.0, listOf(running), emptyList()) }
        api.answers["task"] = { running }
        launch(api)
        openTab("tasks")
        waitTag("task_row")
        text(str(R.string.task_running)).assertExists()
        rule.onAllNodes(hasTestTag("task_row"), useUnmergedTree = true).onFirst().performClick()
        waitTag("task_detail")
        button(str(R.string.task_stop)).performClick()
        waitFor { api.count("stopTask") == 1 }
    }

    @Test fun tasks_error_has_a_retry() {
        val api = api().also { it.failures["tasks"] = ApiException(ApiError.NoServer, "x") }
        launch(api)
        openTab("tasks")
        waitText(str(R.string.action_retry))
        api.failures.remove("tasks")
        button(str(R.string.action_retry)).performClick()
        waitText(str(R.string.tasks_tab_done))
    }
}

@RunWith(AndroidJUnit4::class)
class RulesScreenTest : AppTest() {
    private fun openRules() {
        openTab("more")
        tag("more_rules").performClick()
        waitTag("rules_list")
    }

    @Test fun rules_jobs_and_alerts_are_one_list() {
        launch(api())
        openRules()
        assertEquals(4, count("rule_row"))
        text("Каждое утро присылай сводку новостей").assertExists()
        text("Напомнить про отчёт").assertExists()
        text("BTC выше 90 000 долларов").assertExists()
    }

    @Test fun switch_turns_a_rule_off_on_the_server() {
        val api = api()
        launch(api)
        openRules()
        rule.onAllNodes(hasTestTag("rule_switch"), useUnmergedTree = true).onFirst().performClick()
        waitFor { api.count("setRuleEnabled") == 1 }
    }

    @Test fun a_rule_is_added_with_one_phrase() {
        val api = api()
        launch(api)
        openRules()
        tag("rules_add").performClick()
        waitTag("rules_add_field")
        button(str(R.string.action_save)).assertIsNotEnabled()
        tag("rules_add_field").performTextInput("Каждую пятницу напоминай про отчёт")
        hideKeyboard()
        tag("rules_add_save").performClick()
        waitFor { api.count("addRule") == 1 }
        waitText("Каждую пятницу напоминай про отчёт")
    }

    @Test fun delete_needs_confirmation() {
        val api = api()
        launch(api)
        openRules()
        rule.onAllNodes(hasContentDescription(str(R.string.cd_more_actions)), useUnmergedTree = true).onFirst().performClick()
        text(str(R.string.action_delete)).performClick()
        text(str(R.string.rules_delete_title)).assertExists()
        assertEquals(0, api.count("removeRule"))
        button(str(R.string.action_cancel)).performClick()
        rule.waitForIdle()
        assertEquals(0, api.count("removeRule"))
        rule.onAllNodes(hasContentDescription(str(R.string.cd_more_actions)), useUnmergedTree = true).onFirst().performClick()
        text(str(R.string.action_delete)).performClick()
        button(str(R.string.action_delete)).performClick()
        waitFor { api.count("removeRule") == 1 }
        waitFor { count("rule_row") == 3 }
    }

    @Test fun empty_list_shows_examples() {
        val api = api()
        api.answers["rules"] = { emptyList<com.tema.ia.data.RuleEntry>() }
        launch(api)
        openTab("more")
        tag("more_rules").performClick()
        waitText(str(R.string.rules_empty))
        text(str(R.string.rules_empty_hint)).assertExists()
    }
}

@RunWith(AndroidJUnit4::class)
class MemoryScreenTest : AppTest() {
    @Test fun facts_are_cards_with_sections() {
        launch(api())
        openTab("memory")
        waitTag("facts_list")
        text("Обо мне").assertExists()
        text("Живу в Лиссабоне").assertExists()
    }

    @Test fun a_fact_is_added_and_then_deleted() {
        val api = api()
        launch(api)
        openTab("memory")
        waitTag("facts_list")
        tag("memory_add").performClick()
        waitTag("fact_text")
        tag("fact_text").performTextInput("Люблю чай без сахара")
        hideKeyboard()
        tag("fact_save").performClick()
        waitFor { api.count("addFact") == 1 }
        waitText("Люблю чай без сахара")
        rule.onAllNodes(hasContentDescription(str(R.string.cd_delete)), useUnmergedTree = true).onFirst().performClick()
        text(str(R.string.memory_delete_fact_title)).assertExists()
        button(str(R.string.action_delete)).performClick()
        waitFor { api.count("deleteFact") == 1 }
    }

    @Test fun people_notes_and_skills_tabs() {
        launch(api())
        openTab("memory")
        button(str(R.string.memory_tab_people)).performClick()
        waitTag("people_list")
        text("Ольга").assertExists()
        button(str(R.string.memory_tab_notes)).performClick()
        waitTag("notes_list")
        assertEquals(3, count("note_row"))
        button(str(R.string.memory_tab_skills)).performClick()
        waitTag("skills_list")
        text("mac-volume").assertExists()
    }

    @Test fun a_note_opens_and_a_new_one_is_created() {
        val api = api()
        launch(api)
        openTab("memory")
        button(str(R.string.memory_tab_notes)).performClick()
        waitTag("notes_list")
        rule.onAllNodes(hasTestTag("note_row"), useUnmergedTree = true).onFirst().performClick()
        waitTag("note_body")
        assertTrue(rule.onAllNodes(hasText("кейса", substring = true), useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty())
        pressBack()
        waitTag("notes_list")
        tag("memory_add").performClick()
        waitTag("note_title")
        tag("note_title").performTextInput("Идея про кофейню")
        hideKeyboard()
        tag("note_save").performClick()
        waitFor { api.count("writeNote") == 1 }
        waitText("Идея про кофейню")
    }

    @Test fun a_skill_opens_in_a_sheet() {
        launch(api())
        openTab("memory")
        button(str(R.string.memory_tab_skills)).performClick()
        waitTag("skills_list")
        text("mac-volume").performClick()
        waitText(str(R.string.memory_skill_recipe))
    }

    @Test fun search_finds_and_reports_nothing() {
        launch(api())
        openTab("memory")
        waitTag("memory_search")
        tag("memory_search").performTextInput("Лиссабон")
        waitTag("memory_results")
        text("Живу в Лиссабоне").assertExists()
        tag("memory_search").performTextClearance()
        tag("memory_search").performTextInput("zzzzzz")
        waitText(str(R.string.memory_search_empty))
    }

    @Test fun empty_facts_show_a_hint() {
        val api = api()
        api.answers["facts"] = { emptyList<com.tema.ia.data.FactSection>() }
        launch(api)
        openTab("memory")
        waitText(str(R.string.memory_facts_empty))
    }
}

@RunWith(AndroidJUnit4::class)
class IntegrationsSettingsTest : AppTest() {
    private fun openMore(tag: String, wait: String) {
        openTab("more")
        tag(tag).performClick()
        waitTag(wait)
    }

    @Test fun integrations_are_grouped_and_toggle() {
        val api = api()
        launch(api)
        openMore("more_integrations", "integrations_list")
        text(str(R.string.int_group_comm)).assertExists()
        tag("integrations_list").performScrollToNode(androidx.compose.ui.test.hasText(str(R.string.int_browser_name)))
        text(str(R.string.int_browser_name)).assertExists()
        tag("integrations_list").performScrollToIndex(0)
        rule.waitForIdle()
        rule.onAllNodes(hasTestTag("integration_switch"), useUnmergedTree = true).onFirst().performClick()
        waitFor { api.count("setIntegration") == 1 }
    }

    @Test fun integration_detail_explains_how_to_connect() {
        launch(api())
        openMore("more_integrations", "integrations_list")
        rule.onAllNodes(hasTestTag("integration_row"), useUnmergedTree = true).onFirst().performClick()
        waitTag("integration_detail")
        text(str(R.string.int_how_title)).assertExists()
    }

    @Test fun settings_theme_and_behavior_are_saved() {
        val api = api()
        launch(api)
        openMore("more_settings", "settings_list")
        text(str(R.string.settings_demo)).assertExists()
        button(str(R.string.settings_theme_dark)).performClick()
        waitFor { runBlocking { store.theme.first() } == ThemeMode.Dark }
        waitText(str(R.string.opt_verbosity_medium))
        button(str(R.string.opt_verbosity_medium)).performClick()
        waitFor { api.count("updateSettings") == 1 }
        tag("budget_field").performScrollTo().performTextClearance()
        tag("budget_field").performTextInput("12")
        tag("budget_field").performImeAction()
        waitFor { api.count("updateSettings") == 2 }
        hideKeyboard()
        waitGone(str(R.string.settings_saved))
        tag("switch_heartbeat").performScrollTo().performClick()
        waitFor { api.count("updateSettings") == 3 }
    }

    @Test fun settings_without_connection_to_the_server_still_open() {
        val api = api().also { it.failures["config"] = ApiException(ApiError.NoServer, "x") }
        launch(api)
        openMore("more_settings", "settings_list")
        waitText(str(R.string.settings_behavior_unavailable))
        text(str(R.string.settings_look)).assertExists()
    }

    @Test fun demo_can_go_to_the_connection_screen() {
        launch(api())
        openMore("more_settings", "settings_list")
        tag("settings_connect").performClick()
        waitTag("connect_address")
    }
}

@RunWith(AndroidJUnit4::class)
class ConnectScreenTest : AppTest() {
    @Test fun first_launch_asks_for_address_and_password_and_validates() {
        launch(api(), connected = false)
        waitTag("connect_address")
        tag("connect_submit").performClick()
        waitText(str(R.string.connect_empty_address))
        tag("connect_address").performTextInput("sena.example.com")
        tag("connect_submit").performClick()
        waitText(str(R.string.err_cleartext))
    }

    @Test fun wrong_password_is_explained() {
        val api = api().also { it.failures["overview"] = ApiException(ApiError.BadToken, "x") }
        launch(api, connected = false)
        waitTag("connect_address")
        tag("connect_address").performTextInput("10.0.2.2")
        tag("connect_token").performTextInput("bad")
        tag("connect_submit").performClick()
        waitText(str(R.string.err_bad_token))
        assertEquals(null, runBlocking { store.connection.first() })
    }

    @Test fun a_good_connection_is_saved_and_opens_home() {
        launch(api(), connected = false)
        waitTag("connect_address")
        tag("connect_address").performTextInput("10.0.2.2")
        tag("connect_token").performTextInput("secret")
        tag("connect_submit").performClick()
        waitTag("home_state", 10_000)
        val saved = runBlocking { store.connection.first() }
        assertEquals("http://10.0.2.2:8765", saved?.baseUrl)
        assertEquals("secret", saved?.token)
    }

    @Test fun demo_opens_the_app_without_a_server() {
        launch(api(), connected = false)
        waitTag("connect_demo")
        tag("connect_demo").performClick()
        waitTag("home_state")
    }

    @Test fun password_is_hidden_until_asked() {
        launch(api(), connected = false)
        waitTag("connect_token")
        tag("connect_token").performTextInput("secret")
        tag("connect_token").assert(SemanticsMatcher.keyIsDefined(SemanticsProperties.Password))       // hidden
        rule.onNode(hasContentDescription(str(R.string.cd_show_password)), useUnmergedTree = true).performClick()
        rule.waitForIdle()
        rule.onNode(hasContentDescription(str(R.string.cd_hide_password)), useUnmergedTree = true).assertExists()   // now shown, the button offers to hide it
    }
}
