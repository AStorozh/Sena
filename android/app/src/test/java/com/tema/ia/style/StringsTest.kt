package com.tema.ia.style

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Russian is the source, every string has an English translation; placeholders (%1$s) match; none are empty. */
class StringsTest {
    private val root = generateSequence(File("").absoluteFile) { it.parentFile }.first { File(it, "settings.gradle.kts").exists() }
    private val res = File(root, "app/src/main/res")

    private fun load(dir: String): Map<String, String> =
        Regex("""<string name="([a-z0-9_]+)">(.*?)</string>""", RegexOption.DOT_MATCHES_ALL).findAll(File(res, "$dir/strings.xml").readText()).associate { it.groupValues[1] to it.groupValues[2] }

    private val ru = load("values")
    private val en = load("values-en")

    @Test fun `same names in both languages`() {
        assertEquals("Нет перевода: ${ru.keys - en.keys}", emptySet<String>(), ru.keys - en.keys)
        assertEquals("Лишний перевод: ${en.keys - ru.keys}", emptySet<String>(), en.keys - ru.keys)
    }

    @Test fun `no empty strings`() {
        assertTrue(ru.filterValues { it.isBlank() }.isEmpty())
        assertTrue(en.filterValues { it.isBlank() }.isEmpty())
    }

    @Test fun `placeholders match`() {
        val arg = Regex("""%\d\$[sd]""")
        for (k in ru.keys.intersect(en.keys)) {
            assertEquals("Подстановки в $k", arg.findAll(ru.getValue(k)).map { it.value }.sorted().toList(), arg.findAll(en.getValue(k)).map { it.value }.sorted().toList())
        }
    }

    @Test fun `english has no cyrillic and russian has text`() {
        assertTrue("Кириллица в английском: ${en.filterValues { Regex("[А-Яа-яЁё]").containsMatchIn(it) }.keys}", en.filterValues { Regex("[А-Яа-яЁё]").containsMatchIn(it) }.isEmpty())
        // Brand names (Chrome, GitHub) and letterless strings (a list marker) are allowed without Russian letters
        val noRussian = ru.filter { (k, v) -> !Regex("[А-Яа-яЁё]").containsMatchIn(v) && Regex("[A-Za-z]{2}").containsMatchIn(v.replace(Regex("%\\d\\$[sd]"), "")) && !k.endsWith("_name") }.keys
        assertTrue("Русский текст без русских букв: $noRussian", noRussian.isEmpty())
    }

    @Test fun `every string is used by the code or the manifest`() {
        val code = File(root, "app/src/main").walkTopDown().filter { it.isFile && it.extension in setOf("kt", "xml") && !it.path.contains("/res/values") }.joinToString("\n") { it.readText() }
        val unused = ru.keys.filter { k -> !code.contains("R.string.$k") && !code.contains("@string/$k") }
        assertTrue("Строки без дела: $unused", unused.isEmpty())
        val unusedPlurals = plurals("values").keys.filter { k -> !code.contains("R.plurals.$k") }
        assertTrue("Множественные формы без дела: $unusedPlurals", unusedPlurals.isEmpty())
    }

    /** Plurals: name -> quantity -> text. Russian needs one/few/many/other, English one/other; both say the same number. */
    private fun plurals(dir: String): Map<String, Map<String, String>> =
        Regex("""<plurals name="([a-z0-9_]+)">(.*?)</plurals>""", RegexOption.DOT_MATCHES_ALL).findAll(File(res, "$dir/strings.xml").readText()).associate { m ->
            m.groupValues[1] to Regex("""<item quantity="(\w+)">(.*?)</item>""").findAll(m.groupValues[2]).associate { it.groupValues[1] to it.groupValues[2] }
        }

    @Test fun `plurals exist in both languages with every form they need`() {
        val ruPlurals = plurals("values")
        val enPlurals = plurals("values-en")
        assertTrue("Нет ни одной множественной формы? Тест читает не тот файл", ruPlurals.isNotEmpty())
        assertEquals(ruPlurals.keys, enPlurals.keys)
        for ((name, forms) in ruPlurals) {
            assertTrue("$name: русскому нужны one, few, many, other", forms.keys.containsAll(listOf("one", "few", "many", "other")))
            assertTrue("$name: английскому нужны one, other", enPlurals.getValue(name).keys.containsAll(listOf("one", "other")))
            (forms.values + enPlurals.getValue(name).values).forEach { assertTrue("$name: в каждой форме число", it.contains("%d")) }
        }
    }
}
