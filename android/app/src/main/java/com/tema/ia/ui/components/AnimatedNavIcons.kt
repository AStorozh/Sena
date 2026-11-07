package com.tema.ia.ui.components

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.material3.LocalContentColor
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.asComposePath
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.unit.Dp
import androidx.core.graphics.PathParser
import com.tema.ia.ui.theme.Size
import kotlinx.coroutines.launch

/**
 * Each nav icon below mirrors a Lucide glyph drawn on a 24x24 (or, for the leaf, 686x602) grid
 * and plays its own tap/select animation instead of a single reused effect. All of them read the
 * ambient content color, so the floating menu stays plain black-and-white — no colored tiles here.
 */

private fun parsePath(d: String) = PathParser.createPathFromPathData(d).asComposePath()

private fun android.graphics.RectF.toCenter() = Offset((left + right) / 2f, (top + bottom) / 2f)

@Composable
fun ChatWiggleIcon(selected: Boolean, modifier: Modifier = Modifier, iconSize: Dp = Size.icon) {
    val tint = LocalContentColor.current
    val path = remember { parsePath("M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z") }
    val angle = remember { Animatable(0f) }
    var was by remember { mutableStateOf(selected) }
    LaunchedEffect(selected) {
        if (selected && !was) {
            angle.snapTo(0f)
            angle.animateTo(10f, tween(90, easing = FastOutSlowInEasing))
            angle.animateTo(-8f, tween(110, easing = FastOutSlowInEasing))
            angle.animateTo(0f, tween(120, easing = FastOutSlowInEasing))
        }
        was = selected
    }
    Canvas(modifier.size(iconSize)) {
        val scale = iconSize.toPx() / 24f
        val half = iconSize.toPx() / 2f
        withTransform({
            rotate(angle.value, pivot = Offset(half, half))
            scale(scale, scale, pivot = Offset.Zero)
        }) {
            drawPath(path, tint, style = Stroke(width = 1.8f, cap = androidx.compose.ui.graphics.StrokeCap.Round, join = androidx.compose.ui.graphics.StrokeJoin.Round))
        }
    }
}

private val leafPetals = listOf(
    "M318 24 C296 25 270 33 265 40 C262 44 262 51 266 57 C269 62 275 68 288 76 C294 79 304 87 311 92 C331 108 332 108 338 108 C348 108 352 103 358 80 C360 71 362 61 364 57 C370 42 368 34 361 28 C356 24 340 22 318 24 Z",
    "M403 34 C392 38 389 43 381 74 C373 102 372 111 376 115 C380 120 384 122 395 121 C415 121 432 115 466 96 C483 86 488 82 488 76 C488 73 487 70 486 68 C482 61 462 50 438 40 C419 33 411 32 403 34 Z",
    "M211 73 C179 77 144 103 118 142 C102 167 94 198 98 226 C102 260 122 295 153 324 C164 335 214 379 244 406 C302 456 305 458 318 458 C325 458 326 458 330 456 C334 453 339 445 342 434 C346 420 343 364 337 318 C333 284 333 282 334 245 C334 204 334 194 333 183 C327 135 288 90 242 76 C236 74 222 72 220 72 C219 72 215 73 211 73 Z",
    "M500 107 C489 109 482 112 462 121 C432 135 432 135 409 142 C371 152 363 162 362 200 C361 208 360 224 359 236 C357 263 359 291 364 326 C369 365 375 377 388 377 C399 377 402 375 457 330 C489 304 523 272 537 254 C571 211 580 178 566 142 C554 112 532 100 500 107 Z",
    "M591 255 C582 257 567 266 550 282 C524 305 492 333 434 380 C384 421 373 435 366 466 C364 475 364 479 364 494 C364 516 366 525 373 537 C389 561 417 562 460 542 C557 496 616 401 616 294 C616 261 610 251 591 255 Z",
    "M80 323 C73 328 74 348 82 376 C109 463 174 528 263 556 C300 568 325 569 330 559 C336 548 324 521 301 495 C269 458 158 362 115 334 C98 322 86 318 80 323 Z",
)

