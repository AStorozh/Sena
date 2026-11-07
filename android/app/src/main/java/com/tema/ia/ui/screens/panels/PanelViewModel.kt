package com.tema.ia.ui.screens.panels

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tema.ia.R
import com.tema.ia.data.ApiException
import com.tema.ia.data.Panel
import com.tema.ia.data.PanelAction
import com.tema.ia.data.PanelInfo
import com.tema.ia.data.SenaApi
import com.tema.ia.ui.Loader
import com.tema.ia.ui.UiText
import com.tema.ia.ui.toUiText
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

/** The list of panels the server offers (the More screen): titles and how many need attention. */
class PanelsViewModel(private val api: SenaApi) : ViewModel() {
    val loader = Loader<List<PanelInfo>>(viewModelScope, 30_000L) { api.panels() }
}

/**
 * One panel the server describes. The app has no logic of its own here: it shows the blocks, sends the owner's action with
 * what he typed, and draws the panel the server returns — so a new thing Sena can do needs no new code in the app.
 */
class PanelViewModel(private val api: SenaApi, val id: String) : ViewModel() {
    val loader = Loader<Panel>(viewModelScope, 15_000L) { api.panel(id) }

    private val _busy = MutableStateFlow<String?>(null)   // the action being done: its button shows a spinner
    val busy: StateFlow<String?> = _busy.asStateFlow()
    private val _messages = MutableSharedFlow<UiText>(extraBufferCapacity = 8)
    val messages: SharedFlow<UiText> = _messages.asSharedFlow()

    /**
     * [onDone] runs only once the server took the action (a form clears its fields then, not before: a refusal keeps what was typed).
     * No answer in time means the Mac is still at it — a confirmed task for an agent can take minutes — not that it is gone.
     */
    fun act(action: PanelAction, values: JsonObject = JsonObject(emptyMap()), onDone: () -> Unit = {}) {
        if (_busy.value != null) return
        _busy.value = action.id + action.args
        viewModelScope.launch {
            try {
                val result = api.panelAct(id, action, values)
                loader.set(result.panel)   // the server's answer is the panel as it is now: no second request
                if (result.message.isNotBlank()) _messages.tryEmit(UiText.Raw(result.message))
                onDone()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(if ((e as? ApiException)?.timedOut == true) UiText.Res(R.string.panel_still_running) else e.toUiText())
                loader.refresh()
            } finally {
                _busy.value = null
            }
        }
    }
}
