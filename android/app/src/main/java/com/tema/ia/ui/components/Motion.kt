package com.tema.ia.ui.components

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.ContentTransform
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.keyframes
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.interaction.InteractionSource
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.Dp
import com.tema.ia.ui.theme.Motion
import com.tema.ia.ui.theme.Radius
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space
import kotlinx.coroutines.delay

// The app's shared motion: cascading entrance, spring-loaded press, shimmering placeholders, a live dot, "typing" dots.
// Animated values are read only in graphicsLayer / draw (never in a function body): the screen never recomposes on every frame.

/** What has already appeared on this screen: a card that scrolled off and back on doesn't slide in a second time. */
val LocalPlayed = staticCompositionLocalOf { mutableSetOf<Any>() }

/** A card slides in smoothly from below and fades in; neighbors cascade by [index]. [key] is a stable name so the show isn't repeated. */
@Composable
fun Modifier.rise(index: Int = 0, key: Any? = null, origin: TransformOrigin = TransformOrigin.Center): Modifier {
    val played = LocalPlayed.current
    val id = key ?: index
    val progress = remember(id) { Animatable(if (played.contains(id)) 1f else 0f) }
    LaunchedEffect(id) {
        if (progress.value < 1f) {
            progress.animateTo(1f, tween(Motion.rise, delayMillis = index.coerceIn(0, Motion.staggerMax) * Motion.stagger, easing = FastOutSlowInEasing))
        }
        played.add(id)
    }
    val offset = with(LocalDensity.current) { Space.section.toPx() }
    return this.graphicsLayer {
        val p = progress.value
        alpha = p
        translationY = (1f - p) * offset
        val scale = Motion.enterScale + (1f - Motion.enterScale) * p
        scaleX = scale
        scaleY = scale
        transformOrigin = origin
    }
}

@Composable
fun rememberPress(): MutableInteractionSource = remember { MutableInteractionSource() }

/** A pressed element springs "inward" and bounces back. */
@Composable
fun Modifier.pressScale(source: InteractionSource, pressed: Float = Motion.pressScale): Modifier {
    val isPressed by source.collectIsPressedAsState()
    val scale = animateFloatAsState(if (isPressed) pressed else 1f, spring(Motion.springDamping, Motion.springStiffness), label = "press")
    return this.graphicsLayer {
        scaleX = scale.value
        scaleY = scale.value
    }
}

/** A shimmer running across a placeholder. */
@Composable
fun Modifier.shimmer(): Modifier {
    val x = rememberInfiniteTransition(label = "shimmer").animateFloat(0f, 1f, infiniteRepeatable(tween(Motion.shimmer, easing = LinearEasing), RepeatMode.Restart), label = "x")
    val glow = SenaTheme.colors.card.copy(alpha = 0.7f)
    return this.drawBehind {
        val w = size.width
        val from = -w + x.value * w * 2f
        drawRect(Brush.linearGradient(listOf(Color.Transparent, glow, Color.Transparent), start = Offset(from, 0f), end = Offset(from + w, size.height)))
    }
}

/** A gray "ghost card" with a shimmer: stands in for the list while the data hasn't arrived yet. */
@Composable
fun SkeletonCard(modifier: Modifier = Modifier) {
    val c = SenaTheme.colors
    Row(
        modifier.fillMaxWidth().clip(Radius.card).background(c.chip).shimmer().padding(Space.inset),
        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Space.gap),
    ) {
        Box(Modifier.size(Size.tile).clip(Radius.tile).background(c.card.copy(alpha = 0.55f)))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Space.small)) {
            Box(Modifier.fillMaxWidth(0.6f).height(Space.inset).clip(Radius.pill).background(c.card.copy(alpha = 0.55f)))
            Box(Modifier.fillMaxWidth(0.9f).height(Space.gap).clip(Radius.pill).background(c.card.copy(alpha = 0.4f)))
        }
    }
}

/** First-load placeholder: appears with a delay (so a fast response doesn't flash it) and smoothly. */
@Composable
fun SkeletonList(modifier: Modifier = Modifier, count: Int = 4) {
    var shown by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { delay(Motion.skeletonDelay); shown = true }
    AnimatedVisibility(shown, modifier.fillMaxSize(), enter = fadeIn(tween(Motion.medium)), exit = fadeOut(tween(Motion.short))) {
        Column(Modifier.fillMaxSize().padding(Space.screen), verticalArrangement = Arrangement.spacedBy(Space.gap)) {
            repeat(count) { SkeletonCard() }
        }
    }
}

/** Three dots bouncing in turn: "Sena is typing". */
@Composable
fun TypingDots(color: Color, modifier: Modifier = Modifier) {
    val t = rememberInfiniteTransition(label = "typing")
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(Space.tiny), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
        repeat(3) { i ->
            val lift = t.animateFloat(
                0f, 0f,
                infiniteRepeatable(keyframes {
                    durationMillis = Motion.typing
                    0f at 0
                    1f at (Motion.typing * (i * 2 + 1) / 10) using FastOutSlowInEasing
                    0f at (Motion.typing * (i * 2 + 4) / 10) using FastOutSlowInEasing
                    0f at Motion.typing
                }),
                label = "dot$i",
            )
            val jump = with(LocalDensity.current) { Space.tiny.toPx() }
            Box(Modifier.size(Size.dot).graphicsLayer { translationY = -lift.value * jump; alpha = 0.45f + 0.55f * lift.value }.background(color, CircleShape))
        }
    }
}

/** Text that smoothly crossfades into a new value when it changes (a status word, a caption). */
@Composable
fun SwapText(text: String, modifier: Modifier = Modifier, style: TextStyle = MaterialTheme.typography.bodyMedium, color: Color = Color.Unspecified, maxLines: Int = Int.MAX_VALUE) {
    AnimatedContent(text, modifier, transitionSpec = swap(), label = "swap") { value ->
        Text(value, style = style, color = color, maxLines = maxLines)
    }
}

@Composable
fun SwapText(text: AnnotatedString, modifier: Modifier = Modifier, style: TextStyle = MaterialTheme.typography.bodyMedium, maxLines: Int = Int.MAX_VALUE) {
    AnimatedContent(text, modifier, transitionSpec = swap(), label = "swap") { value ->
        Text(value, style = style, maxLines = maxLines)
    }
}

private fun <T> swap(): androidx.compose.animation.AnimatedContentTransitionScope<T>.() -> ContentTransform = {
    fadeIn(tween(Motion.medium, delayMillis = Motion.short / 2)) togetherWith fadeOut(tween(Motion.short))
}

/** A large, quiet swap of one element for another (send ↔ stop): a gentle fade-in with a slight scale. */
fun <T> popSwap(): androidx.compose.animation.AnimatedContentTransitionScope<T>.() -> ContentTransform = {
    (androidx.compose.animation.scaleIn(spring(Motion.springDamping, Motion.springStiffness), initialScale = Motion.enterScale) + fadeIn(tween(Motion.short))) togetherWith
        (androidx.compose.animation.scaleOut(tween(Motion.short), targetScale = Motion.enterScale) + fadeOut(tween(Motion.short)))
}
