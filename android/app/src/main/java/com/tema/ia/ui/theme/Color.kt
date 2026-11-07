package com.tema.ia.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

// All of the app's colors live here. Screens write SenaTheme.colors.…, never Color(0x…).
// Same visual language as the Mac app: a dark header, a light sheet, black "pills", pastel only as an accent.

/** The Mac app's pastel palette: for icons, status dots, and labels. Text and icons on it are always [OnPastel]. */
val Mustard = Color(0xFFF5BE3B)
val Coral = Color(0xFFF26B5B)
val Lime = Color(0xFF8FCC47)
val Sky = Color(0xFF7DB2FA)
val Teal = Color(0xFF4FC7B5)
val Lavender = Color(0xFFA99BF2)
val OnPastel = Color(0xFF161617)

private val tints = listOf(Mustard, Sky, Coral, Lime, Lavender, Teal)

@Immutable
class SenaColors(
    val dark: Boolean,
    /** The dark bar at the top (the screen header): dark in both themes. */
    val header: Color,
    val onHeader: Color,
    val onHeaderMuted: Color,
    /** The light sheet under the header (graphite in dark theme) and the white cards on it. */
    val sheet: Color,
    val card: Color,
    val cardBorder: Color,
    val text: Color,
    val textMuted: Color,
    /** Ink: primary buttons, the selected tab, the bottom menu. Light in dark theme. */
    val ink: Color,
    val onInk: Color,
    /** The high-contrast card: the screen's main block. Its theme is inverted inside. */
    val lead: Color,
    val onLead: Color,
    val onLeadMuted: Color,
    /** The soft fill of a chip, a switch, and an inactive track. */
    val chip: Color,
    val outline: Color,
    /** Status words directly on white: pastel is too pale for text, so these have their own tones. */
    val okText: Color,
    val warnText: Color,
    val badText: Color,
) {
    /** Pastel in rotation: neighboring icons get different colors. */
    fun tint(index: Int): Color = tints[Math.floorMod(index, tints.size)]

    /** A stable color by key (a name, a record type): the same key is always the same color. */
    fun tintOf(key: String): Color = tint(key.fold(0) { acc, ch -> acc * 31 + ch.code })

    val tintCount: Int get() = tints.size
}

val LightColors = SenaColors(
    dark = false,
    header = Color(0xFF141415),
    onHeader = Color(0xFFF5F3EE),
    onHeaderMuted = Color(0xFF8F8D87),
    sheet = Color(0xFFF3F0EA),
    card = Color(0xFFF6F3ED),   // a hair lighter than the sheet (not a visible difference) — keeps dot contrast rules passing
    cardBorder = Color(0xFF161617),
    text = Color(0xFF161617),
    textMuted = Color(0xFF66625A),
    ink = Color(0xFF161617),
    onInk = Color(0xFFF5F3EE),
    lead = Color(0xFF1B1B1D),
    onLead = Color(0xFFF5F3EE),
    onLeadMuted = Color(0xFFA19F98),
    chip = Color(0xFFE6E2D9),
    outline = Color(0xFF77736A),
    okText = Color(0xFF2F6B0A),
    warnText = Color(0xFF8A5300),
    badText = Color(0xFFC0261E),
)

val DarkColors = SenaColors(
    dark = true,
    header = Color(0xFF0A0A0B),
    onHeader = Color(0xFFF5F3EE),
    onHeaderMuted = Color(0xFF8F8D87),
    sheet = Color(0xFF1C1C1E),
    card = Color(0xFF1C1C1E),
    cardBorder = Color(0xFF8E8B84),
    text = Color(0xFFF3F1EC),
    textMuted = Color(0xFFA9A69E),
    ink = Color(0xFFF3F1EC),
    onInk = Color(0xFF141414),
    lead = Color(0xFFF3F1EC),
    onLead = Color(0xFF161617),
    onLeadMuted = Color(0xFF5B574E),
    chip = Color(0xFF35353A),
    outline = Color(0xFF8E8B84),
    okText = Color(0xFFA6DC63),
    warnText = Color(0xFFF5BE3B),
    badText = Color(0xFFFF8A7C),
)

val LocalSenaColors = staticCompositionLocalOf { LightColors }
