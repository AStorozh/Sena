package com.tema.ia.ui.components

import androidx.annotation.DrawableRes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInParent
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.width
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import kotlin.math.roundToInt
import com.tema.ia.ui.theme.Motion
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import com.tema.ia.R
import com.tema.ia.ui.theme.Coral
import com.tema.ia.ui.theme.LocalMuted
import com.tema.ia.ui.theme.LocalSurface
import com.tema.ia.ui.theme.OnPastel
import com.tema.ia.ui.theme.Radius
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space

enum class ButtonStyle {
    /** A black "pill": the primary action. Turns light itself on a high-contrast surface. */
    Primary,
    /** Coral: stop, delete. */
    Danger,
    /** Outlined: secondary. */
    Outline,
    /** Text only. */
    Text,
}

/** "Ink" for filled elements (button, switch) and the color on top of them: usually [SenaColors.ink]/[SenaColors.onInk], but inverted on a high-contrast surface ([LocalSurface]) so the detail doesn't get lost. */
@Composable
private fun inkPair(): Pair<Color, Color> {
    val c = SenaTheme.colors
    val surface = LocalSurface.current
    val ink = LocalContentColor.current
    return if (surface != null) ink to surface else c.ink to c.onInk
}

@Composable
fun SenaButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    style: ButtonStyle = ButtonStyle.Primary,
    @DrawableRes icon: Int? = null,
    enabled: Boolean = true,
    busy: Boolean = false,
    large: Boolean = false,
    compact: Boolean = false,
) {
    val c = SenaTheme.colors
    val surface = LocalSurface.current          // non-null — we're on a high-contrast surface
    val ink = LocalContentColor.current
    val height = if (large) Size.action else Size.touch
    val padding = PaddingValues(horizontal = if (compact) Space.small else Space.section)
    val enabledNow = enabled && !busy
    val press = rememberPress()
    val mod = modifier.pressScale(press)
    val arrangement = Arrangement.spacedBy(Space.small, Alignment.CenterHorizontally)
    val label: @Composable () -> Unit = {
        Row(horizontalArrangement = arrangement, verticalAlignment = Alignment.CenterVertically) {
            if (busy) Spinner(size = Size.iconSmall, color = LocalContentColor.current)
            else if (icon != null) SenaIcon(icon, null, size = Size.iconSmall)
            Text(
                text, style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f, fill = false),
            )
        }
    }
    when (style) {
        ButtonStyle.Text -> TextButton(
            onClick, mod.heightIn(min = Size.touch), enabledNow, shape = Radius.pill, interactionSource = press,
            colors = ButtonDefaults.textButtonColors(contentColor = if (surface != null) ink else c.text, disabledContentColor = LocalMuted.current),
        ) { label() }
        ButtonStyle.Outline -> OutlinedButton(
            onClick, mod.heightIn(min = height), enabledNow, shape = Radius.pill, contentPadding = padding, interactionSource = press,
            border = BorderStroke(Size.line, if (surface != null) ink.copy(alpha = 0.5f) else c.text.copy(alpha = 0.45f)),
            colors = ButtonDefaults.outlinedButtonColors(contentColor = if (surface != null) ink else c.text, disabledContentColor = LocalMuted.current),
        ) { label() }
        else -> {
            val (container, content) = when (style) {
                ButtonStyle.Danger -> Coral to OnPastel
                else -> inkPair()
            }
            Button(
                onClick, mod.heightIn(min = height), enabledNow, shape = Radius.pill, contentPadding = padding, interactionSource = press,
                colors = ButtonDefaults.buttonColors(
                    containerColor = container, contentColor = content,
                    disabledContainerColor = container.copy(alpha = 0.4f), disabledContentColor = content.copy(alpha = 0.7f),
                ),
            ) { label() }
        }
    }
}

