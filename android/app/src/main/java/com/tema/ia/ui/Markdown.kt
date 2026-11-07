package com.tema.ia.ui

import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle

/** Sena's replies come with light markup: **bold**, `code`, "- item" lines. Everything else is shown as is. */
fun simpleMarkdown(text: String): AnnotatedString = buildAnnotatedString {
    val lines = text.lines()
    lines.forEachIndexed { index, raw ->
        var line = raw
        val bullet = Regex("^\\s*[-*]\\s+(.*)$").matchEntire(line)
        if (bullet != null) line = "• " + bullet.groupValues[1]
        line = line.replace(Regex("^#{1,6}\\s+"), "")
        appendInline(line)
        if (index < lines.lastIndex) append('\n')
    }
}

private fun AnnotatedString.Builder.appendInline(line: String) {
    var i = 0
    while (i < line.length) {
        when {
            line.startsWith("**", i) -> {
                val end = line.indexOf("**", i + 2)
                if (end > i + 2) {
                    withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(line.substring(i + 2, end)) }
                    i = end + 2
                } else { append(line[i]); i++ }
            }
            line[i] == '`' -> {
                val end = line.indexOf('`', i + 1)
                if (end > i + 1) {
                    withStyle(SpanStyle(fontFamily = FontFamily.Monospace)) { append(line.substring(i + 1, end)) }
                    i = end + 1
                } else { append(line[i]); i++ }
            }
            else -> { append(line[i]); i++ }
        }
    }
}
