package com.tema.ia.ui

import android.content.res.Resources
import androidx.annotation.StringRes
import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import com.tema.ia.R
import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/** Text for a screen: either a resource string or ready-made text (e.g. an explanation from the server itself). */
sealed interface UiText {
    data class Res(@StringRes val id: Int, val args: List<Any> = emptyList()) : UiText
    data class Raw(val text: String) : UiText

    fun resolve(resources: Resources): String = when (this) {
        is Res -> resources.getString(id, *args.toTypedArray())
        is Raw -> text
    }
}

@Composable
fun UiText.asString(): String = when (this) {
    is UiText.Res -> stringResource(id, *args.toTypedArray())
    is UiText.Raw -> text
}

/** Connection error in human words. Server explanations (Server) are shown as is. */
fun Throwable.toUiText(): UiText = when ((this as? ApiException)?.kind) {
    ApiError.NoServer -> UiText.Res(R.string.err_no_server)
    ApiError.BadToken -> UiText.Res(R.string.err_bad_token)
    ApiError.Locked -> UiText.Res(R.string.err_locked)
    ApiError.BadAnswer -> UiText.Res(R.string.err_bad_answer)
    ApiError.Server -> UiText.Raw(message)
    null -> UiText.Res(R.string.err_unknown)
}

/** No connection is a separate state (a banner plus the last data), not a "screen error". */
val Throwable?.isOffline: Boolean get() = (this as? ApiException)?.kind == ApiError.NoServer

/** The password is refused or the address is locked for guessing: asking again with the same password only makes it worse. */
val Throwable?.isRefused: Boolean get() = (this as? ApiException)?.kind.let { it == ApiError.BadToken || it == ApiError.Locked }

/** A failed refresh of data already on screen: shown in the banner over the sheet, the data stays. */
val Load<*>.problem: Throwable? get() = (this as? Load.Ready<*>)?.error

/** What the screen shows while it loads data from the server. */
sealed interface Load<out T> {
    data object Loading : Load<Nothing>
    /** Data is available. [error] means the last refresh attempt failed, but the previous data stays on screen. */
    data class Ready<T>(val value: T, val error: Throwable? = null) : Load<T>
    data class Failed(val error: Throwable) : Load<Nothing>
}

val <T> Load<T>.valueOrNull: T? get() = (this as? Load.Ready<T>)?.value

/**
 * Loads data once every [intervalMs] while the screen is visible ([start] / [stop] are called by the lifecycle), and immediately on [refresh].
 * An error does not erase the previous data. A refused password or a locked address stops the rhythm: polling with a wrong
 * password every few seconds would only lock the address again; the owner's own refresh asks once more.
 */
class Loader<T>(
    private val scope: CoroutineScope,
    private val interval: () -> Long,
    private val load: suspend () -> T,
) {
    constructor(scope: CoroutineScope, intervalMs: Long, load: suspend () -> T) : this(scope, { intervalMs }, load)

    private val _state = MutableStateFlow<Load<T>>(Load.Loading)
    val state: StateFlow<Load<T>> = _state.asStateFlow()
    private val _loading = MutableStateFlow(false)
    val loading: StateFlow<Boolean> = _loading.asStateFlow()
    private val kick = Channel<Unit>(Channel.CONFLATED)
    private var job: Job? = null     // the rhythm, while the screen is visible
    private var once: Job? = null    // a single load asked for while the screen is away

    fun start() {
        if (job?.isActive == true) return
        job = scope.launch {
            while (true) {
                step()
                val refused = when (val s = _state.value) { is Load.Ready -> s.error.isRefused; is Load.Failed -> s.error.isRefused; else -> false }
                val wait = if (refused) 0L else interval()
                if (wait <= 0) kick.receive() else withTimeoutOrNull(wait) { kick.receive() }
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
    }

    /**
     * Load now. A started loader loads at once and keeps its rhythm; a stopped one (its screen is away) loads once and stays
     * stopped — an action finishing after the owner left the screen must not start polling in the background.
     */
    fun refresh() {
        if (job?.isActive == true) kick.trySend(Unit)
        else if (once?.isActive != true) once = scope.launch { step() }
    }

    /** Replace the data without a request (after an action the server has already confirmed). */
    fun set(value: T) {
        _state.value = Load.Ready(value)
    }

    private suspend fun step() {
        _loading.value = true
        try {
            _state.value = Load.Ready(load())
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            val old = _state.value.valueOrNull
            _state.value = if (old != null) Load.Ready(old, e) else Load.Failed(e)
        } finally {
            _loading.value = false
        }
    }
}
