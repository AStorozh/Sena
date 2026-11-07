package com.tema.ia

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.assertTextContains
import androidx.lifecycle.Lifecycle
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.tema.ia.data.ThemeMode
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** The app's real activity (not a test one): launch, rotation and a theme change don't lose the screen or the typed text. */
@RunWith(AndroidJUnit4::class)
class ActivityTest {
    @get:Rule val rule = createAndroidComposeRule<MainActivity>()

    private val container get() = (InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as SenaApplication).container

    /** The real activity starts behind the lock code; these tests are about the screens under it: the lock is opened as by the code. */
    @Before fun unlock() {
        container.unlocked.value = true
    }

    @After fun reset() = runBlocking {
        container.store.setTheme(ThemeMode.System)
        container.store.clearConnection()
    }

    private fun waitTag(tag: String, timeout: Long = 10_000) = rule.waitUntil(timeout) { rule.onAllNodes(hasTestTag(tag), useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty() }

    @Test fun cold_start_to_the_first_screen_is_fast_and_connect_comes_first() {
        val started = System.nanoTime()
        waitTag("connect_address")
        val ms = (System.nanoTime() - started) / 1_000_000
        assertTrue("до первого экрана $ms мс", ms < 2_000)
    }

    @Test fun recreate_keeps_the_screen_and_the_draft() {
        runBlocking { container.store.setDemo(true) }
        waitTag("home_state")
        rule.onNodeWithTag("nav_chat", useUnmergedTree = true).performClick()
        waitTag("chat_input")
        rule.onNodeWithTag("chat_input", useUnmergedTree = true).performTextInput("Черновик письма")
        rule.activityRule.scenario.recreate()
        waitTag("chat_input")
        rule.onNodeWithTag("chat_input", useUnmergedTree = true).assertTextContains("Черновик письма")
    }

    /** An app switch closes the lock but no longer tears the app down: the open screen and the half-typed message wait behind it. */
    @Test fun the_lock_keeps_the_screen_and_the_draft_behind_it() {
        runBlocking { container.store.setDemo(true) }
        waitTag("home_state")
        rule.onNodeWithTag("nav_chat", useUnmergedTree = true).performClick()
        waitTag("chat_input")
        rule.onNodeWithTag("chat_input", useUnmergedTree = true).performTextInput("Найди билеты в Лиссабон")
        rule.activityRule.scenario.moveToState(Lifecycle.State.CREATED)   // another app in front: ON_STOP closes the lock
        assertTrue(!container.unlocked.value)
        rule.activityRule.scenario.moveToState(Lifecycle.State.RESUMED)
        rule.waitForIdle()
        assertTrue("the hidden app is not on the screen", rule.onAllNodes(hasTestTag("chat_input"), useUnmergedTree = true).fetchSemanticsNodes().isEmpty())
        container.unlocked.value = true   // the code is entered
        waitTag("chat_input")
        rule.onNodeWithTag("chat_input", useUnmergedTree = true).assertTextContains("Найди билеты в Лиссабон")
    }

    @Test fun theme_switch_keeps_the_screen() {
        runBlocking { container.store.setDemo(true) }
        waitTag("home_state")
        rule.onNodeWithTag("nav_chat", useUnmergedTree = true).performClick()
        waitTag("chat_input")
        rule.onNodeWithTag("chat_input", useUnmergedTree = true).performTextInput("Текст")
        runBlocking { container.store.setTheme(ThemeMode.Light) }
        rule.waitForIdle()
        waitTag("chat_input")
        rule.onNodeWithTag("chat_input", useUnmergedTree = true).assertTextContains("Текст")
        runBlocking { container.store.setTheme(ThemeMode.Dark) }
        rule.waitForIdle()
        waitTag("chat_input")
    }
}
