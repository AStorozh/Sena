package com.tema.ia

import com.tema.ia.data.Connection
import com.tema.ia.data.DemoApi
import com.tema.ia.data.HttpSenaApi
import com.tema.ia.data.SenaApi
import com.tema.ia.data.Store
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn

/** What the app is currently working with. */
sealed interface Session {
    data object Loading : Session
    data object NeedsConnection : Session
    /** [id] changes on every new connection: screens start with a clean slate. */
    data class Active(val id: Int, val api: SenaApi, val demo: Boolean, val address: String) : Session
}

/** Manual dependency wiring: the store, the current connection to the server and whether the app's lock is open. */
class AppContainer(
    val store: Store,
    scope: CoroutineScope,
    private val demoFactory: () -> SenaApi = { DemoApi(rich = true) },
    private val httpFactory: (Connection) -> SenaApi = { HttpSenaApi(it.baseUrl, it.token) },
) {
    private var counter = 0

    /**
     * The lock is open: lives for the process, not the Activity — a rotation keeps it, a fresh process always starts locked.
     * Going to the background closes it again (ui/AppLock.kt). On-device tests of the screens open it before they start.
     */
    val unlocked = MutableStateFlow(false)

    val session: StateFlow<Session> = combine(store.connection, store.demo) { connection, demo -> connection to demo }
        .distinctUntilChanged()
        .map { (connection, demo) ->
            when {
                demo -> Session.Active(++counter, demoFactory(), demo = true, address = "")
                connection != null -> Session.Active(++counter, httpFactory(connection), demo = false, address = connection.baseUrl)
                else -> Session.NeedsConnection
            }
        }
        .stateIn(scope, SharingStarted.Eagerly, Session.Loading)

    fun checkConnection(): suspend (Connection) -> Unit = { httpFactory(it).overview() }
}
