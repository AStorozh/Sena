package com.tema.ia.ui.screens.connect

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tema.ia.R
import com.tema.ia.data.Address
import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import com.tema.ia.data.Connection
import com.tema.ia.data.Store
import com.tema.ia.ui.UiText
import com.tema.ia.ui.toUiText
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

sealed interface CheckState {
    data object Idle : CheckState
    data object Checking : CheckState
    data class Failed(val text: UiText) : CheckState
}

data class ConnectUi(
    val address: String,
    val token: String,
    val check: CheckState,
    val addressError: UiText?,
    val tokenError: UiText?,
)

/**
 * The address survives the process being killed (saved state); the password doesn't: the saved state is kept by the system,
 * outside the app's protected store (Н-4), so a rotation keeps it in this ViewModel and a killed process asks for it again.
 */
class ConnectViewModel(
    private val state: SavedStateHandle,
    private val store: Store,
    private val check: suspend (Connection) -> Unit,
    initialAddress: String = "",
) : ViewModel() {
    private val _ui = MutableStateFlow(ConnectUi(state["address"] ?: initialAddress, "", CheckState.Idle, null, null))
    val ui: StateFlow<ConnectUi> = _ui.asStateFlow()

    fun onAddress(value: String) {
        state["address"] = value
        _ui.value = _ui.value.copy(address = value, addressError = null, check = CheckState.Idle)
    }

    fun onToken(value: String) {
        _ui.value = _ui.value.copy(token = value, tokenError = null, check = CheckState.Idle)
    }

    /** Checks the server and the password, and only then saves. */
    fun submit() {
        val current = _ui.value
        if (current.check == CheckState.Checking) return
        val base = when (val parsed = Address.parse(current.address)) {
            is Address.Result.Ok -> parsed.baseUrl
            Address.Result.Empty -> return fail(address = UiText.Res(R.string.connect_empty_address))
            Address.Result.Invalid -> return fail(address = UiText.Res(R.string.connect_bad_address))
            Address.Result.NeedsHttps -> return fail(address = UiText.Res(R.string.err_cleartext))
        }
        val token = current.token.trim()
        if (token.isEmpty()) return fail(token = UiText.Res(R.string.connect_empty_token))
        _ui.value = current.copy(check = CheckState.Checking, addressError = null, tokenError = null)
        viewModelScope.launch {
            try {
                val connection = Connection(base, token)
                check(connection)
                store.saveConnection(connection)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val wrongToken = (e as? ApiException)?.kind == ApiError.BadToken
                _ui.value = _ui.value.copy(check = CheckState.Failed(e.toUiText()), tokenError = if (wrongToken) e.toUiText() else null)
            }
        }
    }

    fun demo() {
        viewModelScope.launch { store.setDemo(true) }
    }

    private fun fail(address: UiText? = null, token: UiText? = null) {
        _ui.value = _ui.value.copy(addressError = address, tokenError = token, check = CheckState.Idle)
    }
}
