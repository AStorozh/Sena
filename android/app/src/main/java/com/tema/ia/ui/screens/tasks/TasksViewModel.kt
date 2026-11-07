package com.tema.ia.ui.screens.tasks

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tema.ia.R
import com.tema.ia.data.SenaApi
import com.tema.ia.data.TaskItem
import com.tema.ia.data.TasksSnapshot
import com.tema.ia.data.stopRunning
import com.tema.ia.ui.Loader
import com.tema.ia.ui.UiText
import com.tema.ia.ui.toUiText
import com.tema.ia.ui.valueOrNull
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.launch

class TasksViewModel(api: SenaApi) : ViewModel() {
    // while his own task runs the list moves every few seconds; when nothing does — or only Sena's service chores run (the night's
    // cleanup, a review, a long work's turn for hours) — a slower look is enough
    private var running = true
    val loader = Loader(viewModelScope, { if (running) 5_000L else 15_000L }) { api.tasks().also { snapshot -> running = snapshot.active.any { !it.service } } }

    /** Service tasks (cleanup, triage) are hidden by default. */
    fun visible(snapshot: TasksSnapshot, showService: Boolean): Pair<List<TaskItem>, List<TaskItem>> =
        snapshot.active.filter { showService || !it.service } to snapshot.finished.filter { showService || !it.service }

    fun start() = loader.start()
    fun stop() = loader.stop()
}

const val VERDICT_WAIT_MS = 180_000L   // the judge's verdict comes after the task ends (a review of up to a few minutes)

/**
 * How soon to look at a task's page again: every 3 s while it runs; once it ended, every 15 s for a few minutes while the
 * judge's verdict is still on its way (a service chore gets none); after that nothing changes by itself — no polling.
 */
fun pollAfter(task: TaskItem?, endedAtMs: Long?, nowMs: Long): Long = when {
    task == null || task.running -> 3_000L
    task.outcome == null && !task.service && endedAtMs != null && nowMs - endedAtMs < VERDICT_WAIT_MS -> 15_000L
    else -> 0L
}

/**
 * One task: steps, answer, remarks; for a running one, "Stop"; for a finished one, a rating and a remark. [id] is the task's
 * entry in the logs: it finds the task while it runs and after it ends.
 */
class TaskDetailViewModel(
    private val api: SenaApi,
    val id: String,
    private val clock: () -> Long = System::currentTimeMillis,
) : ViewModel() {
    private var shown: TaskItem? = null
    private var endedAtMs: Long? = null   // when this page first saw the task finished (the phone's clock: the Mac's may differ)
    val loader = Loader<TaskItem>(viewModelScope, { pollAfter(shown, endedAtMs, clock()) }) {
        api.task(id).also { t ->
            shown = t
            if (!t.running && endedAtMs == null) endedAtMs = clock()
        }
    }

    private val _messages = MutableSharedFlow<UiText>(extraBufferCapacity = 8)
    val messages: SharedFlow<UiText> = _messages.asSharedFlow()

    private fun act(done: UiText? = null, block: suspend () -> Unit) {
        viewModelScope.launch {
            try {
                block()
                done?.let { _messages.tryEmit(it) }
                loader.refresh()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(e.toUiText())
            }
        }
    }

    /** Stops this very task, as it was seen last: by its entry, or by the thread it runs in for a server that only knows threads. */
    fun stopTask() {
        val task = loader.state.value.valueOrNull?.takeIf { it.running } ?: return
        act { _messages.tryEmit(UiText.Raw(api.stopRunning(task).message)) }
    }

    fun rate(rating: Int) = act(UiText.Res(R.string.task_rated)) { api.rate(id, rating) }

    fun comment(text: String) {
        if (text.isBlank()) return
        act(UiText.Res(R.string.task_comment_sent)) { api.comment(id, text.trim()) }
    }
}
