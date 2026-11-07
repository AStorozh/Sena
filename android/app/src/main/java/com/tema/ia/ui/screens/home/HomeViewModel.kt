package com.tema.ia.ui.screens.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tema.ia.R
import com.tema.ia.data.ActionResult
import com.tema.ia.data.ApiException
import com.tema.ia.data.Overview
import com.tema.ia.data.SenaApi
import com.tema.ia.ui.Load
import com.tema.ia.ui.Loader
import com.tema.ia.ui.UiText
import com.tema.ia.ui.isOffline
import com.tema.ia.ui.toUiText
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.launch

enum class Power { Unknown, On, Off, Starting, Stopping, Offline }

data class HomeUi(
    val load: Load<Overview>,
    val power: Power,
    val busy: Boolean,
    val confirm: HomeConfirm?,
)

enum class HomeConfirm { TurnOff, StopAll }

class HomeViewModel(
    private val api: SenaApi,
    private val startTimeoutMs: Long = 60_000,
    private val stopTimeoutMs: Long = 40_000,
) : ViewModel() {
    private val transition = MutableStateFlow<Power?>(null)   // Starting / Stopping, until the server confirms
    private val busy = MutableStateFlow(false)
    private val confirm = MutableStateFlow<HomeConfirm?>(null)

    val loader = Loader(viewModelScope, { if (transition.value != null) 2_000L else 5_000L }) { api.overview() }

    private val _messages = MutableSharedFlow<UiText>(extraBufferCapacity = 8)
    val messages: SharedFlow<UiText> = _messages.asSharedFlow()

    /** The picture of the question on screen (a captcha, "which one of these"): the question's id and the image bytes. */
    private val _image = MutableStateFlow<Pair<String, ByteArray>?>(null)
    val image: StateFlow<Pair<String, ByteArray>?> = _image.asStateFlow()
    private var imageJob: Job? = null

    val ui: StateFlow<HomeUi> = combine(loader.state, transition, busy, confirm) { load, trans, working, ask ->
        HomeUi(load, powerOf(load, trans), working, ask)
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), HomeUi(Load.Loading, Power.Unknown, false, null))

    init {
        viewModelScope.launch {
            loader.state.collect { load ->
                val ready = (load as? Load.Ready)?.takeIf { it.error == null }?.value ?: return@collect
                // The transition is over once the server itself confirms the target state
                when (transition.value) {
                    Power.Starting -> if (ready.coreAlive) transition.value = null
                    Power.Stopping -> if (!ready.coreAlive) transition.value = null
                    else -> Unit
                }
                val question = ready.question
                if (question == null || !question.hasImage) _image.value = null
                else if (_image.value?.first != question.id && imageJob?.isActive != true) imageJob = viewModelScope.launch { loadImage(question.id) }
            }
        }
    }

    private suspend fun loadImage(id: String) {
        try {
            api.questionImage(id)?.let { _image.value = id to it }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // the next look at the overview tries again; the question itself is readable without the picture
        }
    }

    private fun powerOf(load: Load<Overview>, trans: Power?): Power = when {
        trans != null -> trans
        load is Load.Loading -> Power.Unknown
        load is Load.Failed -> Power.Offline
        load is Load.Ready && load.error.isOffline -> Power.Offline
        load is Load.Ready -> if (load.value.coreAlive) Power.On else Power.Off
        else -> Power.Unknown
    }

    fun start() = loader.start()
    fun stop() = loader.stop()
    fun refresh() = loader.refresh()

    /** Power button: if off, turn on right away; if on, ask first. */
    fun onPower() {
        when (ui.value.power) {
            Power.Off -> run({ api.coreStart() }) { if (it.handover) enterTransition(Power.Starting, startTimeoutMs) }
            Power.On -> confirm.value = HomeConfirm.TurnOff
            else -> Unit
        }
    }

    fun onStopAll() { confirm.value = HomeConfirm.StopAll }
    fun dismissConfirm() { confirm.value = null }

    fun confirmed() {
        val what = confirm.value ?: return
        confirm.value = null
        when (what) {
            HomeConfirm.TurnOff -> run({ api.coreStop() }) { if (it.handover) enterTransition(Power.Stopping, stopTimeoutMs) }
            HomeConfirm.StopAll -> run({ api.stopAll() })
        }
    }

    /** The answer goes to the question on screen ([id]): two agents may be asking, and the oldest isn't always the one he read. */
    fun answer(text: String, id: String? = null) { if (text.isNotBlank()) run({ api.answer(text.trim(), id) }) }

    /**
     * "Yes": the server does the thing right away, and some things take minutes (a task for the computer agent, Claude Code).
     * No answer in time then means "still at work", not "the Mac is gone" — pressing again would do it twice.
     */
    fun confirmSend(id: String) { run({ api.confirmSend(id) }, said = true, stillRunning = UiText.Res(R.string.home_send_running)) }
    fun cancelSend(id: String) { run({ api.cancelSend(id) }, said = true) }

    private fun enterTransition(kind: Power, timeoutMs: Long) {
        transition.value = kind
        viewModelScope.launch {
            delay(timeoutMs)
            if (transition.value == kind) {
                transition.value = null
                _messages.tryEmit(UiText.Res(R.string.home_wait_timeout))
            }
        }
    }

    /**
     * A server action: buttons are busy while it runs; an error becomes a message, success immediately refreshes the screen.
     * [said] — the server's own answer is shown; [stillRunning] — what a timeout means for this action, if not "no connection".
     */
    private fun run(block: suspend () -> ActionResult, said: Boolean = false, stillRunning: UiText? = null, onSuccess: (ActionResult) -> Unit = {}) {
        viewModelScope.launch {
            busy.value = true
            try {
                val result = block()
                if (said && result.message.isNotBlank()) _messages.tryEmit(UiText.Raw(result.message))
                onSuccess(result)
                loader.refresh()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val running = stillRunning?.takeIf { (e as? ApiException)?.timedOut == true }
                _messages.tryEmit(running ?: e.toUiText())
                if (running != null) loader.refresh()
            } finally {
                busy.value = false
            }
        }
    }
}
