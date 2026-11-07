package com.tema.ia.ui.screens.rules

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.tema.ia.R
import com.tema.ia.data.RuleEntry
import com.tema.ia.data.RuleKind
import com.tema.ia.ui.CollectMessages
import com.tema.ia.ui.Fmt
import com.tema.ia.ui.Load
import com.tema.ia.ui.PollWhileVisible
import com.tema.ia.ui.PullRefresh
import com.tema.ia.ui.asString
import com.tema.ia.ui.components.ConfirmDialog
import com.tema.ia.ui.components.EmptyState
import com.tema.ia.ui.components.ErrorState
import com.tema.ia.ui.components.IconTile
import com.tema.ia.ui.components.LoadingBox
import com.tema.ia.ui.components.Muted
import com.tema.ia.ui.components.RowCard
import com.tema.ia.ui.components.SenaButton
import com.tema.ia.ui.components.SenaFab
import com.tema.ia.ui.components.SenaField
import com.tema.ia.ui.components.SenaIcon
import com.tema.ia.ui.components.SenaIconButton
import com.tema.ia.ui.components.SenaScreen
import com.tema.ia.ui.components.SenaSheet
import com.tema.ia.ui.components.SenaSwitch
import com.tema.ia.ui.components.rise
import com.tema.ia.ui.components.isBigFont
import com.tema.ia.ui.components.listPadding
import com.tema.ia.ui.problem
import com.tema.ia.ui.theme.Coral
import com.tema.ia.ui.theme.Lavender
import com.tema.ia.ui.theme.Mustard
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Space
import com.tema.ia.ui.toUiText

@Composable
fun RulesScreen(viewModel: RulesViewModel, onBack: () -> Unit) {
    val load by viewModel.loader.state.collectAsStateWithLifecycle()
    val adding by viewModel.adding.collectAsStateWithLifecycle()
    val deleting by viewModel.deleting.collectAsStateWithLifecycle()
    val saving by viewModel.saving.collectAsStateWithLifecycle()
    PollWhileVisible(viewModel.loader)
    CollectMessages(viewModel.messages)

    deleting?.let {
        ConfirmDialog(stringResource(R.string.rules_delete_title), stringResource(R.string.rules_delete_text, ruleTitle(it)), stringResource(R.string.action_delete),
            onConfirm = viewModel::confirmDelete, onDismiss = viewModel::dismissDelete, destructive = true)
    }
    if (adding) AddRuleSheet(saving, viewModel::closeAdd, viewModel::add)

    SenaScreen(
        title = stringResource(R.string.rules_title),
        onBack = onBack,
        problem = load.problem, onRetry = viewModel.loader::refresh,
        floating = { SenaFab(R.drawable.ic_plus, stringResource(R.string.rules_add), viewModel::openAdd, Modifier.testTag("rules_add")) },
    ) {
        when (val l = load) {
            is Load.Loading -> LoadingBox()
            is Load.Failed -> ErrorState(l.error.toUiText().asString(), viewModel.loader::refresh)
            is Load.Ready -> PullRefresh(viewModel.loader) {
                if (l.value.isEmpty()) {
                    EmptyState(R.drawable.ic_rules, stringResource(R.string.rules_empty), hint = stringResource(R.string.rules_empty_hint))
                } else {
                    LazyColumn(
                        Modifier.fillMaxSize().testTag("rules_list"),
                        contentPadding = listPadding(top = Space.screen, fab = true),
                        verticalArrangement = Arrangement.spacedBy(Space.gap),
                    ) {
                        itemsIndexed(l.value, key = { _, r -> r.kind.name + r.id }) { index, rule -> RuleRow(rule, viewModel, Modifier.animateItem(fadeInSpec = null).rise(index, "rule-" + rule.kind.name + rule.id)) }
                    }
                }
            }
        }
    }
}

/** How often a job repeats, in the app's language; null — a code this version doesn't know (it shows no word for it). */
@Composable
private fun repeatLabel(code: String?): String? = when (code) {
    "daily" -> stringResource(R.string.rules_repeat_daily)
    "weekdays" -> stringResource(R.string.rules_repeat_weekdays)
    "weekly" -> stringResource(R.string.rules_repeat_weekly)
    else -> null
}