/** Home: Sena's leaf blooms petal by petal when the tab is opened. */
@Composable
fun LeafBloomIcon(selected: Boolean, modifier: Modifier = Modifier, iconSize: Dp = Size.icon) {
    val tint = LocalContentColor.current
    val petals = remember {
        leafPetals.map { d ->
            val p = PathParser.createPathFromPathData(d)
            val bounds = android.graphics.RectF().also { p.computeBounds(it, true) }
            p.asComposePath() to bounds.toCenter()
        }
    }
    val progress = remember { List(petals.size) { Animatable(1f) } }
    var was by remember { mutableStateOf(selected) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(selected) {
        if (selected && !was) {
            progress.forEach { it.snapTo(0f) }
            progress.forEachIndexed { i, anim ->
                scope.launch {
                    kotlinx.coroutines.delay(i * 40L)
                    anim.animateTo(1f, spring(dampingRatio = 0.5f, stiffness = 380f))
                }
            }
        }
        was = selected
    }
    Canvas(modifier.size(iconSize)) {
        val scale = iconSize.toPx() / 686f
        val offY = (iconSize.toPx() - 602f * scale) / 2f
        withTransform({ translate(0f, offY); scale(scale, scale, pivot = Offset.Zero) }) {
            petals.forEachIndexed { i, (path, center) ->
                val t = progress[i].value.coerceIn(0f, 1f)
                val overshoot = if (t < 1f) 1f - (1f - t) * (1f - t) else 1f
                withTransform({
                    scale(0.25f + 0.75f * overshoot, 0.25f + 0.75f * overshoot, pivot = center)
                }) {
                    drawPath(path, tint.copy(alpha = tint.alpha * t), style = androidx.compose.ui.graphics.drawscope.Fill)
                }
            }
        }
    }
}

private val tasksLines = listOf("M13 5h8", "M13 12h8", "M13 19h8")
private const val taskCheck1 = "M3 17l2 2 4-4"
private const val taskCheck2 = "M3 7l2 2 4-4"

/** Tasks: the two checkmarks tick in one after another, like items getting done. */
@Composable
fun TaskCheckIcon(selected: Boolean, modifier: Modifier = Modifier, iconSize: Dp = Size.icon) {
    val tint = LocalContentColor.current
    val lines = remember { tasksLines.map(::parsePath) }
    val c1 = remember { PathParser.createPathFromPathData(taskCheck1) }
    val c2 = remember { PathParser.createPathFromPathData(taskCheck2) }
    val f1 = remember { Animatable(1f) }
    val f2 = remember { Animatable(1f) }
    var was by remember { mutableStateOf(selected) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(selected) {
        if (selected && !was) {
            f1.snapTo(0f); f2.snapTo(0f)
            scope.launch { f1.animateTo(1f, tween(220, easing = FastOutSlowInEasing)) }
            scope.launch { kotlinx.coroutines.delay(160L); f2.animateTo(1f, tween(220, easing = FastOutSlowInEasing)) }
        }
        was = selected
    }
    Canvas(modifier.size(iconSize)) {
        val scale = iconSize.toPx() / 24f
        withTransform({ scale(scale, scale, pivot = Offset.Zero) }) {
            val stroke = Stroke(width = 1.8f, cap = androidx.compose.ui.graphics.StrokeCap.Round, join = androidx.compose.ui.graphics.StrokeJoin.Round)
            lines.forEach { drawPath(it, tint, style = stroke) }
            drawPath(partialPath(c1, f1.value).asComposePath(), tint, style = stroke)
            drawPath(partialPath(c2, f2.value).asComposePath(), tint, style = stroke)
        }
    }
}

private fun partialPath(src: android.graphics.Path, fraction: Float): android.graphics.Path {
    if (fraction >= 1f) return src
    val measure = android.graphics.PathMeasure(src, false)
    val dst = android.graphics.Path()
    measure.getSegment(0f, measure.length * fraction.coerceIn(0f, 1f), dst, true)
    return dst
}

private val brainPaths = listOf(
    "M12 18V5",
    "M15 13 a4.17 4.17 0 0 1 -3 -4 a4.17 4.17 0 0 1 -3 4",
    "M17.707 6.294 A3 3 0 1 0 12 5 a3 3 0 1 0 -5.707 1.294",
    "M17.996 5.125 a4 4 0 0 1 2.526 5.77",
    "M18 18 a4 4 0 0 0 2 -7.465",
    "M19.967 17.482 a4 4 0 1 1 -7.967 0.517 a4 4 0 1 1 -7.967 -0.517",
    "M6 18 a4 4 0 0 1 -2 -7.465",
    "M6.004 5.125 a4 4 0 0 0 -2.526 5.77",
)
private val brainSparks = listOf(
    Offset(9f, 8f), Offset(16f, 11f), Offset(11f, 16f), Offset(15f, 16f), Offset(7f, 13f), Offset(18f, 7f),
)

/** Memory: the brain pulses once and sparks flicker around it, like a thought lighting up. */
@Composable
fun BrainSparkIcon(selected: Boolean, modifier: Modifier = Modifier, iconSize: Dp = Size.icon) {
    val tint = LocalContentColor.current
    val paths = remember { brainPaths.map(::parsePath) }
    val pulse = remember { Animatable(1f) }
    val sparks = remember { List(brainSparks.size) { Animatable(0f) } }
    var was by remember { mutableStateOf(selected) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(selected) {
        if (selected && !was) {
            scope.launch { pulse.animateTo(1.16f, tween(140, easing = FastOutSlowInEasing)); pulse.animateTo(1f, spring(dampingRatio = 0.5f, stiffness = 300f)) }
            sparks.forEachIndexed { i, anim ->
                scope.launch {
                    anim.snapTo(0f)
                    kotlinx.coroutines.delay(i * 100L)
                    anim.animateTo(1f, tween(140))
                    anim.animateTo(0f, tween(220))
                }
            }
        }
        was = selected
    }
    Canvas(modifier.size(iconSize)) {
        val scale = iconSize.toPx() / 24f
        withTransform({ scale(scale, scale, pivot = Offset.Zero) }) {
            withTransform({ scale(pulse.value, pulse.value, pivot = Offset(12f, 12f)) }) {
                paths.forEach { drawPath(it, tint, style = Stroke(width = 1.8f, cap = androidx.compose.ui.graphics.StrokeCap.Round, join = androidx.compose.ui.graphics.StrokeJoin.Round)) }
            }
            brainSparks.forEachIndexed { i, pos ->
                drawCircle(tint.copy(alpha = sparks[i].value), radius = 1.1f, center = pos)
            }
        }
    }
}

private val gridCells = listOf(
    "M3 3h7v7H3z" to Offset(6.5f, 6.5f),
    "M14 3h7v7h-7z" to Offset(17.5f, 6.5f),
    "M14 14h7v7h-7z" to Offset(17.5f, 17.5f),
    "M3 14h7v7H3z" to Offset(6.5f, 17.5f),
)

/** More: the four tiles land into place one after another. */
@Composable
fun GridLandIcon(selected: Boolean, modifier: Modifier = Modifier, iconSize: Dp = Size.icon) {
    val tint = LocalContentColor.current
    val cells = remember { gridCells.map { (d, c) -> parsePath(d) to c } }
    val progress = remember { List(gridCells.size) { Animatable(1f) } }
    var was by remember { mutableStateOf(selected) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(selected) {
        if (selected && !was) {
            progress.forEachIndexed { i, anim ->
                scope.launch {
                    anim.snapTo(0f)
                    kotlinx.coroutines.delay(i * 80L)
                    anim.animateTo(1f, spring(dampingRatio = Spring.DampingRatioMediumBouncy, stiffness = 420f))
                }
            }
        }
        was = selected
    }
    Canvas(modifier.size(iconSize)) {
        val scale = iconSize.toPx() / 24f
        withTransform({ scale(scale, scale, pivot = Offset.Zero) }) {
            cells.forEachIndexed { i, (path, center) ->
                val t = progress[i].value
                withTransform({ scale(0.2f + 0.8f * t.coerceIn(0f, 1.3f), 0.2f + 0.8f * t.coerceIn(0f, 1.3f), pivot = center) }) {
                    drawPath(path, tint.copy(alpha = t.coerceIn(0f, 1f)), style = Stroke(width = 1.8f, cap = androidx.compose.ui.graphics.StrokeCap.Round, join = androidx.compose.ui.graphics.StrokeJoin.Round))
                }
            }
        }
    }
}

