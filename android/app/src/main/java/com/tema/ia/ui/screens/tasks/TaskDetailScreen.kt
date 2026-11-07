package com.tema.ia.ui.screens.tasks

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.tema.ia.R
import com.tema.ia.data.OUTCOME_REASON
import com.tema.ia.data.TaskItem
import com.tema.ia.data.TaskStep
import com.tema.ia.ui.CollectMessages
import com.tema.ia.ui.Fmt
import com.tema.ia.ui.Load
import com.tema.ia.ui.PollWhileVisible
import com.tema.ia.ui.asString
import com.tema.ia.ui.components.ButtonStyle
import com.tema.ia.ui.components.DashedLine
import com.tema.ia.ui.components.Dot
import com.tema.ia.ui.components.ErrorState
import com.tema.ia.ui.components.LeadCard
import com.tema.ia.ui.components.LoadingBox
import com.tema.ia.ui.components.LocalBottomPad
import com.tema.ia.ui.components.Muted
import com.tema.ia.ui.components.SenaButton
import com.tema.ia.ui.components.SenaCard
import com.tema.ia.ui.components.SenaField
import com.tema.ia.ui.components.SenaScreen
import com.tema.ia.ui.components.rise
import com.tema.ia.ui.problem
import com.tema.ia.ui.simpleMarkdown
import com.tema.ia.ui.theme.Radius
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space
import com.tema.ia.ui.toUiText

@Composable
fun TaskDetailScreen(viewModel: TaskDetailViewModel, onBack: () -> Unit) {
    val load by viewModel.loader.state.collectAsStateWithLifecycle()
    PollWhileVisible(viewModel.loader)
    CollectMessages(viewModel.messages)
    val l = load
    val res = LocalContext.current.resources
    val locale = LocalConfiguration.current.locales[0]
    SenaScreen(
        title = stringResource(R.string.task_title),
        onBack = onBack,
        problem = l.problem,
        onRetry = viewModel.loader::refresh,
    ) {
        when (l) {
            is Load.Loading -> LoadingBox()
            is Load.Failed -> ErrorState(l.error.toUiText().asString(), viewModel.loader::refresh)
            is Load.Ready -> Detail(l.value, viewModel, res, locale)
        }
    }
}

