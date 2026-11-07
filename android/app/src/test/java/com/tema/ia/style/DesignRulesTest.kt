package com.tema.ia.style

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Rules from docs/DESIGN-RULES.md that can be checked against the source:
 * screens have no colors, sizes, fonts, shapes or texts of their own; no emoji and no WebView; a single leaf.
 */
class DesignRulesTest {
    private val root = generateSequence(File("").absoluteFile) { it.parentFile }.first { File(it, "settings.gradle.kts").exists() }
    private val main = File(root, "app/src/main")
    private val kotlin = File(main, "java/com/tema/ia")

    private fun sources(sub: String) = File(kotlin, sub).walkTopDown().filter { it.extension == "kt" }.toList()

    private fun lines(f: File) = f.readLines().mapIndexed { i, l -> (i + 1) to l }.filter { (_, l) -> !l.trimStart().startsWith("//") && !l.trimStart().startsWith("*") && !l.trimStart().startsWith("/*") }

    private fun violations(files: List<File>, pattern: Regex, ignore: (File, String) -> Boolean = { _, _ -> false }): List<String> =
        files.flatMap { f -> lines(f).filter { (_, l) -> pattern.containsMatchIn(l) && !ignore(f, l) }.map { (n, l) -> "${f.relativeTo(root)}:$n  ${l.trim()}" } }

    private fun assertNone(rule: String, found: List<String>) = assertTrue("$rule:\n" + found.joinToString("\n"), found.isEmpty())

    private val screens get() = sources("ui/screens")
    private val components get() = sources("ui/components")

    @Test fun `no raw colors outside the theme`() {
        val all = sources("ui").filter { !it.path.contains("/ui/theme/") }
        assertNone("Свои цвета: только токены из ui/theme", violations(all, Regex("""Color\(0x|Color\.(Red|Blue|Green|Black|White|Gray|Yellow|Cyan|Magenta)\b|\.copy\(red""")))
    }

    @Test fun `screens have no own sizes fonts or shapes`() {
        assertNone("В экранах нет чисел dp и sp: размеры берутся из Space/Size", violations(screens, Regex("""\b\d+(\.\d+)?\.(dp|sp)\b""")))
        assertNone("В экранах нет FontWeight, FontFamily и своих форм", violations(screens, Regex("""FontWeight|FontFamily|RoundedCornerShape|CircleShape|CutCornerShape""")))
    }

    @Test fun `no hard coded texts`() {
        val all = sources("ui")
        assertNone("Текст на экране только из strings.xml", violations(all, Regex("""\bText\(\s*"|text\s*=\s*"[^"]*[А-Яа-яA-Za-z]|contentDescription\s*=\s*"|placeholder\s*=\s*"""")))
    }

    @Test fun `no cyrillic literals in screens`() {
        val found = violations(screens + components, Regex(""""[^"]*[А-Яа-яЁё][^"]*""""))
        assertNone("Русские строки в коде экранов и компонентов запрещены (кроме имён папок сервера)", found)
    }

    @Test fun `no emoji anywhere`() {
        val emoji = Regex("[\\u2600-\\u27BF\\u2B00-\\u2BFF\\x{1F000}-\\x{1FAFF}\\uFE0F]")
        val files = main.walkTopDown().filter { it.isFile && it.extension in setOf("kt", "xml") }.toList()
        assertNone("Эмодзи нигде не нужны", files.flatMap { f -> f.readLines().mapIndexedNotNull { i, l -> if (emoji.containsMatchIn(l)) "${f.relativeTo(root)}:${i + 1}" else null } })
    }

    @Test fun `no webview and no web content`() {
        val all = sources("") + File(main, "AndroidManifest.xml")
        assertNone("WebView запрещён", violations(all.filter { it.extension == "kt" }, Regex("""WebView|loadUrl|evaluateJavascript""")))
        assertTrue(!File(main, "assets").exists())
    }

    @Test fun `no system dialogs menus or lists instead of the catalog`() {
        assertNone("Свои диалоги и системные списки запрещены", violations(screens, Regex("""\bDialog\(|\bPopup\(|\bCard\(|ElevatedCard|OutlinedCard|\bListItem\(|\bButton\(|\bOutlinedButton\(|\bSwitch\(|\bIcon\(|\bIconButton\(""")))
    }

    @Test fun `no background polling outside the screen lifecycle`() {
        assertNone("Фоновых сервисов и воркеров нет", violations(sources(""), Regex("""WorkManager|JobScheduler|AlarmManager|startForeground|GlobalScope""")))
        val manifest = File(main, "AndroidManifest.xml").readText()
        assertTrue("В манифесте нет сервисов", !manifest.contains("<service") && !manifest.contains("<receiver"))
    }

    @Test fun `password is never logged`() {
        assertNone("Пароль не пишется в логи", violations(sources(""), Regex("""Log\.[dviwe]\(|println\(|Timber|HttpLoggingInterceptor""")))
    }

    @Test fun `the leaf is the only brand mark`() {
        val drawables = File(main, "res/drawable").list().orEmpty().toSet()
        assertTrue(drawables.contains("ic_sena_leaf.xml"))
        assertEquals(emptyList<String>(), drawables.filter { it.contains("logo") || it.contains("brand") || it.contains("leaf") && it != "ic_sena_leaf.xml" && !it.startsWith("ic_splash") })
    }

    @Test fun `only catalog icons are used`() {
        val icons = File(main, "res/drawable").list().orEmpty().map { it.removeSuffix(".xml") }.toSet()
        val used = sources("").flatMap { Regex("""R\.drawable\.(\w+)""").findAll(it.readText()).map { m -> m.groupValues[1] }.toList() }.toSet()
        assertTrue("Нет файла значка: ${used - icons}", (used - icons).isEmpty())
        assertTrue("Все значки названы ic_*: ${icons.filter { !it.startsWith("ic_") }}", icons.all { it.startsWith("ic_") })
    }

    @Test fun `no unused icons`() {
        val code = main.walkTopDown().filter { it.isFile && it.extension in setOf("kt", "xml") && !it.path.contains("/res/drawable/") }.joinToString("\n") { it.readText() }
        val unused = File(main, "res/drawable").list().orEmpty().map { it.removeSuffix(".xml") }.filter { !code.contains("R.drawable.$it") && !code.contains("@drawable/$it") }
        assertTrue("Значки без дела: $unused", unused.isEmpty())
    }

    @Test fun `touch targets are at least 48dp`() {
        val size = File(kotlin, "ui/theme/Dimens.kt").readText()
        assertTrue(Regex("""val touch = 48\.dp""").containsMatchIn(size))
        assertTrue(Regex("""val action = 56\.dp""").containsMatchIn(size))
    }

    @Test fun `every screen stands on the common frame`() {
        val frames = screens.filter { it.name.endsWith("Screen.kt") }
        assertTrue("Нет экранов", frames.size >= 10)
        assertNone("Экран без общего каркаса SenaScreen (тёмная шапка и светлый лист)", frames.filter { !it.readText().contains("SenaScreen(") }.map { it.name })
    }

    @Test fun `the system font only`() {
        assertTrue("Шрифты в ресурсах не нужны: шрифт системный", !File(main, "res/font").exists())
        assertNone("Свои шрифты запрещены", violations(sources(""), Regex("""Font\(R\.font|R\.font\.""")))
    }

    @Test fun `layouts cannot cause horizontal page scroll`() {
        assertNone("Горизонтальная прокрутка страницы запрещена (только ряд вкладок)", violations(screens, Regex("""horizontalScroll""")))
    }
}