/** A 48 dp icon button; with [container] — in a circle. Material still keeps the tap target at 48 dp even when [size] is smaller. */
@Composable
fun SenaIconButton(
    @DrawableRes icon: Int,
    description: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    container: Color = Color.Transparent,
    tint: Color = LocalContentColor.current,
    enabled: Boolean = true,
    size: Dp = Size.touch,
) {
    val press = rememberPress()
    IconButton(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.size(size).pressScale(press),
        interactionSource = press,
        colors = IconButtonDefaults.iconButtonColors(containerColor = container, contentColor = tint, disabledContainerColor = container, disabledContentColor = tint.copy(alpha = 0.35f)),
    ) {
        SenaIcon(icon, description, tint = if (enabled) tint else tint.copy(alpha = 0.35f))
    }
}

/** A round black button with an icon (send, power). The color inverts on a high-contrast surface. */
@Composable
fun RoundButton(
    @DrawableRes icon: Int,
    description: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    danger: Boolean = false,
    enabled: Boolean = true,
    busy: Boolean = false,
    size: androidx.compose.ui.unit.Dp = Size.action,
) {
    val (container, content) = if (danger) Coral to OnPastel else inkPair()
    val press = rememberPress()
    val shown by animateColorAsState(if (enabled) container else container.copy(alpha = 0.3f), tween(Motion.medium), label = "round")
    Surface(
        onClick = onClick,
        enabled = enabled && !busy,
        shape = Radius.pill,
        color = shown,
        contentColor = content,
        interactionSource = press,
        modifier = modifier.size(size).pressScale(press, Motion.pressScale).semantics { role = Role.Button; contentDescription = description },
    ) {
        Box(contentAlignment = Alignment.Center) {
            if (busy) Spinner(size = Size.icon, color = content) else SenaIcon(icon, null, tint = content)
        }
    }
}

/** A switch: black track, light thumb, when on. */
@Composable
fun SenaSwitch(checked: Boolean, onCheckedChange: ((Boolean) -> Unit)?, modifier: Modifier = Modifier, enabled: Boolean = true) {
    val (on, onThumb) = inkPair()
    val haptic = LocalHapticFeedback.current
    Switch(
        checked = checked,
        onCheckedChange = onCheckedChange?.let { change ->
            { value: Boolean ->
                haptic.performHapticFeedback(if (value) HapticFeedbackType.ToggleOn else HapticFeedbackType.ToggleOff)
                change(value)
            }
        },
        modifier = modifier, enabled = enabled,
        colors = SwitchDefaults.colors(
            checkedThumbColor = onThumb, checkedTrackColor = on, checkedBorderColor = on,
            uncheckedThumbColor = LocalMuted.current, uncheckedTrackColor = Color.Transparent, uncheckedBorderColor = LocalMuted.current,
            disabledCheckedThumbColor = onThumb.copy(alpha = 0.6f), disabledCheckedTrackColor = on.copy(alpha = 0.4f), disabledCheckedBorderColor = on.copy(alpha = 0.4f),
            disabledUncheckedThumbColor = LocalMuted.current.copy(alpha = 0.6f), disabledUncheckedTrackColor = Color.Transparent, disabledUncheckedBorderColor = LocalMuted.current.copy(alpha = 0.6f),
        ),
    )
}