/** A rule's words: the owner's own; for a price alert, built from its condition. */
@Composable
private fun ruleTitle(rule: RuleEntry): String {
    val alert = rule.alert ?: return rule.text
    val condition = stringResource(if (alert.above) R.string.rules_alert_above else R.string.rules_alert_below, rule.text, Fmt.number(alert.value))
    return if (alert.everyTime) stringResource(R.string.rules_alert_every, condition) else condition
}

@Composable
private fun RuleRow(rule: RuleEntry, viewModel: RulesViewModel, modifier: Modifier = Modifier) {
    var menu by remember { mutableStateOf(false) }
    val colors = SenaTheme.colors
    val res = LocalResources.current
    val icon = when (rule.kind) { RuleKind.Rule -> R.drawable.ic_rules; RuleKind.Job -> R.drawable.ic_calendar; RuleKind.Alert -> R.drawable.ic_bell }
    val tint = when (rule.kind) { RuleKind.Rule -> Lavender; RuleKind.Job -> Mustard; RuleKind.Alert -> Coral }
    val kindLabel = stringResource(when (rule.kind) { RuleKind.Rule -> R.string.rules_kind_rule; RuleKind.Job -> R.string.rules_kind_job; RuleKind.Alert -> R.string.rules_kind_alert })
    val repeat = repeatLabel(rule.repeat)
    val whenText = rule.at?.let { at -> repeat?.let { stringResource(R.string.rules_when_repeat, at, it) } ?: at }
    val subtitle = listOfNotNull(
        whenText ?: rule.note, kindLabel,
        rule.fails.takeIf { it > 0 }?.let { stringResource(R.string.rules_fails, it) },
        stringResource(R.string.rules_paused).takeIf { rule.paused && rule.enabled },
    ).joinToString(" · ")
    val title = ruleTitle(rule)
    val toggleCd = stringResource(R.string.rules_toggle_cd, title)
    val last = rule.last?.let { l ->
        l.time?.let { stringResource(R.string.rules_last, Fmt.ago(res, it, System.currentTimeMillis() / 1000.0), l.text) } ?: stringResource(R.string.rules_last_plain, l.text)
    }
    RowCard(
        title = title,
        subtitle = subtitle,
        modifier = modifier.fillMaxWidth().testTag("rule_row"),
        maxTitleLines = 6,
        maxSubtitleLines = 3,
        stackTrailing = isBigFont(),
        leading = { IconTile(icon, tint) },
        below = last?.let { { Muted(it, style = MaterialTheme.typography.bodySmall, maxLines = 2) } },
        trailing = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                SenaSwitch(rule.enabled, { viewModel.toggle(rule, it) }, Modifier.semantics { contentDescription = toggleCd }.testTag("rule_switch"))
                Box {
                    SenaIconButton(R.drawable.ic_dots, stringResource(R.string.cd_more_actions), { menu = true }, tint = colors.textMuted)
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }, containerColor = colors.card) {
                        DropdownMenuItem(
                            text = { Text(stringResource(R.string.action_delete), color = colors.text) },
                            leadingIcon = { SenaIcon(R.drawable.ic_trash, null, tint = colors.text) },
                            onClick = { menu = false; viewModel.askDelete(rule) },
                        )
                    }
                }
            }
        },
    )
}

@Composable
private fun AddRuleSheet(saving: Boolean, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var text by rememberSaveable { mutableStateOf("") }
    SenaSheet(stringResource(R.string.rules_add), onDismiss) {
        SenaField(text, { text = it }, Modifier.fillMaxWidth().testTag("rules_add_field"), placeholder = stringResource(R.string.rules_add_hint), minLines = 3, maxLines = 6)
        SenaButton(stringResource(R.string.action_save), { onSave(text) }, Modifier.fillMaxWidth().testTag("rules_add_save"), enabled = text.isNotBlank(), busy = saving, large = true)
    }
}
