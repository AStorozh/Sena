package com.tema.ia.ui.screens.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tema.ia.R
import com.tema.ia.data.SenaApi
import com.tema.ia.data.Store
import com.tema.ia.data.ThemeMode
import com.tema.ia.ui.Loader
import com.tema.ia.ui.UiText
import com.tema.ia.ui.toUiText
import com.tema.ia.ui.valueOrNull
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive

class SettingsViewModel(private val api: SenaApi, private val store: Store) : ViewModel() {
    val loader = Loader(viewModelScope, 0L) { api.config() }

    val theme: StateFlow<ThemeMode> = store.theme.stateIn(viewModelScope, SharingStarted.Eagerly, ThemeMode.System)
    val biometricEnabled: StateFlow<Boolean> = store.biometricEnabled.stateIn(viewModelScope, SharingStarted.Eagerly, false)

    private val _confirmDisconnect = MutableStateFlow(false)
    val confirmDisconnect: StateFlow<Boolean> = _confirmDisconnect.asStateFlow()
    private val _messages = MutableSharedFlow<UiText>(extraBufferCapacity = 8)
    val messages: SharedFlow<UiText> = _messages.asSharedFlow()

    fun setTheme(mode: ThemeMode) { viewModelScope.launch { store.setTheme(mode) } }

    /** Called only after the owner has actually confirmed with their finger/face — turning the switch on is the proof, not just tapping it. */
    fun setBiometricEnabled(on: Boolean) { viewModelScope.launch { store.setBiometricEnabled(on) } }

    fun askDisconnect() { _confirmDisconnect.value = true }
    fun dismissDisconnect() { _confirmDisconnect.value = false }

    /** Signing out of the demo and out of a real connection work the same: the address and password are forgotten, "Connection" opens. */
    fun disconnect() {
        _confirmDisconnect.value = false
        viewModelScope.launch { store.clearConnection() }
    }

    /** Saved right away; whatever the server rejects is reverted with an explanation. */
    fun change(key: String, value: JsonElement) {
        val current = loader.state.value.valueOrNull ?: return
        loader.set(current.copy(settings = apply(current.settings, key, value)))
        viewModelScope.launch {
            try {
                val result = api.updateSettings(mapOf(key to value))
                loader.set(current.copy(settings = result.settings))
                _messages.tryEmit(
                    if (result.rejected.isEmpty()) UiText.Res(R.string.settings_saved)
                    else UiText.Res(R.string.settings_rejected, listOf(result.rejected.joinToString("; "))),
                )
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                loader.set(current)
                _messages.tryEmit(e.toUiText())
            }
        }
    }

    fun change(key: String, value: Boolean) = change(key, JsonPrimitive(value))
    fun change(key: String, value: String) = change(key, JsonPrimitive(value))
    fun change(key: String, value: Int) = change(key, JsonPrimitive(value))
    fun change(key: String, value: Double) = change(key, JsonPrimitive(value))

    private fun apply(s: com.tema.ia.data.ServerSettings, key: String, v: JsonElement): com.tema.ia.data.ServerSettings {
        val p = v as? JsonPrimitive ?: return s
        return when (key) {
            "verbosity" -> s.copy(verbosity = p.content)
            "effort" -> s.copy(effort = p.content)
            "budget_usd_day" -> s.copy(budgetUsdDay = p.content.toDoubleOrNull() ?: s.budgetUsdDay)
            "task_budget_usd" -> s.copy(taskBudgetUsd = p.content.toDoubleOrNull() ?: s.taskBudgetUsd)
            "heartbeat" -> s.copy(heartbeat = p.content == "true")
            "telegram_notify" -> s.copy(telegramNotify = p.content == "true")
            "tidy" -> s.copy(tidy = p.content == "true")
            "weekly" -> s.copy(weekly = p.content == "true")
            "voice_reply" -> s.copy(voiceReply = p.content == "true")
            else -> s
        }
    }
}

/** The budget as typed: "2.5" or "2,5" is two and a half dollars; anything else isn't a sum. */
fun budgetOf(text: String): Double? = text.trim().replace(',', '.').toDoubleOrNull()?.takeIf { it >= 0 && !it.isInfinite() }
