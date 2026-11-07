package com.tema.ia.ui.screens.analytics

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tema.ia.data.CostsReport
import com.tema.ia.data.HomeReport
import com.tema.ia.data.QualityReport
import com.tema.ia.data.RegistryStats
import com.tema.ia.data.SenaApi
import com.tema.ia.ui.Loader
import com.tema.ia.ui.UiText
import com.tema.ia.ui.toUiText
import com.tema.ia.ui.valueOrNull
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.launch

/** Spending, quality, the registry's size and the home/cloud split are fetched separately (independent loaders), so a slow
 * one never blocks another's section from loading and showing — otherwise one stuck request would freeze the whole screen's refresh. */
class AnalyticsViewModel(private val api: SenaApi) : ViewModel() {
    val costs = Loader<CostsReport>(viewModelScope, 15_000L) { api.costsReport("week") }
    val quality = Loader<QualityReport>(viewModelScope, 15_000L) { api.quality(7) }
    val registry = Loader<RegistryStats>(viewModelScope, 60_000L) { api.registry() }   // the numbers move slowly: once a minute is plenty
    val home = Loader<HomeReport>(viewModelScope, 30_000L) { api.home() }

    private val _messages = MutableSharedFlow<UiText>(extraBufferCapacity = 4)
    val messages: SharedFlow<UiText> = _messages.asSharedFlow()

    /** Move a job home or to the cloud. The switch moves at once; if the server refuses, the row goes back. */
    fun move(role: String, home: Boolean) {
        val place = if (home) "home" else "cloud"
        this.home.state.value.valueOrNull?.let { report ->
            this.home.set(report.copy(roles = report.roles.map { if (it.role == role) it.copy(place = place) else it }))
        }
        viewModelScope.launch {
            try {
                _messages.tryEmit(UiText.Raw(api.moveRole(role, place).message))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(e.toUiText())
            }
            this@AnalyticsViewModel.home.refresh()
        }
    }
}