@Composable
private fun Detail(task: TaskItem, viewModel: TaskDetailViewModel, res: android.content.res.Resources, locale: java.util.Locale) {
    var remark by rememberSaveable { mutableStateOf("") }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(horizontal = Space.screen).padding(top = Space.screen, bottom = LocalBottomPad.current).testTag("task_detail"),
        verticalArrangement = Arrangement.spacedBy(Space.gap),
    ) {
        SenaCard(Modifier.fillMaxWidth().rise(0, "td-head")) {
            Text(task.shown, style = MaterialTheme.typography.titleLarge)
            if (task.shown != task.text) Muted(stringResource(R.string.task_own_words, task.text), style = MaterialTheme.typography.bodyMedium)
            val verdict = verdictLabel(task.outcome?.verdict, task.running)
            val meta = listOf(Fmt.ago(res, task.start, System.currentTimeMillis() / 1000.0), taskMeta(task, res, locale)).filter { it.isNotBlank() }.joinToString(" · ")
            val line = listOf(verdict, meta).filter { it.isNotBlank() }.joinToString(" · ")
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.small)) {
                Dot(verdictColor(task.outcome?.verdict, task.running))
                Muted(line, Modifier.weight(1f), style = MaterialTheme.typography.labelMedium, maxLines = 1)
            }
            if (task.running) {
                Text(stringResource(R.string.task_current_agent, agentLabel(task.steps.lastOrNull()?.who ?: "ag_sena")), style = MaterialTheme.typography.titleSmall)
                SenaButton(stringResource(R.string.task_stop), viewModel::stopTask, icon = R.drawable.ic_stop, modifier = Modifier.fillMaxWidth())
            }
        }

        task.outcome?.reason?.takeIf { it.isNotBlank() && it != OUTCOME_REASON }?.let { reason ->
            SenaCard(Modifier.fillMaxWidth().rise(1, "td-reason")) { Muted(reason, style = MaterialTheme.typography.bodyLarge) }
        }

        LeadCard(Modifier.fillMaxWidth().rise(2, "td-answer")) {
            Text(stringResource(R.string.task_answer), style = MaterialTheme.typography.titleMedium)
            val answer = task.answer
            if (answer.isNullOrBlank()) Muted(stringResource(R.string.task_no_answer))
            else Text(simpleMarkdown(answer), style = MaterialTheme.typography.bodyLarge)
        }

        SenaCard(Modifier.fillMaxWidth().rise(3, "td-steps")) {
            Text(stringResource(R.string.task_steps), style = MaterialTheme.typography.titleMedium)
            if (task.steps.isEmpty()) Muted(stringResource(R.string.task_no_steps))
            val upcoming = task.next.takeIf { task.running && !it.isNullOrBlank() }
            // No spacing between rows here: each row's own bottom padding is what the rail fills,
            // so the line actually reaches the next dot instead of stopping short of it.
            Column {
                task.steps.forEachIndexed { index, step ->
                    val isLastReal = index == task.steps.lastIndex
                    Step(step, last = isLastReal && upcoming == null, dashedTrail = isLastReal && upcoming != null)
                }
                if (upcoming != null) UpcomingStep(upcoming)
            }
        }

        if (task.comments.isNotEmpty()) {
            SenaCard(Modifier.fillMaxWidth().rise(4, "td-comments").animateContentSize()) {
                Text(stringResource(R.string.task_comments), style = MaterialTheme.typography.titleMedium)
                task.comments.forEach { Text(it.text, style = MaterialTheme.typography.bodyMedium) }
            }
        }

        if (!task.running && !task.service) {
            SenaCard(Modifier.fillMaxWidth().rise(5, "td-rate")) {
                Text(stringResource(R.string.task_rate), style = MaterialTheme.typography.titleMedium)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(Space.small)) {
                    for (n in 1..5) {
                        SenaButton(
                            n.toString(), { viewModel.rate(n) }, Modifier.weight(1f),
                            style = if (task.rating == n) ButtonStyle.Primary else ButtonStyle.Outline, compact = true,
                        )
                    }
                }
                SenaField(remark, { remark = it }, Modifier.fillMaxWidth().testTag("task_remark"), placeholder = stringResource(R.string.task_comment_hint), minLines = 2)
                SenaButton(stringResource(R.string.task_comment_send), { viewModel.comment(remark); remark = "" }, Modifier.fillMaxWidth(), enabled = remark.isNotBlank())
            }
        }
    }
}

/** One step in the feed: a dot and a thin line on the left, time and text on the right.
 * [dashedTrail]: the line down to the next row is dashed when that next row is only a plan, not a done step. */
@Composable
private fun Step(step: TaskStep, last: Boolean, dashedTrail: Boolean = false) {
    val colors = SenaTheme.colors
    Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(Space.gap)) {
        // The dot sits flush with the row's top, so the rail above (from the previous row) lands exactly on it
        // instead of stopping short by the text's own top offset.
        Column(Modifier.width(Size.dotBig).fillMaxHeight(), horizontalAlignment = Alignment.CenterHorizontally) {
            Dot(colors.ink, size = Size.dotBig)
            if (dashedTrail) DashedLine(Modifier.weight(1f).width(Size.line), colors.cardBorder)
            else if (!last) Box(Modifier.weight(1f).width(Size.line).background(colors.cardBorder, Radius.pill))
        }
        Column(Modifier.weight(1f).padding(top = Space.tiny, bottom = if (last) Space.tiny else Space.gap), verticalArrangement = Arrangement.spacedBy(Space.tiny)) {
            val meta = listOfNotNull(agentLabel(step.who).takeIf { step.who.isNotBlank() }, step.time?.let { Fmt.hhmm(it) }).joinToString(" · ")
            if (meta.isNotBlank()) Muted(meta, style = MaterialTheme.typography.labelMedium)
            Text(step.text, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

/** What the agent itself expects to do next, while it's still only a guess: a hollow dot, no line below it. */
@Composable
private fun UpcomingStep(text: String) {
    val colors = SenaTheme.colors
    Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(Space.gap)) {
        Column(Modifier.width(Size.dotBig).fillMaxHeight(), horizontalAlignment = Alignment.CenterHorizontally) {
            Dot(colors.cardBorder, size = Size.dotBig)
        }
        Column(Modifier.weight(1f).padding(top = Space.tiny, bottom = Space.tiny), verticalArrangement = Arrangement.spacedBy(Space.tiny)) {
            Muted(stringResource(R.string.task_next_step, text), style = MaterialTheme.typography.bodyMedium)
        }
    }
}
