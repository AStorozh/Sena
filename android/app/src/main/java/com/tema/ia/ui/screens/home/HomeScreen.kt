package com.tema.ia.ui.screens.home

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.tema.ia.R
import com.tema.ia.data.Overview
import com.tema.ia.data.PendingSend
import com.tema.ia.data.Question
import com.tema.ia.data.TaskItem
import com.tema.ia.ui.CollectMessages
import com.tema.ia.ui.Fmt
import com.tema.ia.ui.Load
import com.tema.ia.ui.PollWhileVisible
import com.tema.ia.ui.PullRefresh
import com.tema.ia.ui.asString
import com.tema.ia.ui.components.ButtonRow
import com.tema.ia.ui.components.ButtonStyle
import com.tema.ia.ui.components.ConfirmDialog
import com.tema.ia.ui.components.DotBar
import com.tema.ia.ui.components.ErrorState
import com.tema.ia.ui.components.IconTile
import com.tema.ia.ui.components.LeadCard
import com.tema.ia.ui.components.LoadingBox
import com.tema.ia.ui.components.LocalBottomPad
import com.tema.ia.ui.components.Muted
import com.tema.ia.ui.components.SwapText
import com.tema.ia.ui.components.rise
import com.tema.ia.ui.theme.LocalMuted
import com.tema.ia.ui.components.RoundButton
import com.tema.ia.ui.components.RowCard
import com.tema.ia.ui.components.SectionTitle
import com.tema.ia.ui.components.SenaButton
import com.tema.ia.ui.components.SenaCard
import com.tema.ia.ui.components.SenaField
import com.tema.ia.ui.components.SenaLeaf
import com.tema.ia.ui.components.SenaScreen
import com.tema.ia.ui.components.Dot
import com.tema.ia.ui.problem
import com.tema.ia.ui.theme.Coral
import com.tema.ia.ui.theme.Lime
import com.tema.ia.ui.theme.Motion
import com.tema.ia.ui.theme.Mustard
import com.tema.ia.ui.theme.Radius
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Sky
import com.tema.ia.ui.theme.Space
import com.tema.ia.ui.toUiText
import com.tema.ia.ui.valueOrNull
import java.time.LocalDate
import java.time.format.DateTimeFormatter

@Composable
fun HomeScreen(viewModel: HomeViewModel, onOpenTask: (String) -> Unit, modifier: Modifier = Modifier, onOpenTasks: () -> Unit = {}) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    PollWhileVisible(viewModel.loader)
    CollectMessages(viewModel.messages)

    when (ui.confirm) {
        HomeConfirm.TurnOff -> ConfirmDialog(
            stringResource(R.string.confirm_off_title), stringResource(R.string.confirm_off_text), stringResource(R.string.confirm_off_action),
            onConfirm = viewModel::confirmed, onDismiss = viewModel::dismissConfirm, destructive = true,
        )
        HomeConfirm.StopAll -> ConfirmDialog(
            stringResource(R.string.confirm_stop_all_title), stringResource(R.string.confirm_stop_all_text), stringResource(R.string.confirm_stop_all_action),
            onConfirm = viewModel::confirmed, onDismiss = viewModel::dismissConfirm, destructive = true,
        )
        null -> Unit
    }

    val load = ui.load
    val overview = load.valueOrNull
    val moving = ui.power == Power.Starting || ui.power == Power.Stopping
    val locale = LocalConfiguration.current.locales[0]
    val colors = SenaTheme.colors
    val on = ui.power == Power.On
    val breath = if (on) {
        rememberInfiniteTransition(label = "leaf").animateFloat(1f, 1.12f, infiniteRepeatable(tween(Motion.leaf), RepeatMode.Reverse), label = "breath")
    } else null

    SenaScreen(
        modifier,
        title = weekday(locale),
        nav = true,
        problem = load.problem.takeIf { !moving },   // while Sena starts or stops the port changes hands: a gap there is expected
        onRetry = viewModel::refresh,
        leading = {
            SenaLeaf(Modifier.graphicsLayer { scaleX = breath?.value ?: 1f; scaleY = breath?.value ?: 1f }.testTag("home_leaf"), size = Size.leafTop, tint = colors.onHeader, description = stringResource(R.string.cd_leaf))
        },
        actions = {
            Text(
                LocalDate.now().format(DateTimeFormatter.ofPattern("d MMMM", locale)),
                style = MaterialTheme.typography.labelLarge, color = colors.onHeaderMuted,
            )
        },
    ) {
        PullRefresh(viewModel.loader) {
            when {
                load is Load.Loading -> LoadingBox()
                load is Load.Failed && ui.power == Power.Offline -> ErrorState(load.error.toUiText().asString(), viewModel::refresh)
                else -> HomeContent(ui, overview, viewModel, onOpenTask, onOpenTasks)
            }
        }
    }
}

