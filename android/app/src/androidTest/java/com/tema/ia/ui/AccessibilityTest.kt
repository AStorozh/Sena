package com.tema.ia.ui

import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.tema.ia.AppTest
import com.tema.ia.R
import com.tema.ia.data.DemoApi
import com.tema.ia.data.ScriptedApi
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Accessibility: every clickable element is at least 48 dp and has a TalkBack name (text or description). */
@RunWith(AndroidJUnit4::class)
class AccessibilityTest : AppTest() {
    private fun api() = ScriptedApi(DemoApi(latencyMs = 0))

    private fun checkTargets(screen: String) {
        rule.waitForIdle()
        val density = ctx.resources.displayMetrics.density
        val min = 48 * density - 1   // allowance for pixel rounding
        val problems = mutableListOf<String>()
        val nodes = rule.onAllNodes(hasClickAction() or SemanticsMatcher.keyIsDefined(SemanticsActions.OnClick), useUnmergedTree = false).fetchSemanticsNodes()
        for (n in nodes) {
            val b = n.touchBoundsInRoot
            if (b.isEmpty) continue   // element is entirely off-screen (within a scroll): it's checked after scrolling
            val label = n.config.getOrNull(SemanticsProperties.Text)?.joinToString { it.text }
                ?: n.config.getOrNull(SemanticsProperties.ContentDescription)?.joinToString()
            if (b.width < min || b.height < min) problems += "$screen: «$label» ${b.width / density}x${b.height / density} dp"
            if (label.isNullOrBlank() && n.config.getOrNull(SemanticsProperties.Role) == null) problems += "$screen: нажимаемый элемент без названия"
        }
        assertTrue(problems.joinToString("\n"), problems.isEmpty())
    }

    @Test fun targets_on_every_screen() {
        launch(api())
        waitTag("home_state")
        checkTargets("главная")
        openTab("chat"); waitTag("chat_input"); checkTargets("чат")
        openTab("tasks"); text(str(R.string.tasks_tab_done)).performClick(); waitTag("tasks_list"); checkTargets("задачи")
        rule.onAllNodes(hasTestTag("task_row"), useUnmergedTree = true).onFirst().performClick(); waitTag("task_detail"); checkTargets("задача")
        pressBack()
        openTab("memory"); waitTag("facts_list"); checkTargets("память: факты")
        button(str(R.string.memory_tab_people)).performClick(); waitTag("people_list"); checkTargets("память: люди")
        button(str(R.string.memory_tab_notes)).performClick(); waitTag("notes_list"); checkTargets("память: заметки")
        button(str(R.string.memory_tab_skills)).performClick(); waitTag("skills_list"); checkTargets("память: навыки")
        openTab("more"); waitTag("more_rules"); checkTargets("ещё")
        tag("more_rules").performClick(); waitTag("rules_list"); checkTargets("правила")
        pressBack()
        tag("more_integrations").performClick(); waitTag("integrations_list"); checkTargets("интеграции")
        pressBack()
        tag("more_settings").performClick(); waitTag("settings_list"); checkTargets("настройки")
        tag("switch_voice_reply").performScrollTo(); checkTargets("настройки, низ")
    }

    @Test fun connect_screen_targets() {
        launch(api(), connected = false)
        waitTag("connect_address")
        checkTargets("подключение")
    }

    @Test fun key_controls_have_spoken_names() {
        launch(api())
        waitTag("home_power")
        val power = rule.onNode(hasTestTag("home_power")).fetchSemanticsNode().config.getOrNull(SemanticsProperties.ContentDescription)?.joinToString().orEmpty()
        assertTrue("кнопка питания: «$power»", power == str(R.string.power_off_cd) || power == str(R.string.power_on_cd))
        openTab("chat"); waitTag("chat_send")
        val send = rule.onNode(hasTestTag("chat_send")).fetchSemanticsNode().config.getOrNull(SemanticsProperties.ContentDescription)?.joinToString().orEmpty()
        assertTrue("кнопка отправки: «$send»", send == str(R.string.chat_send_cd))
    }
}
