package com.tema.ia.ui.screens.integrations

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tema.ia.R
import com.tema.ia.data.IntegrationCatalog
import com.tema.ia.data.IntegrationState
import com.tema.ia.data.SenaApi
import com.tema.ia.ui.Loader
import com.tema.ia.ui.UiText
import com.tema.ia.ui.toUiText
import com.tema.ia.ui.valueOrNull
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class IntegrationsData(val states: Map<String, IntegrationState>, val keys: Map<String, Boolean>)

enum class Link { Working, Disabled, NoKey, Unused, Problem, Part, NotInstalled }

/**
 * What an integration looks like: whether it's disabled, whether what it needs is on the Mac (the server's "ready": Claude Code,
 * Luma, the home models, the hearing), whether there are enough keys — a missing key of something nothing uses now (the server's
 * "needed") is no fault — and whether there were errors today.
 */
fun linkOf(spec: IntegrationCatalog.Spec, state: IntegrationState?, keys: Map<String, Boolean>): Link {
    if (state?.enabled == false) return Link.Disabled
    if (state?.ready == false) return Link.NotInstalled
    val known = spec.keys.filter { it in keys }
    if (known.size == spec.keys.size && spec.keys.isNotEmpty()) {
        val ok = if (spec.anyKey) known.any { keys[it] == true } else known.all { keys[it] == true }
        if (!ok) return if (state?.needed ?: !spec.optional) Link.NoKey else Link.Unused
    }
    if (state != null && state.errorsToday > 0 && state.lastError != null) return Link.Problem
    return if (state?.enabled == null && state != null && !state.canToggle) Link.Part else Link.Working
}

class IntegrationsViewModel(private val api: SenaApi) : ViewModel() {
    // the keys come with the settings (light), not the whole overview of the Mac every few seconds
    val loader = Loader(viewModelScope, 10_000L) {
        coroutineScope {
            val states = async { api.integrations() }
            val keys = async { runCatching { api.config().keys }.getOrDefault(emptyMap()) }
            IntegrationsData(states.await(), keys.await())
        }
    }

    private val _selected = MutableStateFlow<String?>(null)
    val selected: StateFlow<String?> = _selected.asStateFlow()
    private val _messages = MutableSharedFlow<UiText>(extraBufferCapacity = 8)
    val messages: SharedFlow<UiText> = _messages.asSharedFlow()

    fun open(id: String) { _selected.value = id }
    fun close() { _selected.value = null }

    /**
     * The switch changes the row immediately; a server failure reverts it to the previous state. One that needs Sena's restart
     * (the Telegram account) says so: switched on, it connects only at her next start.
     */
    fun toggle(id: String, on: Boolean) {
        val restart = loader.state.value.valueOrNull?.states?.get(id)?.restart == true
        loader.state.value.valueOrNull?.let { data ->
            data.states[id]?.let { loader.set(data.copy(states = data.states + (id to it.copy(enabled = on)))) }
        }
        viewModelScope.launch {
            try {
                api.setIntegration(id, on)
                _messages.tryEmit(UiText.Res(if (!on) R.string.int_toggled_off else if (restart) R.string.int_toggled_on_restart else R.string.int_toggled_on))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(e.toUiText())
            } finally {
                loader.refresh()
            }
        }
    }
}
