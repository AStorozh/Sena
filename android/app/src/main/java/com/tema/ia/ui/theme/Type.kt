package com.tema.ia.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// The phone's system font, like the Mac app (which uses the system San Francisco). Material 3 scale, custom sizes only here.
private val family = FontFamily.Default

private fun style(size: Int, line: Int, weight: FontWeight, spacing: Double = 0.0) =
    TextStyle(fontFamily = family, fontSize = size.sp, lineHeight = line.sp, fontWeight = weight, letterSpacing = spacing.sp)

private val base = Typography()

val SenaTypography = Typography(
    displayLarge = base.displayLarge.copy(fontFamily = family),
    displayMedium = base.displayMedium.copy(fontFamily = family),
    displaySmall = style(34, 40, FontWeight.SemiBold, -0.5),
    headlineLarge = base.headlineLarge.copy(fontFamily = family),
    headlineMedium = style(28, 34, FontWeight.SemiBold, -0.3),
    headlineSmall = style(22, 28, FontWeight.SemiBold),
    titleLarge = style(20, 26, FontWeight.SemiBold),
    titleMedium = style(16, 22, FontWeight.SemiBold),
    titleSmall = style(14, 20, FontWeight.SemiBold),
    bodyLarge = style(16, 24, FontWeight.Normal),
    bodyMedium = style(14, 20, FontWeight.Normal),
    bodySmall = style(13, 18, FontWeight.Normal),
    labelLarge = style(14, 20, FontWeight.SemiBold),
    labelMedium = style(13, 16, FontWeight.Medium),
    labelSmall = style(12, 16, FontWeight.Medium),
)

/** The screen title in the header, like "Mon" in the mockup. */
val ScreenTitleStyle = style(19, 24, FontWeight.Bold, -0.3)

/** A large number on a card (expenses). */
val BigNumberStyle = style(32, 38, FontWeight.Bold, -0.4)