@Composable
private fun weekday(locale: java.util.Locale): String =
    LocalDate.now().dayOfWeek.getDisplayName(java.time.format.TextStyle.FULL_STANDALONE, locale).replaceFirstChar { it.uppercase(locale) }

@Composable
private fun HomeContent(ui: HomeUi, overview: Overview?, viewModel: HomeViewModel, onOpenTask: (String) -> Unit, onOpenTasks: () -> Unit) {
    val image by viewModel.image.collectAsStateWithLifecycle()
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = Space.screen).padding(top = Space.screen, bottom = LocalBottomPad.current),
        verticalArrangement = Arrangement.spacedBy(Space.gap),
    ) {
        var step = 0
        if (overview != null) {
            overview.question?.let { q ->
                QuestionCard(q, image?.takeIf { it.first == q.id }?.second, ui.busy, { viewModel.answer(it, q.id) }, Modifier.rise(step++, "question"))
            }
            overview.pendingSends.forEach { send ->
                SendCard(send, ui.busy, { viewModel.confirmSend(send.id) }, { viewModel.cancelSend(send.id) }, Modifier.rise(step++, "send-" + send.id))
            }
        }
        PowerCard(ui, viewModel, Modifier.rise(step++, "power"))
        if (overview != null && ui.power != Power.Offline) TodayCard(overview, Modifier.rise(step++, "today"))
        val mine = overview?.ownWork.orEmpty()
        if (overview != null && ui.power != Power.Offline && mine.isNotEmpty()) {
            Running(mine, viewModel, onOpenTask, onOpenTasks, Modifier.rise(step++, "running"))
        }
    }
}

/** Ф-11: what today cost (and of how much, when there is a daily limit) and how many errors the journal has. */
@Composable
private fun TodayCard(overview: Overview, modifier: Modifier = Modifier) {
    val locale = LocalConfiguration.current.locales[0]
    SenaCard(modifier.fillMaxWidth().testTag("home_today")) {
        Text(stringResource(R.string.home_today_title), style = MaterialTheme.typography.titleMedium)
        val spent = Fmt.money(overview.spentToday, locale)
        if (overview.budgetLimit > 0) {
            Muted(stringResource(R.string.home_today_spent_of, spent, Fmt.money(overview.budgetLimit, locale)))
            DotBar((overview.spentToday / overview.budgetLimit).toFloat())
        } else {
            Muted(stringResource(R.string.home_today_spent, spent))
        }
        Muted(stringResource(R.string.home_today_errors, overview.errorsToday))
    }
}

@Composable
private fun PowerCard(ui: HomeUi, viewModel: HomeViewModel, modifier: Modifier = Modifier) {
    val colors = SenaTheme.colors
    val on = ui.power == Power.On
    val working = ui.busy || ui.power == Power.Starting || ui.power == Power.Stopping
    val clickable = (ui.power == Power.On || ui.power == Power.Off) && !ui.busy
    val description = stringResource(
        when {
            working -> R.string.power_busy_cd
            on -> R.string.power_off_cd
            else -> R.string.power_on_cd
        },
    )
    val tint = when (ui.power) {
        Power.On -> Lime
        Power.Starting, Power.Stopping -> Mustard
        Power.Off, Power.Offline -> Coral
        Power.Unknown -> colors.tint(0)
    }
    SenaCard(modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.gap)) {
            IconTile(R.drawable.ic_sena_leaf, tint)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Space.tiny)) {
                Text(
                    stringResource(
                        when (ui.power) {
                            Power.On -> R.string.home_state_on
                            Power.Off -> R.string.home_state_off
                            Power.Starting -> R.string.home_state_starting
                            Power.Stopping -> R.string.home_state_stopping
                            Power.Offline -> R.string.home_state_offline
                            Power.Unknown -> R.string.home_state_loading
                        },
                    ),
                    style = MaterialTheme.typography.titleLarge, modifier = Modifier.testTag("home_state"),
                )
                SwapText(
                    stringResource(
                        when (ui.power) {
                            Power.On -> R.string.home_hint_on
                            Power.Off -> R.string.home_hint_off
                            Power.Starting, Power.Stopping -> R.string.home_hint_busy
                            Power.Offline -> R.string.home_hint_offline
                            Power.Unknown -> R.string.home_hint_busy
                        },
                    ),
                    color = LocalMuted.current,
                )
            }
            RoundButton(
                R.drawable.ic_power, description, viewModel::onPower,
                Modifier.testTag("home_power"), danger = on, enabled = clickable || working, busy = working,
            )
        }
    }
}

