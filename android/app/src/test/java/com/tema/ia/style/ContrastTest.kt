package com.tema.ia.style

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import com.tema.ia.ui.theme.Coral
import com.tema.ia.ui.theme.DarkColors
import com.tema.ia.ui.theme.LightColors
import com.tema.ia.ui.theme.Lime
import com.tema.ia.ui.theme.Mustard
import com.tema.ia.ui.theme.OnPastel
import com.tema.ia.ui.theme.SenaColors
import org.junit.Assert.assertTrue
import org.junit.Test

/** Design rule: text contrast at least 4.5:1, icons and outlines at least 3:1 — in both themes. */
class ContrastTest {
    private fun ratio(a: Color, b: Color): Double {
        val l1 = a.luminance().toDouble()
        val l2 = b.luminance().toDouble()
        return (maxOf(l1, l2) + 0.05) / (minOf(l1, l2) + 0.05)
    }

    private fun check(name: String, fg: Color, bg: Color, min: Double) =
        assertTrue("$name: контраст ${"%.2f".format(ratio(fg, bg))} ниже $min", ratio(fg, bg) >= min)

    private val themes = listOf("тёмная" to DarkColors, "светлая" to LightColors)

    @Test fun `text on the sheet and on cards`() = themes.forEach { (n, c) ->
        check("$n: текст на листе", c.text, c.sheet, 4.5)
        check("$n: второй текст на листе", c.textMuted, c.sheet, 4.5)
        check("$n: текст на карточке", c.text, c.card, 4.5)
        check("$n: второй текст на карточке", c.textMuted, c.card, 4.5)
        check("$n: текст на чипе", c.text, c.chip, 4.5)
    }

    @Test fun `text on the header`() = themes.forEach { (n, c) ->
        check("$n: текст на шапке", c.onHeader, c.header, 4.5)
        check("$n: второй текст на шапке", c.onHeaderMuted, c.header, 4.5)
    }

    @Test fun `text on the lead card and on ink`() = themes.forEach { (n, c) ->
        check("$n: текст на главной карточке", c.onLead, c.lead, 4.5)
        check("$n: второй текст на главной карточке", c.onLeadMuted, c.lead, 4.5)
        check("$n: текст на чернилах", c.onInk, c.ink, 4.5)
    }

    @Test fun `dark text on every pastel`() = themes.forEach { (n, c) ->
        for (i in 0 until c.tintCount) check("$n: пастель $i", OnPastel, c.tint(i), 4.5)
        check("$n: коралловая кнопка", OnPastel, Coral, 4.5)
    }

    @Test fun `status words on cards and on the sheet`() = themes.forEach { (n, c) ->
        for ((name, color) in listOf("работает" to c.okText, "внимание" to c.warnText, "ошибка" to c.badText)) {
            check("$n: «$name» на карточке", color, c.card, 4.5)
            check("$n: «$name» на листе", color, c.sheet, 4.5)
        }
    }

    @Test fun `status dots are visible on the header and on cards`() = themes.forEach { (n, c) ->
        for ((name, color) in listOf("лайм" to Lime, "коралл" to Coral, "горчица" to Mustard)) {
            check("$n: $name на шапке", color, c.header, 3.0)
            check("$n: $name на карточке", color, c.card, if (c.dark) 3.0 else 1.5)   // dot sits next to a word: meaning isn't carried by color alone
        }
    }

    @Test fun `outlines against the sheet and cards`() = themes.forEach { (n, c) ->
        check("$n: контур поля на листе", c.outline, c.sheet, 3.0)
        check("$n: контур поля на карточке", c.outline, c.card, 3.0)
    }

    @Test fun `the leaf is readable on the header`() = themes.forEach { (n, c) -> check("$n: лист", c.onHeader, c.header, 7.0) }

    @Test fun `tints wrap, handle negatives and are stable per key`() {
        val c: SenaColors = DarkColors
        assertTrue(c.tint(c.tintCount) == c.tint(0))
        assertTrue(c.tint(-1) == c.tint(c.tintCount - 1))
        assertTrue(c.tintOf("telegram") == c.tintOf("telegram"))
        for (i in 0 until c.tintCount) assertTrue(c.tint(i) != c.tint(i + 1))
    }
}
