package com.tema.ia

import android.content.Context
import androidx.annotation.StringRes
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.hasTestTag
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onLast
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.Density
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.test.platform.app.InstrumentationRegistry
import com.tema.ia.data.Connection
import com.tema.ia.data.DemoApi
import com.tema.ia.data.ScriptedApi
import com.tema.ia.data.SenaApi
import com.tema.ia.data.Store
import com.tema.ia.data.ThemeMode
import com.tema.ia.ui.SenaRoot
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Rule
import java.io.File

/** Base for on-device screen tests: the real app (theme, navigation, screens) against a fake server. */
abstract class AppTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    protected val ctx: Context = InstrumentationRegistry.getInstrumentation().targetContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    protected lateinit var container: AppContainer
    protected lateinit var store: Store
    private var lastApi: SenaApi? = null

    @After fun tearDown() = scope.cancel()

    /** Strings in the same language the app is shown in (when launched with a locale, that one). */
    private var uiCtx: Context = ctx

    protected fun str(@StringRes id: Int, vararg args: Any): String = uiCtx.getString(id, *args)

    /** Launches the app. By default it's already connected (demo), so the shell is visible right away. */
    protected fun launch(
        api: SenaApi = ScriptedApi(DemoApi(latencyMs = 0)),
        theme: ThemeMode = ThemeMode.System,
        fontScale: Float = 1f,
        locale: java.util.Locale? = null,
        connected: Boolean = true,
        check: suspend (Connection) -> Unit = {},
    ) {
        store = Store(PreferenceDataStoreFactory.create(scope = scope) { File(ctx.cacheDir, "test_${System.nanoTime()}.preferences_pb") })
        runBlocking {
            store.setTheme(theme)
            if (connected) store.setDemo(true)
        }
        lastApi = api
        uiCtx = if (locale == null) ctx else ctx.createConfigurationContext(android.content.res.Configuration(ctx.resources.configuration).also { it.setLocale(locale) })
        container = AppContainer(store, scope, demoFactory = { api }, httpFactory = { api })
        container.unlocked.value = true   // the screens under test, not the lock: the app opens as if the code was just entered
        if (locale != null) {
            // Language for the whole window (and the bottom sheets, which have their own context): set it on the activity itself
            java.util.Locale.setDefault(locale)
            rule.activity.resources.let { r -> r.updateConfiguration(android.content.res.Configuration(r.configuration).also { it.setLocale(locale) }, r.displayMetrics) }
        }
        rule.setContent {
            val density = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(density.density, fontScale)) {
                SenaRoot(container) { dark ->
                    val bottom = if (dark) androidx.activity.SystemBarStyle.dark(android.graphics.Color.TRANSPARENT)
                    else androidx.activity.SystemBarStyle.light(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT)
                    rule.activity.enableEdgeToEdge(statusBarStyle = androidx.activity.SystemBarStyle.dark(android.graphics.Color.TRANSPARENT), navigationBarStyle = bottom)
                }
            }
        }
    }

    protected fun tag(t: String): SemanticsNodeInteraction = rule.onNodeWithTag(t, useUnmergedTree = true)
    protected fun text(t: String): SemanticsNodeInteraction = rule.onNodeWithText(t, useUnmergedTree = true)

    /** The button with this text (if there are two, e.g. on the screen and in a dialog, the last one, i.e. the topmost). */
    protected fun button(t: String): SemanticsNodeInteraction =
        rule.onAllNodes(androidx.compose.ui.test.hasText(t) and androidx.compose.ui.test.hasClickAction()).onLast()

    /** The on-screen keyboard covers buttons below the field; we dismiss it before tapping them. */
    protected fun hideKeyboard() {
        rule.runOnUiThread {
            val window = rule.activity.window
            androidx.core.view.WindowCompat.getInsetsController(window, window.decorView).hide(androidx.core.view.WindowInsetsCompat.Type.ime())
        }
        rule.waitForIdle()
        Thread.sleep(600)    // the keyboard slides away with an animation: while it moves, the buttons below the field shift
        rule.waitForIdle()
    }

    /** A screenshot of the real screen (with system bars) into /data/local/tmp/sena_shots; adb pull retrieves it from there. */
    protected fun shot(name: String) {
        runCatching {
            val ui = InstrumentationRegistry.getInstrumentation().uiAutomation
            fun sh(cmd: String) = android.os.ParcelFileDescriptor.AutoCloseInputStream(ui.executeShellCommand(cmd)).use { it.readBytes() }
            sh("mkdir -p /data/local/tmp/sena_shots")
            sh("screencap -p /data/local/tmp/sena_shots/$name.png")
        }
    }

    protected fun pressBack() {
        InstrumentationRegistry.getInstrumentation().sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK)
        rule.waitForIdle()
    }

    protected fun openTab(route: String) {
        tag("nav_$route").performClick()
        rule.waitForIdle()
    }

    protected fun waitFor(timeoutMs: Long = 8_000, message: String = "условие не наступило", condition: () -> Boolean) {
        try {
            rule.waitUntil(timeoutMs) { condition() }
        } catch (e: androidx.compose.ui.test.ComposeTimeoutException) {
            val texts = runCatching {
                rule.onAllNodes(androidx.compose.ui.test.SemanticsMatcher("any") { true }, useUnmergedTree = true).fetchSemanticsNodes()
                    .flatMap { n -> (n.config.getOrNull(androidx.compose.ui.semantics.SemanticsProperties.Text) ?: emptyList<androidx.compose.ui.text.AnnotatedString>()).map { it.text } }
            }.getOrDefault(emptyList())
            val calls = (lastApi as? ScriptedApi)?.calls?.takeLast(25)
            shot("fail_${System.currentTimeMillis()}")
            throw AssertionError("$message\nТексты на экране: $texts\nВызовы: $calls")
        }
    }

    protected fun exists(tag: String): Boolean = rule.onAllNodes(hasTestTag(tag), useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty()
    protected fun count(tag: String): Int = rule.onAllNodes(hasTestTag(tag), useUnmergedTree = true).fetchSemanticsNodes().size
    protected fun waitTag(tag: String, timeoutMs: Long = 8_000) = waitFor(timeoutMs, "нет элемента $tag") { exists(tag) }
    /** The short message at the bottom (snackbar) covers the buttons under it: we wait for it to go away before tapping. */
    protected fun waitGone(text: String, timeoutMs: Long = 40_000) =
        waitFor(timeoutMs, "текст «$text» не исчез") { rule.onAllNodes(androidx.compose.ui.test.hasText(text), useUnmergedTree = true).fetchSemanticsNodes().isEmpty() }

    protected fun waitText(text: String, timeoutMs: Long = 8_000) =
        waitFor(timeoutMs, "нет текста «$text»") { rule.onAllNodesWithTextCompat(text).isNotEmpty() }

    private fun androidx.compose.ui.test.junit4.ComposeContentTestRule.onAllNodesWithTextCompat(t: String) =
        onAllNodes(androidx.compose.ui.test.hasText(t), useUnmergedTree = true).fetchSemanticsNodes()
}
