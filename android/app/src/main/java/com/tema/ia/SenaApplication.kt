package com.tema.ia

import android.app.Application
import android.content.Context
import androidx.datastore.preferences.preferencesDataStore
import com.tema.ia.data.Store
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

private val Context.settingsStore by preferencesDataStore("sena")

class SenaApplication : Application() {
    val container: AppContainer by lazy {
        AppContainer(Store(settingsStore), CoroutineScope(SupervisorJob() + Dispatchers.Default))
    }
}
