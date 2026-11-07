package com.tema.ia.ui.screens.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tema.ia.R
import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import com.tema.ia.data.ChatMessage
import com.tema.ia.data.SenaApi
import com.tema.ia.data.stopRunning
import com.tema.ia.ui.Load
import com.tema.ia.ui.Loader
import com.tema.ia.ui.UiText
import com.tema.ia.ui.toUiText
import com.tema.ia.ui.valueOrNull
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * A chat bubble. [error] is a task failure, shown with a "Retry" button when [retryText] says what to resend — only when
 * resending can't run the request twice (a refusal, a task the server reports failed or doesn't know). [asked] — the words the
 * error is about: once the history holds them answered, the error is stale and goes.
 * [after] — for the owner's own message not yet in the history: the newest moment the history had when it was sent; the server's
 * copy of it comes later than that, an older message with the same words doesn't count.
 */
data class Bubble(
    val id: String, val fromUser: Boolean, val text: String, val time: Double,
    val error: UiText? = null, val retryText: String? = null, val after: Double = 0.0, val asked: String? = null,
)

data class ChatUi(
    val load: Load<List<ChatMessage>>,
    val bubbles: List<Bubble>,
    val working: Boolean,
    val step: String?,
    val confirmClear: Boolean,
    val offline: Boolean = false,   // the request is on the Mac, but the phone has lost it for now: the answer will still come here
)

/** The message being answered now: its words, when it left (the phone's clock), and the task's names once the server gives them. */
private data class Asked(val text: String, val sentAtMs: Long, val tid: String? = null, val thread: String? = null)

const val CHAT_WAIT_MS = 15 * 60_000L   // a request is watched this long; past it the answer still comes into the conversation by itself
const val CHAT_BACKOFF_MS = 10_000L     // the slowest look at a request while the connection is gone

