package com.tema.ia.ui.screens.chat

import android.Manifest
import android.content.ClipData
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.ui.graphics.TransformOrigin
import com.tema.ia.ui.components.TypingDots
import com.tema.ia.ui.components.popSwap
import com.tema.ia.ui.components.rise
import com.tema.ia.ui.theme.Motion
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.platform.ClipEntry
import androidx.compose.ui.platform.LocalClipboard
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.tema.ia.R
import com.tema.ia.ui.CollectMessages
import com.tema.ia.ui.Load
import com.tema.ia.ui.LocalSnackbar
import com.tema.ia.ui.PollWhileVisible
import com.tema.ia.ui.PullRefresh
import com.tema.ia.ui.asString
import com.tema.ia.ui.components.Chip
import com.tema.ia.ui.components.ConfirmDialog
import com.tema.ia.ui.components.EmptyState
import com.tema.ia.ui.components.ErrorState
import com.tema.ia.ui.components.LoadingBox
import com.tema.ia.ui.components.LocalBottomPad
import com.tema.ia.ui.components.Muted
import com.tema.ia.ui.components.RoundButton
import com.tema.ia.ui.components.SenaButton
import com.tema.ia.ui.components.SenaField
import com.tema.ia.ui.components.SenaIcon
import com.tema.ia.ui.components.SenaIconButton
import com.tema.ia.ui.components.SenaScreen
import com.tema.ia.ui.problem
import com.tema.ia.ui.simpleMarkdown
import com.tema.ia.ui.theme.Coral
import com.tema.ia.ui.theme.Radius
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space
import com.tema.ia.ui.Fmt
import com.tema.ia.ui.toUiText
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ChatScreen(viewModel: ChatViewModel, voice: VoiceViewModel, onGoHome: () -> Unit) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val talk by voice.state.collectAsStateWithLifecycle()
    PollWhileVisible(viewModel.loader)
    CollectMessages(viewModel.messages)
    // the live voice: the microphone is asked for once, when the owner first presses it
    val context = LocalContext.current
    val snack = LocalSnackbar.current
    val scope = rememberCoroutineScope()
    val noMic = stringResource(R.string.voice_no_mic)
    val askMic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) voice.start() else scope.launch { snack.showSnackbar(noMic) }
    }
    val startTalk = {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) voice.start()
        else askMic.launch(Manifest.permission.RECORD_AUDIO)
    }
    talk?.let { VoiceSheet(it, onEnd = voice::end, onAgain = startTalk, onClose = voice::close) }
    var draft by rememberSaveable { mutableStateOf("") }
    var menu by remember { mutableStateOf(false) }
    val colors = SenaTheme.colors

    if (ui.confirmClear) {
        ConfirmDialog(stringResource(R.string.chat_clear_title), stringResource(R.string.chat_clear_text), stringResource(R.string.action_delete),
            onConfirm = viewModel::clear, onDismiss = viewModel::dismissClear, destructive = true)
    }

    val load = ui.load
    SenaScreen(
        title = stringResource(R.string.nav_chat),
        problem = load.problem,
        onRetry = viewModel::refresh,
        onBack = onGoHome,
        actions = {
            Box {
                SenaIconButton(R.drawable.ic_dots, stringResource(R.string.cd_more_actions), { menu = true }, tint = colors.onHeader, size = Size.touchHeader)
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }, containerColor = colors.card) {
                    DropdownMenuItem(
                        text = { Text(stringResource(R.string.chat_clear), color = colors.text) },
                        leadingIcon = { SenaIcon(R.drawable.ic_trash, null, tint = colors.text) },
                        onClick = { menu = false; viewModel.askClear() },
                    )
                }
            }
        },
    ) {
        Column(Modifier.weight(1f).fillMaxWidth().testTag("chat_list_host")) {
            when {
                load is Load.Loading -> LoadingBox()
                load is Load.Failed -> ErrorState(load.error.toUiText().asString(), viewModel::refresh)
                else -> {
                    if (ui.bubbles.isEmpty()) {
                        EmptyState(R.drawable.ic_chat, stringResource(R.string.chat_empty), hint = stringResource(R.string.chat_empty_hint))
                    } else {
                        PullRefresh(viewModel.loader) { Messages(ui.bubbles, viewModel::retry) }
                    }
                }
            }
        }
        Composer(
            draft, { draft = it }, ui.working, ui.step, ui.offline,
            onSend = { viewModel.send(draft); draft = "" },
            onStop = viewModel::stopTask,
            onTalk = if (voice.available) startTalk else null,
        )
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun Messages(bubbles: List<Bubble>, onRetry: (Bubble) -> Unit) {
    val listState = rememberLazyListState()
    LaunchedEffect(bubbles.size, bubbles.lastOrNull()?.id) {
        if (bubbles.isNotEmpty()) listState.scrollToItem(bubbles.lastIndex)
    }
    val clipboard = LocalClipboard.current
    val snack = LocalSnackbar.current
    val copied = stringResource(R.string.copied)
    val scope = rememberCoroutineScope()
    val locale = LocalConfiguration.current.locales[0]
    val today = stringResource(R.string.day_today)
    val yesterday = stringResource(R.string.day_yesterday)
    LazyColumn(
        Modifier.fillMaxSize().testTag("chat_list"), state = listState,
        contentPadding = PaddingValues(horizontal = Space.screen, vertical = Space.gap),
        verticalArrangement = Arrangement.spacedBy(Space.gap),
    ) {
        var previousDay: LocalDate? = null
        val rows = bubbles.map { b ->
            val day = Fmt.day(b.time)
            val header = if (day != previousDay) dayTitle(day, today, yesterday, locale) else null
            previousDay = day
            b to header
        }
        items(rows.size, key = { rows[it].first.id.ifBlank { "${rows[it].first.time}:${rows[it].first.text.hashCode()}" } }) { i ->
            val (bubble, header) = rows[i]
            Column(Modifier.fillMaxWidth().animateItem(fadeInSpec = null), verticalArrangement = Arrangement.spacedBy(Space.gap)) {
                if (header != null) Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) { Chip(header) }
                BubbleRow(bubble, onRetry) {
                    scope.launch {
                        clipboard.setClipEntry(ClipEntry(ClipData.newPlainText("Sena", bubble.text)))
                        snack.showSnackbar(copied)
                    }
                }
            }
        }
    }
}