@Composable
fun SenaField(
    value: String,
    onValueChange: (String) -> Unit,
    modifier: Modifier = Modifier,
    label: String? = null,
    placeholder: String? = null,
    singleLine: Boolean = false,
    minLines: Int = 1,
    maxLines: Int = if (singleLine) 1 else 5,
    keyboardOptions: KeyboardOptions = KeyboardOptions.Default,
    keyboardActions: KeyboardActions = KeyboardActions.Default,
    visualTransformation: VisualTransformation = VisualTransformation.None,
    trailing: (@Composable () -> Unit)? = null,
    leading: (@Composable () -> Unit)? = null,
    supporting: String? = null,
    isError: Boolean = false,
    enabled: Boolean = true,
    pill: Boolean = false,
) {
    val c = SenaTheme.colors
    val surface = LocalSurface.current
    // On a high-contrast surface the field is a light "island" with dark text; on a sheet it's white with an outline
    val container = surface?.let { LocalContentColor.current } ?: c.card
    val text = surface ?: c.text
    val muted = surface?.copy(alpha = 0.62f) ?: c.textMuted
    val border = if (surface != null) container else c.outline
    val focus = if (surface != null) text else c.ink
    OutlinedTextField(
        value = value, onValueChange = onValueChange, modifier = modifier,
        enabled = enabled, singleLine = singleLine, minLines = minLines, maxLines = maxLines,
        label = label?.let { { Text(it) } },
        placeholder = placeholder?.let { { Text(it) } },
        supportingText = supporting?.let { { Text(it) } },
        trailingIcon = trailing, leadingIcon = leading,
        isError = isError,
        keyboardOptions = keyboardOptions, keyboardActions = keyboardActions, visualTransformation = visualTransformation,
        textStyle = MaterialTheme.typography.bodyLarge,
        shape = if (pill) Radius.pill else Radius.field,
        colors = OutlinedTextFieldDefaults.colors(
            focusedTextColor = text, unfocusedTextColor = text, disabledTextColor = muted,
            focusedBorderColor = focus, unfocusedBorderColor = border, errorBorderColor = Coral, disabledBorderColor = border.copy(alpha = 0.5f),
            focusedLabelColor = text, unfocusedLabelColor = muted, errorLabelColor = Coral,
            focusedPlaceholderColor = muted, unfocusedPlaceholderColor = muted,
            focusedLeadingIconColor = text, unfocusedLeadingIconColor = text, focusedTrailingIconColor = text, unfocusedTrailingIconColor = text,
            focusedContainerColor = container, unfocusedContainerColor = container, errorContainerColor = container, disabledContainerColor = container,
            cursorColor = focus, errorCursorColor = Coral,
            focusedSupportingTextColor = if (surface != null) SenaTheme.colors.onLeadMuted else muted, unfocusedSupportingTextColor = if (surface != null) SenaTheme.colors.onLeadMuted else muted, errorSupportingTextColor = Coral,
        ),
    )
}

/** Search: a pill with a magnifier and a close cross. */
@Composable
fun SearchField(value: String, onValueChange: (String) -> Unit, placeholder: String, modifier: Modifier = Modifier) {
    val c = SenaTheme.colors
    SenaField(
        value, onValueChange, modifier, placeholder = placeholder, singleLine = true, pill = true,
        leading = { SenaIcon(R.drawable.ic_search, null, tint = c.text) },
        trailing = if (value.isNotEmpty()) {
            { SenaIconButton(R.drawable.ic_close, stringResource(R.string.cd_clear), { onValueChange("") }, tint = c.text) }
        } else null,
    )
}

/**
 * Section tabs: pills in a row; the selected one's black fill doesn't jump, it springs over to the new tab.
 * The row scrolls if it doesn't fit.
 */
