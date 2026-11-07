package com.tema.ia.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Locale

class FormatTest {
    @Test fun `money uses the locale separator`() {
        assertEquals("$0,12", Fmt.money(0.123, Locale.forLanguageTag("ru")))
        assertEquals("$0.12", Fmt.money(0.123, Locale.US))
        assertEquals("$5,00", Fmt.money(5.0, Locale.forLanguageTag("ru")))
        assertEquals("$0.00", Fmt.money(0.0, Locale.US))
    }

    @Test fun `a sum under a cent keeps its digits`() {
        assertEquals("$0.0032", Fmt.money(0.0032, Locale.US))
        assertEquals("$0,0032", Fmt.money(0.0032, Locale.forLanguageTag("ru")))
        assertEquals("$0.000040", Fmt.money(0.00004, Locale.US))
    }

    @Test fun `numbers as people write them`() {
        assertEquals("90000", Fmt.number(90_000.0))
        assertEquals("150.5", Fmt.number(150.5))
        assertEquals("2.5", Fmt.number(2.5))
        assertEquals("0", Fmt.number(0.0))
    }

    @Test fun `markdown bold code and bullets`() {
        val t = simpleMarkdown("**Итог**: ок\n- пункт `код`\n## Заголовок")
        assertEquals("Итог: ок\n• пункт код\nЗаголовок", t.text)
        assertEquals(1, t.spanStyles.count { it.item.fontWeight != null })
        assertEquals(1, t.spanStyles.count { it.item.fontFamily != null })
    }

    @Test fun `markdown leaves broken markers alone`() {
        assertEquals("a ** b `c", simpleMarkdown("a ** b `c").text)
        assertEquals("", simpleMarkdown("").text)
        assertTrue(simpleMarkdown("обычный текст").spanStyles.isEmpty())
    }
}
