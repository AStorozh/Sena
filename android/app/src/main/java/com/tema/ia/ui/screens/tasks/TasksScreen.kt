package com.tema.ia.ui.screens.tasks

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
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
import com.tema.ia.data.TaskItem
import com.tema.ia.data.Verdict
import com.tema.ia.ui.Fmt
import com.tema.ia.ui.Load
import com.tema.ia.ui.PollWhileVisible
import com.tema.ia.ui.PullRefresh
import com.tema.ia.ui.asString
import com.tema.ia.ui.components.DotBar
import com.tema.ia.ui.components.EmptyState
import com.tema.ia.ui.components.ErrorState
import com.tema.ia.ui.components.LoadingBox
import com.tema.ia.ui.components.Muted
import com.tema.ia.ui.components.SegmentTabs
import com.tema.ia.ui.components.SenaCard
import com.tema.ia.ui.components.SenaIconButton
import com.tema.ia.ui.components.SenaLeaf
import com.tema.ia.ui.components.SenaScreen
import com.tema.ia.ui.components.SenaSwitch
import com.tema.ia.ui.components.rise
import com.tema.ia.ui.components.StatusChip
import com.tema.ia.ui.components.listPadding
import com.tema.ia.ui.problem
import com.tema.ia.ui.theme.Coral
import com.tema.ia.ui.theme.Lime
import com.tema.ia.ui.theme.Mustard
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Sky
import com.tema.ia.ui.theme.Space
import com.tema.ia.ui.toUiText
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.graphics.Color

@Composable
fun verdictLabel(verdict: Verdict?, running: Boolean): String = stringResource(
    when {
        running -> R.string.task_running
        verdict == Verdict.Done -> R.string.verdict_done
        verdict == Verdict.DoneWithRemarks -> R.string.verdict_remarks
        verdict == Verdict.Failed -> R.string.verdict_failed
        verdict == Verdict.Unverified -> R.string.verdict_unverified   // the judge's honest "not enough facts to tell"
        else -> R.string.verdict_none
    },
)

fun verdictColor(verdict: Verdict?, running: Boolean): Color = when {
    running -> Sky
    verdict == Verdict.Done -> Lime
    verdict == Verdict.DoneWithRemarks -> Mustard
    verdict == Verdict.Failed -> Coral
    else -> Sky
}

/** A step's "who" ("ag_web", "ag_code/Sena") into a name the owner recognizes. */
@Composable
fun agentLabel(who: String): String {
    val base = who.substringBefore('/')
    val extra = who.substringAfter('/', "")
    val name = stringResource(
        when (base) {
            "ag_sena" -> R.string.agent_sena
            "ag_web" -> R.string.agent_web
            "ag_mac" -> R.string.agent_mac_name
            "ag_code" -> R.string.agent_code
            "ag_judge" -> R.string.agent_judge
            "ag_night" -> R.string.agent_night
            "ag_ears" -> R.string.agent_ears
            else -> return base
        },
    )
    return if (extra.isBlank()) name else "$name: $extra"
}

@Composable
fun TasksScreen(viewModel: TasksViewModel, onOpen: (TaskItem) -> Unit) {
    val load by viewModel.loader.state.collectAsStateWithLifecycle()
    PollWhileVisible(viewModel.loader)
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var showService by rememberSaveable { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    val colors = SenaTheme.colors
    val l = load
    val counts = (l as? Load.Ready)?.let { viewModel.visible(it.value, showService) }

    SenaScreen(
        title = stringResource(R.string.tasks_title),
        nav = true,
        problem = l.problem,
        onRetry = viewModel.loader::refresh,
        leading = { SenaLeaf(size = Size.leafTop, tint = colors.onHeader, description = stringResource(R.string.cd_leaf)) },
        actions = {
            Box {
                SenaIconButton(R.drawable.ic_dots, stringResource(R.string.cd_more_actions), { menu = true }, tint = colors.onHeader, size = Size.touchHeader)
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }, containerColor = colors.card) {
                    DropdownMenuItem(
                        text = { Text(stringResource(R.string.tasks_show_service), color = colors.text) },
                        trailingIcon = { SenaSwitch(showService, null) },
                        onClick = { showService = !showService },
                    )
                }
            }
        },
    ) {
        when (l) {
            is Load.Loading -> LoadingBox()
            is Load.Failed -> ErrorState(l.error.toUiText().asString(), viewModel.loader::refresh)
            is Load.Ready -> {
                val (running, done) = counts ?: viewModel.visible(l.value, showService)
                SegmentTabs(
                    listOf(stringResource(R.string.tasks_tab_running) + if (running.isNotEmpty()) " (${running.size})" else "", stringResource(R.string.tasks_tab_done)),
                    tab, { tab = it }, Modifier.padding(top = Space.screen),
                )
                val rows = if (tab == 0) running else done
                PullRefresh(viewModel.loader, modifier = Modifier.weight(1f)) {
                    if (rows.isEmpty()) {
                        if (tab == 0) EmptyState(R.drawable.ic_tasks, stringResource(R.string.tasks_empty_running), hint = stringResource(R.string.tasks_empty_running_hint))
                        else EmptyState(R.drawable.ic_tasks, stringResource(R.string.tasks_empty_done))
                    } else {
                        TaskList(rows, l.value.now, onOpen)
                    }
                }
            }
        }
    }
}

@Composable
private fun TaskList(rows: List<TaskItem>, now: Double, onOpen: (TaskItem) -> Unit) {
    val res = LocalContext.current.resources
    val locale = LocalConfiguration.current.locales[0]
    val clock = if (now > 0) now else System.currentTimeMillis() / 1000.0
    LazyColumn(
        Modifier.fillMaxSize().testTag("tasks_list"),
        contentPadding = listPadding(top = Space.gap), verticalArrangement = Arrangement.spacedBy(Space.gap),
    ) {
        itemsIndexed(rows, key = { _, t -> t.id + t.start }) { index, task ->
            SenaCard(Modifier.fillMaxWidth().animateItem(fadeInSpec = null).rise(index, "task-" + task.id + task.start).testTag("task_row"), onClick = { onOpen(task) }, padding = Space.inset) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.small)) {
                    StatusChip(verdictLabel(task.outcome?.verdict, task.running), verdictColor(task.outcome?.verdict, task.running))
                    Box(Modifier.weight(1f))
                    Muted(Fmt.ago(res, task.start, clock), style = MaterialTheme.typography.labelMedium)
                }
                Text(task.shown, style = MaterialTheme.typography.titleMedium, maxLines = 4, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                if (task.running) {
                    // where it is now: which step, who works and what it does (the last step's words alone didn't say how far along)
                    val last = task.steps.lastOrNull()
                    Muted(
                        if (last != null) stringResource(R.string.tasks_stage, task.steps.size, agentLabel(last.who), last.text) else stringResource(R.string.tasks_stage_start),
                        style = MaterialTheme.typography.bodyMedium, maxLines = 2,
                    )
                    DotBar(null)
                } else {
                    val meta = taskMeta(task, res, locale)
                    if (meta.isNotBlank()) Muted(meta)
                }
            }
        }
    }
}

/** "2 min · $0.02 · service": what's known about a task besides its name and start time. */
fun taskMeta(task: TaskItem, res: android.content.res.Resources, locale: java.util.Locale): String {
    val parts = mutableListOf<String>()
    task.end?.let { parts += Fmt.duration(res, it - task.start) }
    if (task.cost > 0) parts += Fmt.money(task.cost, locale)
    if (task.service) parts += res.getString(R.string.task_service)
    return parts.joinToString(" · ")
}
