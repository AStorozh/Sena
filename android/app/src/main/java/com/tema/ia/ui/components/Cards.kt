package com.tema.ia.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import com.tema.ia.ui.theme.LocalMuted
import com.tema.ia.ui.theme.LocalSurface
import com.tema.ia.ui.theme.Radius
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space

/** A white card on a light sheet (graphite in dark theme) with a thin border. */
@Composable
fun SenaCard(
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
    padding: Dp = Space.screen,
    content: @Composable ColumnScope.() -> Unit,
) {
    val c = SenaTheme.colors
    val inner: @Composable () -> Unit = {
        Column(Modifier.fillMaxWidth().padding(padding), verticalArrangement = Arrangement.spacedBy(Space.gap), content = content)
    }
    if (onClick != null) {
        val press = rememberPress()
        Surface(onClick = onClick, modifier = modifier.pressScale(press), shape = Radius.card, color = c.card, contentColor = c.text, border = BorderStroke(Size.hairline, c.cardBorder), interactionSource = press, content = inner)
    } else {
        Surface(modifier = modifier, shape = Radius.card, color = c.card, contentColor = c.text, border = BorderStroke(Size.hairline, c.cardBorder), content = inner)
    }
}

/**
 * A high-contrast card — the screen's main block: nearly black in light theme, nearly white in dark theme.
 * Buttons and fields inside invert their colors (light on dark and vice versa).
 */
@Composable
fun LeadCard(
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
    padding: Dp = Space.screen,
    content: @Composable ColumnScope.() -> Unit,
) {
    val c = SenaTheme.colors
    val inner: @Composable () -> Unit = {
        CompositionLocalProvider(LocalMuted provides c.onLeadMuted, LocalSurface provides c.lead) {
            Column(Modifier.fillMaxWidth().padding(padding), verticalArrangement = Arrangement.spacedBy(Space.gap), content = content)
        }
    }
    if (onClick != null) {
        val press = rememberPress()
        Surface(onClick = onClick, modifier = modifier.pressScale(press), shape = Radius.card, color = c.lead, contentColor = c.onLead, interactionSource = press, content = inner)
    } else {
        Surface(modifier = modifier, shape = Radius.card, color = c.lead, contentColor = c.onLead, content = inner)
    }
}

/**
 * A list row: icon on the left, title and caption, action on the right. One shape for all sections (rules, tasks, notes, integrations).
 * [lead] — a high-contrast row (the main one in the list).
 */
@Composable
fun RowCard(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    onClick: (() -> Unit)? = null,
    leading: (@Composable () -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
    below: (@Composable ColumnScope.() -> Unit)? = null,
    maxTitleLines: Int = 2,
    maxSubtitleLines: Int = 2,
    stackTrailing: Boolean = false,   // action under the text instead of to the right: leaves room for the title at large font sizes
    lead: Boolean = false,
) {
    val c = SenaTheme.colors
    val body: @Composable () -> Unit = {
        Row(
            Modifier.fillMaxWidth().heightIn(min = Size.tile + Space.screen).padding(horizontal = Space.inset, vertical = Space.gap),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(Space.gap),
        ) {
            leading?.invoke()
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Space.tiny)) {
                Text(title, style = MaterialTheme.typography.titleMedium, maxLines = maxTitleLines, overflow = TextOverflow.Ellipsis)
                if (!subtitle.isNullOrBlank()) Muted(subtitle, maxLines = maxSubtitleLines)
                below?.invoke(this)
                if (stackTrailing && trailing != null) Box(Modifier.padding(top = Space.small)) { trailing() }
            }
            if (!stackTrailing) trailing?.invoke()
        }
    }
    val colorsFor: @Composable () -> Unit = {
        if (lead) CompositionLocalProvider(LocalMuted provides c.onLeadMuted, LocalSurface provides c.lead, content = body) else body()
    }
    val shape = Radius.card
    val container = if (lead) c.lead else c.card
    val content = if (lead) c.onLead else c.text
    val border = if (lead) null else BorderStroke(Size.hairline, c.cardBorder)
    if (onClick != null) {
        val press = rememberPress()
        Surface(onClick = onClick, modifier = modifier.pressScale(press), shape = shape, color = container, contentColor = content, border = border, interactionSource = press, content = colorsFor)
    } else {
        Surface(modifier = modifier, shape = shape, color = container, contentColor = content, border = border, content = colorsFor)
    }
}