private fun dayTitle(day: LocalDate, today: String, yesterday: String, locale: java.util.Locale): String {
    val now = LocalDate.now()
    return when (day) {
        now -> today
        now.minusDays(1) -> yesterday
        else -> DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM).withLocale(locale).format(day)
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun BubbleRow(bubble: Bubble, onRetry: (Bubble) -> Unit, onCopy: () -> Unit) {
    val colors = SenaTheme.colors
    val mine = bubble.fromUser
    val error = bubble.error
    val locale = LocalConfiguration.current.locales[0]
    val maxWidth = (LocalConfiguration.current.screenWidthDp * 0.8f).dp
    Row(
        Modifier.fillMaxWidth().rise(0, "bubble-" + bubble.id, if (mine) TransformOrigin(1f, 1f) else TransformOrigin(0f, 1f)),
        horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start,
    ) {
        Column(horizontalAlignment = if (mine) Alignment.End else Alignment.Start, verticalArrangement = Arrangement.spacedBy(Space.tiny)) {
            Surface(
                shape = if (mine) Radius.bubbleMine else Radius.bubbleTheirs,
                color = if (mine) colors.ink else colors.card,
                contentColor = if (mine) colors.onInk else colors.text,
                border = when {
                    error != null -> BorderStroke(Size.line, Coral)
                    mine -> null
                    else -> BorderStroke(Size.hairline, colors.cardBorder)
                },
                modifier = Modifier.widthIn(max = maxWidth).combinedClickable(onClick = {}, onLongClick = onCopy),
            ) {
                Column(Modifier.padding(horizontal = Space.inset, vertical = Space.gap), verticalArrangement = Arrangement.spacedBy(Space.small)) {
                    if (error != null) {
                        val retry = bubble.retryText != null   // no "Retry" where the Mac may already be at it: it would run twice
                        Row(horizontalArrangement = Arrangement.spacedBy(Space.small), verticalAlignment = Alignment.CenterVertically) {
                            SenaIcon(R.drawable.ic_alert, null, tint = colors.badText, size = Size.iconSmall)
                            Text(stringResource(if (retry) R.string.chat_failed_prefix else R.string.chat_lost_prefix), style = MaterialTheme.typography.labelLarge, color = colors.badText)
                        }
                        Text(error.asString(), style = MaterialTheme.typography.bodyLarge)
                        if (retry) SenaButton(stringResource(R.string.chat_error_retry), { onRetry(bubble) }, icon = R.drawable.ic_refresh)
                    } else {
                        Text(simpleMarkdown(bubble.text), style = MaterialTheme.typography.bodyLarge)
                    }
                }
            }
            val time = timeLabel(bubble.time, locale)
            if (time.isNotBlank()) Muted(time, Modifier.padding(horizontal = Space.small), style = MaterialTheme.typography.labelSmall)
        }
    }
}

private fun timeLabel(time: Double, locale: java.util.Locale): String = if (time > 0) Fmt.hhmm(time, locale = locale) else ""

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Composer(draft: String, onDraft: (String) -> Unit, working: Boolean, step: String?, offline: Boolean, onSend: () -> Unit, onStop: () -> Unit, onTalk: (() -> Unit)?) {
    val colors = SenaTheme.colors
    val keyboard = WindowInsets.isImeVisible
    Column(
        Modifier.fillMaxWidth().imePadding().padding(horizontal = Space.screen).padding(top = Space.small, bottom = if (keyboard) Space.small else LocalBottomPad.current),
        verticalArrangement = Arrangement.spacedBy(Space.small),
    ) {
        AnimatedVisibility(visible = working, enter = fadeIn(tween(Motion.medium)) + expandVertically(tween(Motion.medium)), exit = fadeOut(tween(Motion.short)) + shrinkVertically(tween(Motion.short))) {
            Row(
                Modifier.testTag("chat_step").background(colors.chip, Radius.pill).padding(horizontal = Space.inset, vertical = Space.small),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(Space.small),
            ) {
                TypingDots(colors.text)
                // the connection to a running request is lost for now: it goes on on the Mac, and its answer will come here
                Text(
                    if (offline) stringResource(R.string.chat_connection_lost) else step?.takeIf { it.isNotBlank() } ?: stringResource(R.string.chat_thinking),
                    style = MaterialTheme.typography.labelLarge, color = colors.text, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
        }
        Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(Space.small)) {
            SenaField(
                draft, onDraft, Modifier.weight(1f).testTag("chat_input"),
                placeholder = stringResource(R.string.chat_hint), maxLines = 5,
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            )
            // an empty field offers the voice instead of sending (as in messengers); while Sena works — "stop"
            val button = when {
                working -> ComposerButton.Stop
                draft.isBlank() && onTalk != null -> ComposerButton.Talk
                else -> ComposerButton.Send
            }
            AnimatedContent(button, transitionSpec = popSwap(), label = "sendStop") { shown ->
                when (shown) {
                    ComposerButton.Stop -> RoundButton(R.drawable.ic_stop, stringResource(R.string.chat_stop_cd), onStop, Modifier.testTag("chat_stop"), danger = true)
                    ComposerButton.Talk -> RoundButton(R.drawable.ic_mic, stringResource(R.string.voice_talk_cd), { onTalk?.invoke() }, Modifier.testTag("chat_talk"))
                    ComposerButton.Send -> RoundButton(R.drawable.ic_send, stringResource(R.string.chat_send_cd), onSend, Modifier.testTag("chat_send"), enabled = draft.isNotBlank())
                }
            }
        }
    }
}

private enum class ComposerButton { Send, Stop, Talk }
