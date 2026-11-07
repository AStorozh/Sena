package com.tema.ia.ui

import androidx.compose.animation.AnimatedContentTransitionScope
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.EnterTransition
import androidx.compose.animation.ExitTransition
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.createSavedStateHandle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import androidx.navigation.NamedNavArgument
import androidx.navigation.NavBackStackEntry
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavGraphBuilder
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.tema.ia.AppContainer
import com.tema.ia.R
import com.tema.ia.Session
import com.tema.ia.data.AndroidMic
import com.tema.ia.data.AndroidSpeaker
import com.tema.ia.ui.components.FloatingNav
import com.tema.ia.ui.components.NavItem
import com.tema.ia.ui.screens.analytics.AnalyticsScreen
import com.tema.ia.ui.screens.analytics.AnalyticsViewModel
import com.tema.ia.ui.screens.chat.ChatScreen
import com.tema.ia.ui.screens.chat.ChatViewModel
import com.tema.ia.ui.screens.chat.VoiceViewModel
import com.tema.ia.ui.screens.connect.ConnectScreen
import com.tema.ia.ui.screens.connect.ConnectViewModel
import com.tema.ia.ui.screens.home.HomeScreen
import com.tema.ia.ui.screens.home.HomeViewModel
import com.tema.ia.ui.screens.integrations.IntegrationsScreen
import com.tema.ia.ui.screens.integrations.IntegrationsViewModel
import com.tema.ia.ui.screens.memory.MemoryScreen
import com.tema.ia.ui.screens.memory.MemoryViewModel
import com.tema.ia.ui.screens.memory.NoteScreen
import com.tema.ia.ui.screens.memory.NoteViewModel
import com.tema.ia.ui.screens.memory.displayTitle
import com.tema.ia.ui.screens.more.MoreScreen
import com.tema.ia.ui.screens.panels.PanelScreen
import com.tema.ia.ui.screens.panels.PanelViewModel
import com.tema.ia.ui.screens.panels.PanelsViewModel
import com.tema.ia.ui.screens.rules.RulesScreen
import com.tema.ia.ui.screens.rules.RulesViewModel
import com.tema.ia.ui.screens.secrets.SecretsScreen
import com.tema.ia.ui.screens.secrets.SecretsViewModel
import com.tema.ia.ui.screens.settings.SettingsScreen
import com.tema.ia.ui.screens.settings.SettingsViewModel
import com.tema.ia.ui.screens.tasks.TaskDetailScreen
import com.tema.ia.ui.screens.tasks.TaskDetailViewModel
import com.tema.ia.ui.screens.tasks.TasksScreen
import com.tema.ia.ui.screens.tasks.TasksViewModel
import com.tema.ia.ui.theme.Motion
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Space

object Routes {
    const val Home = "home"
    const val Chat = "chat"
    const val Tasks = "tasks"
    const val Memory = "memory"
    const val More = "more"
    const val Rules = "rules"
    const val Integrations = "integrations"
    const val Analytics = "analytics"
    const val Settings = "settings"
    const val Secrets = "secrets"
    const val Panel = "panel/{id}"
    const val Task = "task/{id}"
    const val Note = "note?path={path}&title={title}"
    const val Connect = "connect"
    fun task(id: String) = "task/" + android.net.Uri.encode(id)
    fun panel(id: String) = "panel/" + android.net.Uri.encode(id)
    fun note(path: String, title: String) = "note?path=" + android.net.Uri.encode(path) + "&title=" + android.net.Uri.encode(title)
}

private val tabs = listOf(
    NavItem(Routes.Home, R.string.nav_home),
    NavItem(Routes.Chat, R.string.nav_chat),
    NavItem(Routes.Tasks, R.string.nav_tasks),
    NavItem(Routes.Memory, R.string.nav_memory),
    NavItem(Routes.More, R.string.nav_more),
)

/**
 * Shell: screens plus a floating bottom menu of five items. Everything is rebuilt when the connection changes, and the screens'
 * ViewModels go with it ([ScreensScope]). The lock only hides the shell (ui/AppLock.kt): the open screen, what was typed and the
 * answer being waited for are there again when it opens.
 */
