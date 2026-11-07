package com.tema.ia

import com.tema.ia.ui.Loader
import androidx.lifecycle.viewmodel.initializer
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope

/** Polling never ends on its own: without stopping it, runTest would wait forever. We stop it once the test is done. */
@OptIn(ExperimentalCoroutinesApi::class)
fun TestScope.autoStop(vararg loaders: Loader<*>) {
    backgroundScope.launch(start = CoroutineStart.UNDISPATCHED) {
        try {
            awaitCancellation()
        } finally {
            loaders.forEach { it.stop() }
        }
    }
}

/** Subscribes to the flow right away (without it, stateIn(WhileSubscribed) never starts working). */
fun <T> TestScope.watch(flow: kotlinx.coroutines.flow.Flow<T>, into: (T) -> Unit = {}) {
    backgroundScope.launch(start = CoroutineStart.UNDISPATCHED) { flow.collect { into(it) } }
}

/** A ViewModel that gets cleared like a real one at the end of the test: its viewModelScope is cancelled and all tasks in it finish. */
inline fun <reified T : androidx.lifecycle.ViewModel> TestScope.viewModelOf(crossinline create: () -> T): T {
    val store = androidx.lifecycle.ViewModelStore()
    val vm = androidx.lifecycle.ViewModelProvider.create(
        store, androidx.lifecycle.viewmodel.viewModelFactory { initializer { create() } },
    )[T::class]
    backgroundScope.launch(start = CoroutineStart.UNDISPATCHED) {
        try { awaitCancellation() } finally { store.clear() }
    }
    return vm
}
