package com.tema.ia.ui.screens.rules

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tema.ia.R
import com.tema.ia.data.RuleEntry
import com.tema.ia.data.SenaApi
import com.tema.ia.ui.Loader
import com.tema.ia.ui.UiText
import com.tema.ia.ui.toUiText
import com.tema.ia.ui.valueOrNull
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class RulesViewModel(private val api: SenaApi) : ViewModel() {
    val loader = Loader(viewModelScope, 10_000L) { api.rules() }

    private val _adding = MutableStateFlow(false)
    val adding: StateFlow<Boolean> = _adding.asStateFlow()
    private val _deleting = MutableStateFlow<RuleEntry?>(null)
    val deleting: StateFlow<RuleEntry?> = _deleting.asStateFlow()
    private val _saving = MutableStateFlow(false)
    val saving: StateFlow<Boolean> = _saving.asStateFlow()

    private val _messages = MutableSharedFlow<UiText>(extraBufferCapacity = 8)
    val messages: SharedFlow<UiText> = _messages.asSharedFlow()

    fun openAdd() { _adding.value = true }
    fun closeAdd() { _adding.value = false }
    fun askDelete(entry: RuleEntry) { _deleting.value = entry }
    fun dismissDelete() { _deleting.value = null }

    private fun act(block: suspend () -> Unit) {
        viewModelScope.launch {
            try {
                block()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(e.toUiText())
            } finally {
                loader.refresh()
            }
        }
    }

    fun add(text: String) {
        if (text.isBlank() || _saving.value) return
        _saving.value = true
        act {
            try {
                api.addRule(text.trim())
                _adding.value = false
                _messages.tryEmit(UiText.Res(R.string.rules_added))
            } finally {
                _saving.value = false
            }
        }
    }

    /** The switch changes the row immediately; if the server refuses, the list refreshes and reverts to how it was. */
    fun toggle(entry: RuleEntry, enabled: Boolean) {
        loader.state.value.valueOrNull?.let { rows ->
            loader.set(rows.map { if (it.kind == entry.kind && it.id == entry.id) it.copy(enabled = enabled) else it })
        }
        act { api.setRuleEnabled(entry, enabled) }
    }

    fun confirmDelete() {
        val entry = _deleting.value ?: return
        _deleting.value = null
        act {
            api.removeRule(entry)
            _messages.tryEmit(UiText.Res(R.string.rules_deleted))
        }
    }
}