@Composable
fun AppShell(container: AppContainer, session: Session.Active) = key(session.id) { ScreensScope(session.id) { Shell(container, session) } }

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Shell(container: AppContainer, session: Session.Active) {
    val nav = rememberNavController()
    val snackbar = remember { SnackbarHostState() }
    val backStack by nav.currentBackStackEntryAsState()
    val selectedTab = tabs.firstOrNull { t -> backStack?.destination?.hierarchy?.any { it.route == t.route } == true }?.route
    // Chat needs the vertical room the floating menu would take, so it hides there; the screen's own back arrow returns to Home, which has it again.
    val showFloatingNav = selectedTab != null && selectedTab != Routes.Chat
    val keyboard = WindowInsets.isImeVisible
    val colors = SenaTheme.colors
    val api = session.api
    val appContext = LocalContext.current.applicationContext
    val openTab = { route: String ->
        nav.navigate(route) {
            popUpTo(nav.graph.findStartDestination().id) { saveState = true }
            launchSingleTop = true
            restoreState = true
        }
    }
    val openConnect = { nav.navigate(Routes.Connect) { launchSingleTop = true } }

    CompositionLocalProvider(LocalSnackbar provides snackbar, LocalOpenConnect provides openConnect.takeIf { !session.demo }) {
        Box(Modifier.fillMaxSize().background(colors.header)) {
            NavHost(
                navController = nav,
                startDestination = Routes.Home,
                modifier = Modifier.fillMaxSize(),
                enterTransition = { tabEnter() },
                exitTransition = { tabExit() },
                popEnterTransition = { slideInHorizontally(tween(Motion.medium, easing = FastOutSlowInEasing)) { -it / Motion.tabSlide } + fadeIn(tween(Motion.medium)) },
                popExitTransition = { fadeOut(tween(Motion.short)) },
            ) {
                composable(Routes.Home) {
                    val vm: HomeViewModel = viewModel(factory = viewModelFactory { initializer { HomeViewModel(api) } })
                    HomeScreen(vm, onOpenTask = { nav.navigate(Routes.task(it)) }, onOpenTasks = { openTab(Routes.Tasks) })
                }
                composable(Routes.Chat) {
                    // both follow the lock: the answer being waited for pauses behind it, the live voice ends when it closes
                    val vm: ChatViewModel = viewModel(factory = viewModelFactory { initializer { ChatViewModel(api, open = container.unlocked) } })
                    val voice: VoiceViewModel = viewModel(factory = viewModelFactory {
                        initializer { VoiceViewModel(api, open = container.unlocked) { AndroidMic() to AndroidSpeaker(appContext) } }
                    })
                    ChatScreen(vm, voice, onGoHome = { openTab(Routes.Home) })
                }
                composable(Routes.Tasks) {
                    val vm: TasksViewModel = viewModel(factory = viewModelFactory { initializer { TasksViewModel(api) } })
                    TasksScreen(vm) { nav.navigate(Routes.task(it.id)) }
                }
                composable(Routes.Memory) {
                    val vm: MemoryViewModel = viewModel(factory = viewModelFactory { initializer { MemoryViewModel(api, createSavedStateHandle()) } })
                    MemoryScreen(
                        vm, onOpenNote = { note -> nav.navigate(Routes.note(note.path, note.displayTitle())) },
                        onOpenTask = { nav.navigate(Routes.task(it)) }, onOpenChat = { openTab(Routes.Chat) },
                    )
                }
                composable(Routes.More) {
                    val panelsVm: PanelsViewModel = viewModel(factory = viewModelFactory { initializer { PanelsViewModel(api) } })
                    PollWhileVisible(panelsVm.loader)
                    val panels by panelsVm.loader.state.collectAsStateWithLifecycle()
                    MoreScreen(
                        onRules = { nav.navigate(Routes.Rules) }, onIntegrations = { nav.navigate(Routes.Integrations) },
                        onAnalytics = { nav.navigate(Routes.Analytics) }, onSettings = { nav.navigate(Routes.Settings) },
                        onSecrets = { nav.navigate(Routes.Secrets) },
                        panels = (panels as? Load.Ready)?.value.orEmpty(), onPanel = { nav.navigate(Routes.panel(it)) },
                    )
                }
                detail(Routes.Rules) {
                    val vm: RulesViewModel = viewModel(factory = viewModelFactory { initializer { RulesViewModel(api) } })
                    RulesScreen(vm, onBack = { nav.popBackStack() })
                }
                detail(Routes.Integrations) {
                    val vm: IntegrationsViewModel = viewModel(factory = viewModelFactory { initializer { IntegrationsViewModel(api) } })
                    IntegrationsScreen(vm, onBack = { nav.popBackStack() })
                }
                detail(Routes.Analytics) {
                    val vm: AnalyticsViewModel = viewModel(factory = viewModelFactory { initializer { AnalyticsViewModel(api) } })
                    AnalyticsScreen(vm, onBack = { nav.popBackStack() })
                }
                detail(Routes.Settings) {
                    val vm: SettingsViewModel = viewModel(factory = viewModelFactory { initializer { SettingsViewModel(api, container.store) } })
                    SettingsScreen(vm, session.demo, session.address, onChangeConnection = { nav.navigate(Routes.Connect) }, onBack = { nav.popBackStack() })
                }
                detail(Routes.Panel, listOf(navArgument("id") { type = NavType.StringType })) { entry ->
                    val id = entry.arguments?.getString("id").orEmpty()
                    val vm: PanelViewModel = viewModel(key = "panel-$id", factory = viewModelFactory { initializer { PanelViewModel(api, id) } })
                    PanelScreen(vm, onBack = { nav.popBackStack() })
                }
                detail(Routes.Secrets) {
                    val vm: SecretsViewModel = viewModel(factory = viewModelFactory { initializer { SecretsViewModel(api) } })
                    SecretsScreen(vm, onBack = { nav.popBackStack() })
                }
                detail(Routes.Task, listOf(navArgument("id") { type = NavType.StringType })) { entry ->
                    val id = entry.arguments?.getString("id").orEmpty()
                    val vm: TaskDetailViewModel = viewModel(factory = viewModelFactory { initializer { TaskDetailViewModel(api, id) } })
                    TaskDetailScreen(vm, onBack = { nav.popBackStack() })
                }
                detail(Routes.Note, listOf(navArgument("path") { type = NavType.StringType }, navArgument("title") { type = NavType.StringType; defaultValue = "" })) { entry ->
                    val path = entry.arguments?.getString("path").orEmpty()
                    val title = entry.arguments?.getString("title").orEmpty().ifBlank { stringResource(R.string.note_title) }
                    val vm: NoteViewModel = viewModel(factory = viewModelFactory { initializer { NoteViewModel(api, path) } })
                    NoteScreen(vm, title, onBack = { nav.popBackStack() })
                }
                detail(Routes.Connect) {
                    val vm: ConnectViewModel = viewModel(factory = viewModelFactory {
                        initializer { ConnectViewModel(createSavedStateHandle(), container.store, container.checkConnection(), session.address) }
                    })
                    ConnectScreen(vm, onBack = { nav.popBackStack() }, allowDemo = false)
                }
            }
            Column(
                Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(bottom = if (showFloatingNav && !keyboard) Space.gap else Space.small),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                SnackbarHost(snackbar, Modifier.padding(horizontal = Space.screen)) { data ->
                    Snackbar(data, shape = com.tema.ia.ui.theme.Radius.pill, containerColor = colors.ink, contentColor = colors.onInk, actionContentColor = colors.onInk)
                }
                AnimatedVisibility(
                    visible = showFloatingNav && !keyboard,
                    enter = fadeIn(tween(Motion.medium)) + slideInVertically(tween(Motion.medium)) { it / 2 },
                    exit = fadeOut(tween(Motion.short)) + slideOutVertically(tween(Motion.short)) { it / 2 },
                ) {
                    FloatingNav(tabs, selectedTab, openTab, Modifier.padding(top = Space.small))
                }
            }
        }
    }
}

