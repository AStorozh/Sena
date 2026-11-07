package com.tema.ia.ui.components

import androidx.annotation.DrawableRes
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.FlowRowScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size as GeoSize
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import com.tema.ia.R
import com.tema.ia.ui.theme.Motion
import com.tema.ia.ui.theme.LocalMuted
import com.tema.ia.ui.theme.OnPastel
import com.tema.ia.ui.theme.Radius
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space

/** Sena's leaf — the app's only mark. */
@Composable
fun SenaLeaf(modifier: Modifier = Modifier, size: Dp = Size.leafBig, tint: Color = SenaTheme.colors.onHeader, description: String? = null) {
    Icon(
        painter = painterResource(R.drawable.ic_sena_leaf),
        contentDescription = description,
        tint = tint,
        modifier = modifier.size(size),
    )
}

@Composable
fun SenaIcon(@DrawableRes id: Int, description: String?, modifier: Modifier = Modifier, tint: Color = LocalContentColor.current, size: Dp = Size.icon) {
    Icon(painter = painterResource(id), contentDescription = description, tint = tint, modifier = modifier.size(size))
}

/** A rounded square (or circle) in a pastel color with a dark icon: how list rows tell record types apart. */
@Composable
fun IconTile(@DrawableRes icon: Int, tint: Color, modifier: Modifier = Modifier, size: Dp = Size.tile, round: Boolean = false) {
    val shown by animateColorAsState(tint, tween(Motion.medium), label = "tile")
    Box(
        modifier.size(size).clip(if (round) CircleShape else Radius.tile).background(shown).clearAndSetSemantics { },
        contentAlignment = Alignment.Center,
    ) {
        SenaIcon(icon, null, tint = OnPastel, size = Size.icon)
    }
}

/** A circle with the first letter of a name: people in memory. */
@Composable
fun Initial(name: String, tint: Color, modifier: Modifier = Modifier, size: Dp = Size.avatar) {
    Box(modifier.size(size).clip(CircleShape).background(tint).clearAndSetSemantics { }, contentAlignment = Alignment.Center) {
        Text(name.trim().take(1).uppercase(), style = MaterialTheme.typography.titleMedium, color = OnPastel)
    }
}

@Composable
fun Dot(color: Color, modifier: Modifier = Modifier, size: Dp = Size.dot) {
    val shown by animateColorAsState(color, tween(Motion.medium), label = "dot")
    Box(modifier.size(size).background(shown, CircleShape).clearAndSetSemantics { })
}

/** A dashed vertical line: a step timeline connector to something expected but not done yet. */
@Composable
fun DashedLine(modifier: Modifier = Modifier, color: Color = LocalMuted.current) {
    Canvas(modifier) {
        val x = size.width / 2f
        drawLine(
            color = color, start = Offset(x, 0f), end = Offset(x, size.height), strokeWidth = size.width,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(size.width * 2f, size.width * 1.6f), 0f),
        )
    }
}

/** A status word with a colored dot: color is never the only carrier of meaning. */
@Composable
fun StatusChip(text: String, dot: Color, modifier: Modifier = Modifier) {
    Row(
        modifier.heightIn(min = Size.chip).background(SenaTheme.colors.chip, Radius.pill).padding(horizontal = Space.gap),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Space.small),
    ) {
        Dot(dot)
        Text(text, style = MaterialTheme.typography.labelMedium, color = SenaTheme.colors.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

/** A row of dot+label counts (verdicts, statuses) that must stay on one line: it scrolls sideways instead of wrapping. */
@Composable
fun DotLegend(modifier: Modifier = Modifier, content: @Composable RowScope.() -> Unit) {
    Row(modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(Space.section), content = content)
}

@Composable
fun DotLegendItem(dot: Color, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.small)) {
        Dot(dot)
        Text(text, style = MaterialTheme.typography.bodyMedium, color = SenaTheme.colors.text, maxLines = 1)
    }
}

/** A soft chip with no dot (day heading, label). */
@Composable
fun Chip(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        modifier = modifier.background(SenaTheme.colors.chip, Radius.pill).padding(horizontal = Space.gap, vertical = Space.tiny + Space.tiny / 2),
        style = MaterialTheme.typography.labelMedium,
        color = SenaTheme.colors.text,
        maxLines = 1,
    )
}

/** A muted caption: its color comes from the surface it sits on. */
@Composable
fun Muted(text: String, modifier: Modifier = Modifier, style: TextStyle = MaterialTheme.typography.bodyMedium, maxLines: Int = Int.MAX_VALUE) {
    Text(text, modifier, color = LocalMuted.current, style = style, maxLines = maxLines, overflow = TextOverflow.Ellipsis)
}

@Composable
fun SectionTitle(text: String, modifier: Modifier = Modifier, trailing: (@Composable RowScope.() -> Unit)? = null) {
    Row(modifier.fillMaxWidth().padding(top = Space.small), verticalAlignment = Alignment.CenterVertically) {
        Text(text, style = MaterialTheme.typography.titleMedium, color = SenaTheme.colors.text, modifier = Modifier.weight(1f))
        trailing?.invoke(this)
    }
}

@Composable
fun Spinner(modifier: Modifier = Modifier, size: Dp = Size.icon, color: Color = LocalContentColor.current) {
    CircularProgressIndicator(modifier = modifier.size(size), color = color, strokeWidth = Size.line + Size.hairline / 2)
}

/** Large system font (above 130%): list rows stack into a column, button rows wrap. */
@Composable
fun isBigFont(): Boolean = LocalDensity.current.fontScale > 1.3f

/** A row of buttons that wraps to the next line when it doesn't fit (large font, long labels). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ButtonRow(modifier: Modifier = Modifier, content: @Composable FlowRowScope.() -> Unit) {
    FlowRow(
        modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(Space.small),
        verticalArrangement = Arrangement.spacedBy(Space.small),
        content = content,
    )
}

/**
 * A dotted bar with a fill: how much is used up (progress from 0 to 1), or "in progress" (progress = null, a running segment).
 * The fill eases in on appearance and on change. Like the "time left" bar in the mockup.
 */
@Composable
fun DotBar(progress: Float?, modifier: Modifier = Modifier, color: Color = LocalContentColor.current, track: Color = LocalMuted.current) {
    val sweep = if (progress == null) {
        rememberInfiniteTransition(label = "bar").animateFloat(0f, 1f, infiniteRepeatable(tween(Motion.sweep, easing = LinearEasing), RepeatMode.Restart), label = "sweep")
    } else null
    val fill = remember { Animatable(0f) }
    LaunchedEffect(progress) {
        if (progress != null) fill.animateTo(progress.coerceIn(0f, 1f), tween(Motion.fill, easing = FastOutSlowInEasing))
    }
    Canvas(modifier.fillMaxWidth().height(Size.bar)) {
        val h = size.height
        val step = h * 2.4f
        val count = (size.width / step).toInt().coerceAtLeast(1)
        val spacing = size.width / count
        for (i in 0 until count) drawCircle(track.copy(alpha = 0.55f), radius = h / 4f, center = Offset(spacing * i + spacing / 2f, h / 2f))
        val length = if (sweep != null) size.width * 0.28f else (size.width * fill.value).coerceAtLeast(if (fill.value > 0f) h else 0f)
        val start = if (sweep != null) (size.width + length) * sweep.value - length else 0f
        val from = start.coerceAtLeast(0f)
        val to = (start + length).coerceAtMost(size.width)
        if (to > from) drawRoundRect(color, topLeft = Offset(from, 0f), size = GeoSize(to - from, h), cornerRadius = CornerRadius(h / 2f))
    }
}
