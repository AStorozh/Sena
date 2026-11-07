package com.tema.ia.ui.components

import androidx.annotation.StringRes
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandHorizontally
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkHorizontally
import androidx.compose.runtime.getValue
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import com.tema.ia.ui.theme.Motion
import com.tema.ia.ui.theme.Radius
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space

data class NavItem(val route: String, @StringRes val label: Int)

/** Each of the five tabs has its own animated glyph. */
@Composable
private fun NavGlyph(route: String, selected: Boolean, modifier: Modifier = Modifier) {
    when (route) {
        com.tema.ia.ui.Routes.Home -> LeafBloomIcon(selected, modifier)
        com.tema.ia.ui.Routes.Chat -> ChatWiggleIcon(selected, modifier)
        com.tema.ia.ui.Routes.Tasks -> TaskCheckIcon(selected, modifier)
        com.tema.ia.ui.Routes.Memory -> BrainSparkIcon(selected, modifier)
        com.tema.ia.ui.Routes.More -> GridLandIcon(selected, modifier)
    }
}

/**
 * Floating bottom menu: a black "pill" with round items; the selected one expands into a light capsule with a label.
 * Like the bottom bar in the mockup with a light background.
 */
@Composable
fun FloatingNav(items: List<NavItem>, selected: String?, onSelect: (String) -> Unit, modifier: Modifier = Modifier) {
    val c = SenaTheme.colors
    val big = isBigFont()
    val haptic = LocalHapticFeedback.current
    Surface(modifier = modifier, shape = Radius.pill, color = c.ink, contentColor = c.onInk, shadowElevation = Space.small) {
        Row(
            Modifier.height(Size.nav).padding(horizontal = Space.small),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(Space.tiny),
        ) {
            items.forEach { item ->
                val on = item.route == selected
                val label = stringResource(item.label)
                val press = rememberPress()
                val container by animateColorAsState(if (on) c.onInk else c.ink, tween(Motion.medium), label = "navBg")
                val content by animateColorAsState(if (on) c.ink else c.onInk.copy(alpha = 0.72f), tween(Motion.medium), label = "navFg")
                val bounce by animateFloatAsState(if (on) 1.12f else 1f, spring(Motion.springDamping - 0.2f, Motion.springStiffness / 2f), label = "navBounce")
                Surface(
                    onClick = { if (!on) haptic.performHapticFeedback(HapticFeedbackType.SegmentTick); onSelect(item.route) },
                    shape = Radius.pill,
                    color = container,
                    contentColor = content,
                    interactionSource = press,
                    modifier = Modifier
                        .heightIn(min = Size.navItem)
                        .widthIn(min = Size.navItem)
                        .pressScale(press)
                        .testTag("nav_" + item.route)
                        .semantics(mergeDescendants = true) { contentDescription = label; this.selected = on; role = Role.Tab },
                ) {
                    Row(
                        Modifier.heightIn(min = Size.navItem).padding(horizontal = Space.gap),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        NavGlyph(item.route, on, modifier = Modifier.graphicsLayer { scaleX = bounce; scaleY = bounce })
                        AnimatedVisibility(
                            on && !big,
                            enter = fadeIn(tween(Motion.medium)) + expandHorizontally(spring(Motion.springDamping + 0.2f, Motion.springStiffness / 2f)),
                            exit = fadeOut(tween(Motion.short)) + shrinkHorizontally(tween(Motion.medium)),
                        ) {
                            Text(label, Modifier.padding(start = Space.small, end = Space.tiny), style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Clip)
                        }
                    }
                }
            }
        }
    }
}