private fun tabIndex(route: String?): Int? = tabs.indexOfFirst { it.route == route }.takeIf { it >= 0 }

/** Tabs page like sheets: the new one slides in from the side the tab index moved toward, the old one slides out the other way. */
private fun AnimatedContentTransitionScope<NavBackStackEntry>.tabEnter(): EnterTransition {
    val from = tabIndex(initialState.destination.route)
    val to = tabIndex(targetState.destination.route)
    if (from == null || to == null) return fadeIn(tween(Motion.medium))
    val dir = if (to > from) 1 else -1
    return slideInHorizontally(tween(Motion.medium, easing = FastOutSlowInEasing)) { it * dir / Motion.tabSlide } + fadeIn(tween(Motion.medium))
}

private fun AnimatedContentTransitionScope<NavBackStackEntry>.tabExit(): ExitTransition {
    val from = tabIndex(initialState.destination.route)
    val to = tabIndex(targetState.destination.route)
    val dir = if (from != null && to != null && to < from) 1 else -1
    return slideOutHorizontally(tween(Motion.medium, easing = FastOutSlowInEasing)) { it * dir / Motion.tabSlide } + fadeOut(tween(Motion.short))
}

/** Second-level screen: slides in from the right, no bottom menu. */
private fun NavGraphBuilder.detail(
    route: String,
    arguments: List<NamedNavArgument> = emptyList(),
    content: @Composable (NavBackStackEntry) -> Unit,
) {
    composable(
        route, arguments,
        enterTransition = { slideInHorizontally(tween(Motion.medium, easing = FastOutSlowInEasing)) { it / 4 } + fadeIn(tween(Motion.medium)) },
        exitTransition = { slideOutHorizontally(tween(Motion.medium, easing = FastOutSlowInEasing)) { -it / Motion.tabSlide } + fadeOut(tween(Motion.short)) },
        popEnterTransition = { slideInHorizontally(tween(Motion.medium, easing = FastOutSlowInEasing)) { -it / Motion.tabSlide } + fadeIn(tween(Motion.medium)) },
        popExitTransition = { slideOutHorizontally(tween(Motion.medium, easing = FastOutSlowInEasing)) { it / 4 } + fadeOut(tween(Motion.medium)) },
    ) { content(it) }
}
