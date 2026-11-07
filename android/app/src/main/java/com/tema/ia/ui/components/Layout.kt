package com.tema.ia.ui.components

import androidx.annotation.DrawableRes
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.runtime.remember
import com.tema.ia.ui.theme.Motion
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.tema.ia.R
import com.tema.ia.ui.theme.LocalMuted
import com.tema.ia.ui.theme.LocalSurface
import com.tema.ia.ui.theme.Radius
import com.tema.ia.ui.theme.ScreenTitleStyle
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space

/** How much space the floating menu and the system bar take at the bottom: lists add this to their bottom padding. */
val LocalBottomPad = compositionLocalOf { 0.dp }

/**
 * Screen frame: a dark header with a large phrase and a light sheet with content, as in the mockups.
 * Top row: "back" or a leading element on the left, screen actions on the right.
 * [problem] — why the last refresh failed while the screen still has data: a banner over the sheet says it in words.
 */
@Composable
fun SenaScreen(
    modifier: Modifier = Modifier,
    title: String? = null,
    onBack: (() -> Unit)? = null,
    leading: (@Composable () -> Unit)? = null,
    actions: @Composable RowScope.() -> Unit = {},
    header: (@Composable ColumnScope.() -> Unit)? = null,
    problem: Throwable? = null,
    onRetry: () -> Unit = {},
    nav: Boolean = false,
    floating: (@Composable BoxScope.() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    val c = SenaTheme.colors
    val insets = WindowInsets.navigationBars.asPaddingValues().calculateBottomPadding()
    val bottom = insets + if (nav) Size.nav + Space.gap + Space.inset else Space.inset
    Column(modifier.fillMaxSize().background(c.header)) {
        CompositionLocalProvider(LocalContentColor provides c.onHeader, LocalMuted provides c.onHeaderMuted, LocalSurface provides c.header) {
            Column(Modifier.statusBarsPadding().fillMaxWidth()) {
                Row(
                    Modifier.fillMaxWidth().heightIn(min = Size.touchHeader + Space.small).padding(horizontal = Space.screen, vertical = Space.small),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(Space.gap),
                ) {
                    if (onBack != null) {
                        SenaIconButton(R.drawable.ic_back, stringResource(R.string.cd_back), onBack, tint = c.onHeader, size = Size.touchHeader)
                    } else leading?.invoke()
                    if (title != null) {
                        Text(title, style = ScreenTitleStyle, color = c.onHeader, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    } else {
                        Box(Modifier.weight(1f))
                    }
                    actions()
                }
                if (header != null) {
                    Column(
                        Modifier.fillMaxWidth().padding(horizontal = Space.screen).padding(bottom = Space.inset),
                        verticalArrangement = Arrangement.spacedBy(Space.small),
                        content = header,
                    )
                } else {
                    Box(Modifier.size(Space.small))
                }
            }
        }
        Box(Modifier.weight(1f).fillMaxWidth().clip(Radius.sheet).background(c.sheet), contentAlignment = Alignment.TopCenter) {
            CompositionLocalProvider(LocalBottomPad provides bottom, LocalPlayed provides remember { mutableSetOf<Any>() }) {
                Column(Modifier.fillMaxSize().widthIn(max = Size.maxWidth)) {
                    // the last problem stays drawn while the banner slides away, so its words don't vanish before it does
                    var shown by remember { mutableStateOf<Throwable?>(null) }
                    LaunchedEffect(problem) { if (problem != null) shown = problem }
                    AnimatedVisibility(problem != null, enter = expandVertically(tween(Motion.medium)) + fadeIn(tween(Motion.medium)), exit = shrinkVertically(tween(Motion.short)) + fadeOut(tween(Motion.short))) {
                        (problem ?: shown)?.let { ProblemBanner(it, onRetry) }
                    }
                    content()
                }
                floating?.invoke(this)
            }
        }
    }
}

/** Round action button in the bottom right: add a fact, rule, or note. */
@Composable
fun BoxScope.SenaFab(@DrawableRes icon: Int, description: String, onClick: () -> Unit, modifier: Modifier = Modifier, visible: Boolean = true) {
    val c = SenaTheme.colors
    val press = rememberPress()
    AnimatedVisibility(
        visible,
        Modifier.align(Alignment.BottomEnd).padding(end = Space.screen, bottom = LocalBottomPad.current),
        enter = scaleIn(spring(Motion.springDamping, Motion.springStiffness / 2f), initialScale = 0.3f) + fadeIn(tween(Motion.medium)),
        exit = scaleOut(tween(Motion.short), targetScale = 0.3f) + fadeOut(tween(Motion.short)),
    ) {
        Surface(
            onClick = onClick,
            shape = CircleShape,
            color = c.ink,
            contentColor = c.onInk,
            shadowElevation = Space.small,
            interactionSource = press,
            modifier = Modifier.size(Size.fab).pressScale(press).then(modifier),
        ) {
            Box(contentAlignment = Alignment.Center) { SenaIcon(icon, description, tint = c.onInk) }
        }
    }
}

/** List padding: the screen's side margins, plus room at the bottom for the floating menu (and the "add" button, if there is one). */
@Composable
fun listPadding(top: Dp = Space.small, fab: Boolean = false): PaddingValues =
    PaddingValues(start = Space.screen, end = Space.screen, top = top, bottom = LocalBottomPad.current + if (fab) Size.fab + Space.inset else 0.dp)

/** A short caption under the header title (quieter than the large phrase). */
@Composable
fun HeaderNote(text: String, modifier: Modifier = Modifier) {
    Text(text, modifier.fillMaxWidth(), style = MaterialTheme.typography.bodyMedium, color = SenaTheme.colors.onHeaderMuted, maxLines = 2, overflow = TextOverflow.Ellipsis)
}