@Composable
private fun Running(active: List<TaskItem>, viewModel: HomeViewModel, onOpenTask: (String) -> Unit, onOpenTasks: () -> Unit, modifier: Modifier = Modifier) {
    val res = LocalContext.current.resources
    Column(modifier, verticalArrangement = Arrangement.spacedBy(Space.small)) {
        SectionTitle(stringResource(R.string.home_active, active.size), modifier = Modifier.clickable(onClick = onOpenTasks), trailing = {
            SenaButton(stringResource(R.string.home_stop_all), viewModel::onStopAll, style = ButtonStyle.Text, icon = R.drawable.ic_stop)
        })
        active.forEach { task ->
            RowCard(
                title = task.shown,
                modifier = Modifier.fillMaxWidth(),
                subtitle = Fmt.ago(res, task.start, System.currentTimeMillis() / 1000.0),
                onClick = { onOpenTask(task.id) },
                leading = { IconTile(R.drawable.ic_tasks, Sky) },
                below = { DotBar(null, Modifier.padding(top = Space.small)) },
            )
        }
    }
}

@Composable
private fun QuestionCard(question: Question, image: ByteArray?, busy: Boolean, onAnswer: (String) -> Unit, modifier: Modifier = Modifier) {
    var answer by rememberSaveable { mutableStateOf("") }
    val picture = remember(image) { image?.let { BitmapFactory.decodeByteArray(it, 0, it.size)?.asImageBitmap() } }
    LeadCard(modifier.fillMaxWidth().testTag("home_question")) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.small)) {
            Dot(Coral)
            Text(stringResource(R.string.home_question_title), style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            if (question.count > 1) Muted(stringResource(R.string.home_question_more, question.count - 1), style = MaterialTheme.typography.labelMedium)
        }
        Text(question.text, style = MaterialTheme.typography.bodyLarge)
        if (picture != null) {
            Image(picture, stringResource(R.string.home_question_image_cd), Modifier.fillMaxWidth().clip(Radius.tile).testTag("home_question_image"), contentScale = ContentScale.FillWidth)
        }
        SenaField(answer, { answer = it }, Modifier.fillMaxWidth(), placeholder = stringResource(R.string.home_question_hint))
        SenaButton(stringResource(R.string.home_question_send), { onAnswer(answer); answer = "" }, Modifier.fillMaxWidth(), enabled = answer.isNotBlank() && !busy)
    }
}

/** Something prepared that waits for the owner's "yes": a message to someone, a change to the site, an agent's task, a secret. */
@Composable
private fun SendCard(send: PendingSend, busy: Boolean, onSend: () -> Unit, onCancel: () -> Unit, modifier: Modifier = Modifier) {
    LeadCard(modifier.fillMaxWidth().testTag("home_send")) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.small)) {
            Dot(Mustard)
            Text(stringResource(R.string.home_send_title), style = MaterialTheme.typography.titleMedium)
        }
        if (send.name.isNotBlank()) Text(send.name, style = MaterialTheme.typography.titleMedium)
        if (send.text.isNotBlank()) Text(send.text, style = MaterialTheme.typography.bodyLarge)
        ButtonRow {
            SenaButton(stringResource(R.string.home_send_confirm), onSend, enabled = !busy)
            SenaButton(stringResource(R.string.action_cancel), onCancel, style = ButtonStyle.Outline, enabled = !busy)
        }
    }
}
