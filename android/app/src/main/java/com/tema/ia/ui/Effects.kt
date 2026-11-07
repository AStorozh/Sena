package com.tema.ia.ui

import android.view.Window
import android.view.WindowManager
import androidx.activity.compose.LocalActivity
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import androidx.lifecycle.compose.LifecycleStartEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.LocalViewModelStoreOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import java.util.WeakHashMap

val LocalSnackbar = staticCompositionLocalOf { SnackbarHostState() }

/** Opens the connection screen (a refused password is fixed there); null — there is none to open (the first launch). */
val LocalOpenConnect = staticCompositionLocalOf<(() -> Unit)?> { null }

/** Short screen messages ("Saved", "Failed: …") go out as a snackbar. */
@Composable
fun CollectMessages(messages: Flow<UiText>) {
    val snack = LocalSnackbar.current
    val resources = LocalContext.current.resources
    LaunchedEffect(messages) {
        messages.collect {
            snack.currentSnackbarData?.dismiss()
            snack.showSnackbar(it.resolve(resources))
        }
    }
}

/** Polling runs only while the screen is visible: the app in front and its lock open (the screens stay under a closed lock). */
@Composable
fun PollWhileVisible(vararg loaders: Loader<*>) {
    val locked = LocalLocked.current
    LifecycleStartEffect(*loaders, locked) {
        if (!locked) loaders.forEach { it.start() }
        onStopOrDispose { loaders.forEach { it.stop() } }
    }
}

/**
 * The screens' ViewModels inside live as long as this part of the app is on screen: a rotation and the lock keep them, but once
 * it leaves for good — the connection changes — they are cleared, and with them every poll, a chat task's watch and the live voice.
 * (Navigation keeps the screens' ViewModels in the store it is given: in the activity's own they would outlive the connection.)
 */
@Composable
fun ScreensScope(key: Any, content: @Composable () -> Unit) {
    val screens: ScreensStore = viewModel(key = "screens-$key", factory = viewModelFactory { initializer { ScreensStore() } })
    val activity = LocalActivity.current
    DisposableEffect(screens) {
        onDispose { if (activity?.isChangingConfigurations != true) screens.viewModelStore.clear() }
    }
    CompositionLocalProvider(LocalViewModelStoreOwner provides screens, content = content)
}

/** The store [ScreensScope] hands to the screens; it survives a rotation in the activity's own store. */
class ScreensStore : ViewModel(), ViewModelStoreOwner {
    override val viewModelStore = ViewModelStore()

    override fun onCleared() = viewModelStore.clear()
}

/** While shown, the window stays out of screenshots, screen recordings and the recents preview: the lock code and the passwords. */
@Composable
fun SecureWindow() {
    val window = LocalActivity.current?.window ?: return
    DisposableEffect(window) {
        SecureHolds.hold(window)
        onDispose { SecureHolds.release(window) }
    }
}

/** Several screens may ask for the secure window at once (the lock over the secrets): the flag goes when the last one does. */
private object SecureHolds {
    private val holds = WeakHashMap<Window, Int>()

    fun hold(window: Window) {
        val n = holds[window] ?: 0
        if (n == 0) window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        holds[window] = n + 1
    }

    fun release(window: Window) {
        val n = (holds[window] ?: return) - 1
        if (n > 0) {
            holds[window] = n
        } else {
            holds.remove(window)
            window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
    }
}

/**
 * Pull-to-refresh gesture: the indicator stays visible while data is loading.
 * One screen can depend on more than one loader (e.g. Analytics' spending and quality calls, fetched separately so a
 * slow one doesn't block the other's section from showing) — the gesture refreshes all of them and waits for all of them.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PullRefresh(vararg loaders: Loader<*>, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    // loaders is a vararg: a fresh array is allocated on every call, so keying remember() on it directly would
    // never match and silently restart this flow (and its collector) on every single recomposition. A List's
    // equals() is content-based, unlike Array's, so it's a key that actually stays stable across recompositions.
    val loaderList = loaders.toList()
    val loading by remember(loaderList) { combine(loaderList.map { it.loading }) { flags -> flags.any { it } } }.collectAsStateWithLifecycle(false)
    var pulled by remember { mutableStateOf(false) }
    LaunchedEffect(loading) { if (!loading) pulled = false }
    val isRefreshing = pulled && loading
    PullToRefreshBox(
        isRefreshing = isRefreshing,
        onRefresh = { pulled = true; loaders.forEach { it.refresh() } },
        modifier = modifier.fillMaxSize(),
    ) {
        Box(Modifier.fillMaxSize()) { content() }
    }
}