@Composable
fun SegmentTabs(labels: List<String>, selected: Int, onSelect: (Int) -> Unit, modifier: Modifier = Modifier, inset: Boolean = true) {
    val c = SenaTheme.colors
    val haptic = LocalHapticFeedback.current
    // Where each tab actually sits, measured — not assumed — so the sliding fill always matches each tab's own rendered size exactly.
    val spots = remember { mutableStateMapOf<Int, Triple<Float, Float, Float>>() }   // left offset, width, height in pixels
    val spot = spots[selected]
    val x = animateFloatAsState(spot?.first ?: 0f, spring(Motion.springDamping + 0.2f, Motion.springStiffness / 2f), label = "tabX")
    val w = animateFloatAsState(spot?.second ?: 0f, spring(Motion.springDamping + 0.2f, Motion.springStiffness / 2f), label = "tabW")
    val tabHeight = spot?.third?.let { with(LocalDensity.current) { it.toDp() } } ?: Size.tab
    Row(
        modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).then(if (inset) Modifier.padding(horizontal = Space.screen) else Modifier),
    ) {
        Box {
            if (spot != null) {
                Box(
                    Modifier
                        .offset { IntOffset(x.value.roundToInt(), 0) }
                        .width(with(LocalDensity.current) { w.value.toDp() })
                        .height(tabHeight)
                        .background(c.ink, Radius.pill),
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(Space.small)) {
                labels.forEachIndexed { index, label ->
                    val on = index == selected
                    val press = rememberPress()
                    val ink by animateColorAsState(if (on) c.onInk else c.text, tween(Motion.medium), label = "tabInk")
                    val edge by animateColorAsState(if (on) Color.Transparent else c.outline, tween(Motion.medium), label = "tabEdge")
                    Surface(
                        onClick = { if (!on) haptic.performHapticFeedback(HapticFeedbackType.SegmentTick); onSelect(index) },
                        shape = Radius.pill,
                        color = Color.Transparent,
                        contentColor = ink,
                        border = BorderStroke(Size.hairline, edge),
                        interactionSource = press,
                        modifier = Modifier
                            .heightIn(min = Size.tab)
                            .pressScale(press)
                            .onGloballyPositioned { spots[index] = Triple(it.positionInParent().x, it.size.width.toFloat(), it.size.height.toFloat()) }
                            .semantics { this.selected = on; role = Role.Tab },
                    ) {
                        Box(Modifier.padding(horizontal = Space.inset), contentAlignment = Alignment.Center) {
                            Text(label, style = MaterialTheme.typography.labelLarge, maxLines = 1)
                        }
                    }
                }
            }
        }
    }
}

/** A selector made of several equal parts in a shared "capsule" (theme, mode): the selected part is black and springs over. */
@Composable
fun SegmentedControl(labels: List<String>, selected: Int, onSelect: (Int) -> Unit, modifier: Modifier = Modifier) {
    val c = SenaTheme.colors
    val surface = LocalSurface.current
    val haptic = LocalHapticFeedback.current
    val track = if (surface != null) LocalContentColor.current.copy(alpha = 0.14f) else c.chip
    val (pick, onPick) = inkPair()
    val text = if (surface != null) LocalContentColor.current else c.text
    BoxWithConstraints(modifier.fillMaxWidth().background(track, Radius.pill).padding(Space.tiny)) {
        val count = labels.size.coerceAtLeast(1)
        val gap = Space.tiny
        val itemWidth = (maxWidth - gap * (count - 1)) / count
        val x = animateFloatAsState(
            with(LocalDensity.current) { ((itemWidth + gap) * selected.coerceAtLeast(0)).toPx() },
            spring(Motion.springDamping + 0.2f, Motion.springStiffness / 2f), label = "segX",
        )
        if (selected >= 0) {
            Box(Modifier.offset { IntOffset(x.value.roundToInt(), 0) }.width(itemWidth).height(Size.touch).background(pick, Radius.pill))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(gap)) {
            labels.forEachIndexed { index, label ->
                val on = index == selected
                val press = rememberPress()
                val ink by animateColorAsState(if (on) onPick else text, tween(Motion.medium), label = "segInk")
                Surface(
                    onClick = { if (!on) haptic.performHapticFeedback(HapticFeedbackType.SegmentTick); onSelect(index) },
                    shape = Radius.pill,
                    color = Color.Transparent,
                    contentColor = ink,
                    interactionSource = press,
                    modifier = Modifier.weight(1f).heightIn(min = Size.touch).pressScale(press).semantics { this.selected = on; role = Role.Tab },
                ) {
                    Box(Modifier.padding(horizontal = Space.small), contentAlignment = Alignment.Center) {
                        Text(label, style = MaterialTheme.typography.labelLarge, maxLines = 1)
                    }
                }
            }
        }
    }
}
