package com.tema.ia.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.graphics.Color
import com.tema.ia.data.ThemeMode

object SenaTheme {
    val colors: SenaColors
        @Composable get() = LocalSenaColors.current
}

/** Muted text on the current surface (captions, time). Cards and the header supply their own. */
val LocalMuted = compositionLocalOf { Color.Unspecified }

/** The color of the high-contrast surface (header, main card) we're standing on; null means a regular sheet. Buttons and fields on it invert their colors. */
val LocalSurface = compositionLocalOf<Color?> { null }

@Composable
fun SenaTheme(mode: ThemeMode, content: @Composable () -> Unit) {
    val dark = when (mode) {
        ThemeMode.System -> isSystemInDarkTheme()
        ThemeMode.Dark -> true
        ThemeMode.Light -> false
    }
    val c = if (dark) DarkColors else LightColors
    val scheme = (if (dark) darkColorScheme() else lightColorScheme()).copy(
        primary = c.ink, onPrimary = c.onInk, primaryContainer = c.ink, onPrimaryContainer = c.onInk,
        secondary = c.chip, onSecondary = c.text, secondaryContainer = c.chip, onSecondaryContainer = c.text,
        tertiary = Sky, onTertiary = OnPastel,
        background = c.sheet, onBackground = c.text,
        surface = c.card, onSurface = c.text,
        surfaceVariant = c.chip, onSurfaceVariant = c.textMuted,
        surfaceContainer = c.card, surfaceContainerHigh = c.card, surfaceContainerHighest = c.card,
        surfaceContainerLow = c.sheet, surfaceContainerLowest = c.sheet,
        inverseSurface = c.ink, inverseOnSurface = c.onInk,
        error = Coral, onError = OnPastel, errorContainer = Coral, onErrorContainer = OnPastel,
        outline = c.outline, outlineVariant = c.cardBorder,
    )
    CompositionLocalProvider(LocalSenaColors provides c, LocalMuted provides c.textMuted) {
        MaterialTheme(colorScheme = scheme, typography = SenaTypography, content = content)
    }
}
