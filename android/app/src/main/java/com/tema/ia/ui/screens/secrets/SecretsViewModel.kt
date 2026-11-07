package com.tema.ia.ui.screens.secrets

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tema.ia.R
import com.tema.ia.data.ActionResult
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

/** The keeper of secrets: the list never carries values; a value is shown only on the owner's request and lives in memory until the sheet closes. */
class SecretsViewModel(private val api: SenaApi) : ViewModel() {
    val loader = Loader(viewModelScope, 30_000L) { api.secrets() }

    private val _saving = MutableStateFlow(false)
    val saving: StateFlow<Boolean> = _saving.asStateFlow()
    private val _revealed = MutableStateFlow<Pair<String, String>?>(null)   // name -> value, for the open sheet only
    val revealed: StateFlow<Pair<String, String>?> = _revealed.asStateFlow()
    private val _messages = MutableSharedFlow<UiText>(extraBufferCapacity = 8)
    val messages: SharedFlow<UiText> = _messages.asSharedFlow()

    /** A change to the keeper: what the server answers is said as is — "saved" is never guessed; a refusal keeps the sheet open. */
    private fun write(fallback: UiText, block: suspend () -> ActionResult, onDone: () -> Unit) {
        viewModelScope.launch {
            _saving.value = true
            try {
                val result = block()
                onDone()
                _messages.tryEmit(result.message.takeIf { it.isNotBlank() }?.let { UiText.Raw(it) } ?: fallback)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(e.toUiText())
            } finally {
                _saving.value = false
                loader.refresh()
            }
        }
    }

    /** [allow] goes as it is: an empty list takes the secret away from every agent. */
    fun save(name: String, value: String, note: String, allow: List<String>, reveal: Boolean, sites: String, onDone: () -> Unit = {}) {
        if (name.isBlank()) return
        write(UiText.Res(R.string.secrets_saved), { api.secretPut(name.trim(), value, note.trim(), allow, if (reveal) "confirm" else "never", siteList(sites)) }, onDone)
    }

    fun remove(name: String, onDone: () -> Unit = {}) {
        write(UiText.Res(R.string.secrets_removed), { api.secretRemove(name) }, onDone)
    }

    /** The value itself: asked for on purpose, kept only while the sheet is open. */
    fun reveal(name: String) {
        viewModelScope.launch {
            try {
                _revealed.value = name to api.secretReveal(name)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(e.toUiText())
            }
        }
    }

    fun hide() { _revealed.value = null }
}

/** The sites typed by hand, one host each: the server strips an address down to its host. */
fun siteList(text: String): List<String> = text.split(',', ';', ' ', '\n').map { it.trim() }.filter { it.isNotEmpty() }

/** The keeper takes a value of at least this many characters (its own rule, said in the field's hint). */
const val SECRET_MIN_LENGTH = 8

/** Whether Save can be pressed: a name, and a value long enough; editing may leave the value empty — the current one stays. */
fun canSaveSecret(name: String, value: String, editing: Boolean): Boolean =
    name.isNotBlank() && (value.length >= SECRET_MIN_LENGTH || (editing && value.isEmpty()))
