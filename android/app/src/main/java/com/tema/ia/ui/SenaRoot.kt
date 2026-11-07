package com.tema.ia.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.createSavedStateHandle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.tema.ia.AppContainer
import com.tema.ia.Session
import com.tema.ia.data.ThemeMode
import com.tema.ia.ui.screens.connect.ConnectScreen
import com.tema.ia.ui.screens.connect.ConnectViewModel
import com.tema.ia.ui.theme.SenaTheme

/** App entry point: theme first, then either "Connection" or the shell with its sections. */
@Composable
fun SenaRoot(container: AppContainer, onDarkChanged: (Boolean) -> Unit = {}) {
    val mode by container.store.theme.collectAsStateWithLifecycle(ThemeMode.System)
    val systemDark = isSystemInDarkTheme()
    val dark = when (mode) { ThemeMode.System -> systemDark; ThemeMode.Dark -> true; ThemeMode.Light -> false }
    LaunchedEffect(dark) { onDarkChanged(dark) }
    SenaTheme(mode) {
        AppLockGate(container.store, container.unlocked) {
            val session by container.session.collectAsStateWithLifecycle()
            when (val s = session) {
                Session.Loading -> Box(Modifier.fillMaxSize().background(SenaTheme.colors.header))
                Session.NeedsConnection -> {
                    val vm: ConnectViewModel = viewModel(factory = viewModelFactory {
                        initializer { ConnectViewModel(createSavedStateHandle(), container.store, container.checkConnection()) }
                    })
                    ConnectScreen(vm)
                }
                is Session.Active -> AppShell(container, s)
            }
        }
    }
}