class ChatViewModel(
    private val api: SenaApi,
    private val pollMs: Long = 700,
    private val clock: () -> Long = System::currentTimeMillis,
    private val open: StateFlow<Boolean> = MutableStateFlow(true),   // the app's lock is open (AppContainer.unlocked): behind it nothing is polled
) : ViewModel() {
    /** The request being answered: whether one is out, its latest step, whether the connection to it is lost for now. */
    private data class Progress(val working: Boolean = false, val step: String? = null, val offline: Boolean = false)

    private val progress = MutableStateFlow(Progress())
    // the conversation: every 5 s while an answer is awaited, every 10 s otherwise (other channels and Sena herself write here too)
    val loader = Loader(viewModelScope, { if (progress.value.working) 5_000L else 10_000L }) { api.history() }

    private val local = MutableStateFlow<List<Bubble>>(emptyList())
    private val confirmClear = MutableStateFlow(false)
    private var asked: Asked? = null
    private var counter = 0

    private val _messages = MutableSharedFlow<UiText>(extraBufferCapacity = 8)
    val messages: SharedFlow<UiText> = _messages.asSharedFlow()

    val ui: StateFlow<ChatUi> = combine(loader.state, local, progress, confirmClear) { load, pending, now, ask ->
        val history = (load as? Load.Ready)?.value.orEmpty()
        val saved = history.map { Bubble(it.id, it.fromUser, it.text, it.time) }
        // our own bubble disappears once the server's copy of that message has arrived; an error, once the history has the answer
        ChatUi(load, saved + pending.filter { !it.confirmedBy(history) && !it.answeredIn(history) }, now.working, now.step, ask, now.offline)
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), ChatUi(Load.Loading, emptyList(), false, null, false))

    private fun Bubble.confirmedBy(history: List<ChatMessage>) = error == null && history.any { it.fromUser && it.text == text && it.time > after }

    /** An error about words the history now holds with an answer after them: the request got through after all. */
    private fun Bubble.answeredIn(history: List<ChatMessage>): Boolean {
        val words = asked ?: return false
        val mine = history.indexOfFirst { it.fromUser && it.text == words && it.time > after }
        return mine >= 0 && history.drop(mine + 1).any { !it.fromUser }
    }

    fun start() = loader.start()
    fun stop() = loader.stop()
    fun refresh() = loader.refresh()

    fun send(raw: String) {
        val text = raw.trim()
        if (text.isEmpty() || progress.value.working) return
        val loaded = loader.state.value.valueOrNull
        val history = loaded.orEmpty()
        // the newest moment the history has (an empty one has nothing to mistake for ours); when it hasn't loaded yet,
        // a minute back on the phone's clock — the Mac keeps the same time
        val after = if (loaded != null) history.maxOfOrNull { it.time } ?: 0.0 else clock() / 1000.0 - 60
        val mine = Bubble("local-${counter++}", true, text, clock() / 1000.0, after = after)
        // what the history already has and the old failures go: the list holds only what isn't confirmed yet
        local.value = local.value.filter { it.error == null && !it.confirmedBy(history) } + mine
        progress.value = Progress(working = true)
        asked = Asked(text, clock())
        viewModelScope.launch {
            try {
                val id = try {
                    api.ask(text)
                } catch (e: ApiException) {
                    // the message left and no answer came in time: the Mac may well be at it — no "Retry", it could run twice
                    if (!e.timedOut) throw e
                    local.value = local.value + lostBubble(text, after)
                    return@launch
                }
                watch(id, text, after)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                local.value = local.value + errorBubble(e.toUiText(), text, after)
            } finally {
                progress.value = Progress()
                asked = null
                loader.refresh()
            }
        }
    }

    /**
     * Follows a request to its answer. Losing the connection doesn't end it — the task goes on on the Mac — so the watch keeps
     * the request's id and looks again, slower and slower, saying the answer will still come here; behind the closed lock it
     * waits. Only the server's own word ends it early: the task failed, or it doesn't know it (then "Retry" is safe).
     */
    private suspend fun watch(id: String, text: String, after: Double) {
        val until = clock() + CHAT_WAIT_MS
        var wait = pollMs
        while (true) {
            delay(wait)
            open.first { it }
            if (clock() > until) {
                _messages.tryEmit(UiText.Res(R.string.chat_long_wait))
                return
            }
            val status = try {
                api.askStatus(id)
            } catch (e: ApiException) {
                if (e.kind != ApiError.NoServer) throw e
                progress.update { it.copy(offline = true) }
                wait = (wait * 2).coerceIn(2_000L, CHAT_BACKOFF_MS)
                continue
            }
            wait = pollMs
            asked = asked?.copy(tid = status.tid ?: asked?.tid, thread = status.thread ?: asked?.thread)
            progress.value = Progress(working = true, step = status.steps.lastOrNull())
            if (status.done) {
                if (status.failed) local.value = local.value + errorBubble(UiText.Raw(status.answer.orEmpty()), text, after)
                return
            }
        }
    }

    private fun errorBubble(error: UiText, retry: String, after: Double) =
        Bubble("local-${counter++}", false, "", clock() / 1000.0, error = error, retryText = retry, after = after, asked = retry)

    /** The phone lost a message on its way: it may have reached the Mac, so no "Retry" — the answer, if any, replaces this. */
    private fun lostBubble(text: String, after: Double) =
        Bubble("local-${counter++}", false, "", clock() / 1000.0, error = UiText.Res(R.string.chat_lost_body), after = after, asked = text)

    fun retry(bubble: Bubble) {
        val text = bubble.retryText ?: return
        // the failed attempt (our bubble plus the error) is removed, a new one takes its place
        local.value = local.value.filterNot { it.id == bubble.id || (it.fromUser && it.text == text && it.id.startsWith("local-")) }
        send(text)
    }

    /**
     * "Stop": the task answering the message being sent now, and nothing else. The server names it in the task's status; an
     * older server doesn't, and then it is the one running task with exactly these words that started after they were sent.
     */
    fun stopTask() {
        val current = asked ?: return
        viewModelScope.launch {
            try {
                val result = if (current.tid != null || current.thread != null) api.stopRunning(current.tid, current.thread)
                else {
                    val snapshot = api.tasks()
                    val age = (clock() - current.sentAtMs) / 1000.0 + 5   // a margin for the two clocks' rhythm
                    val ours = snapshot.active.filter { !it.service && it.text == current.text && snapshot.now - it.start <= age }
                    ours.singleOrNull()?.let { api.stopRunning(it) }
                }
                when {
                    result == null -> _messages.tryEmit(UiText.Res(R.string.chat_stop_not_found))
                    result.message.isNotBlank() -> _messages.tryEmit(UiText.Raw(result.message))
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(e.toUiText())
            }
        }
    }

    fun askClear() { confirmClear.value = true }
    fun dismissClear() { confirmClear.value = false }

    fun clear() {
        confirmClear.value = false
        viewModelScope.launch {
            try {
                api.clearHistory()
                local.value = emptyList()
                _messages.tryEmit(UiText.Res(R.string.chat_cleared))
                loader.refresh()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(e.toUiText())
            }
        }
    }
}
