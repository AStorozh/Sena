package com.tema.ia.ui.screens.integrations

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.tema.ia.R
import com.tema.ia.data.IntegrationCatalog
import com.tema.ia.data.IntegrationState
import com.tema.ia.ui.CollectMessages
import com.tema.ia.ui.Fmt
import com.tema.ia.ui.Load
import com.tema.ia.ui.PollWhileVisible
import com.tema.ia.ui.PullRefresh
import com.tema.ia.ui.asString
import com.tema.ia.ui.components.ErrorState
import com.tema.ia.ui.components.IconTile
import com.tema.ia.ui.components.LeadCard
import com.tema.ia.ui.components.LoadingBox
import com.tema.ia.ui.components.Muted
import com.tema.ia.ui.components.RowCard
import com.tema.ia.ui.components.SectionTitle
import com.tema.ia.ui.components.SenaCard
import com.tema.ia.ui.components.SenaScreen
import com.tema.ia.ui.components.SenaSheet
import com.tema.ia.ui.components.SenaSwitch
import com.tema.ia.ui.components.rise
import com.tema.ia.ui.components.StatusChip
import com.tema.ia.ui.components.isBigFont
import com.tema.ia.ui.components.listPadding
import com.tema.ia.ui.problem
import com.tema.ia.ui.theme.Coral
import com.tema.ia.ui.theme.Lime
import com.tema.ia.ui.theme.Mustard
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Space
import com.tema.ia.ui.toUiText

private fun linkLabel(link: Link): Int = when (link) {
    Link.Working -> R.string.int_status_working
    Link.Disabled -> R.string.int_status_off
    Link.NoKey -> R.string.int_status_nokey
    Link.Unused -> R.string.int_status_unused
    Link.Problem -> R.string.int_status_problem
    Link.Part -> R.string.int_status_working   // part of Sena herself: it works, but there's no switch
    Link.NotInstalled -> R.string.int_status_not_installed
}

@Composable
private fun linkColor(link: Link): Color = when (link) {
    Link.Working, Link.Part -> Lime
    Link.Disabled, Link.Unused -> SenaTheme.colors.outline
    Link.NoKey, Link.NotInstalled -> Mustard
    Link.Problem -> Coral
}

private val groupTitles = mapOf(
    IntegrationCatalog.Group.Comm to R.string.int_group_comm,
    IntegrationCatalog.Group.Work to R.string.int_group_work,
    IntegrationCatalog.Group.Data to R.string.int_group_data,
    IntegrationCatalog.Group.Sena to R.string.int_group_sena,
)

@Composable
fun IntegrationsScreen(viewModel: IntegrationsViewModel, onBack: () -> Unit) {
    val load by viewModel.loader.state.collectAsStateWithLifecycle()
    val selected by viewModel.selected.collectAsStateWithLifecycle()
    PollWhileVisible(viewModel.loader)
    CollectMessages(viewModel.messages)

    val data = (load as? Load.Ready)?.value
    selected?.let { id ->
        IntegrationCatalog.by(id)?.let { spec -> DetailSheet(spec, data?.states?.get(id), data?.keys.orEmpty(), viewModel::close) }
    }

    SenaScreen(
        title = stringResource(R.string.int_title),
        onBack = onBack,
        problem = load.problem, onRetry = viewModel.loader::refresh,
    ) {
        when (val l = load) {
            is Load.Loading -> LoadingBox()
            is Load.Failed -> ErrorState(l.error.toUiText().asString(), viewModel.loader::refresh)
            is Load.Ready -> PullRefresh(viewModel.loader) {
                LazyColumn(
                    Modifier.fillMaxSize().testTag("integrations_list"),
                    contentPadding = listPadding(top = Space.section), verticalArrangement = Arrangement.spacedBy(Space.gap),
                ) {
                    var position = 0
                    for (group in IntegrationCatalog.Group.entries) {
                        val specs = IntegrationCatalog.all.filter { it.group == group }
                        item(key = "group-$group") { SectionTitle(stringResource(groupTitles.getValue(group)), Modifier.rise(0, "group-$group")) }
                        for (spec in specs) {
                            val index = position++
                            item(key = spec.id) { IntegrationRow(spec, l.value.states[spec.id], l.value.keys, viewModel, Modifier.animateItem(fadeInSpec = null).rise(index, "int-" + spec.id)) }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun IntegrationRow(spec: IntegrationCatalog.Spec, state: IntegrationState?, keys: Map<String, Boolean>, viewModel: IntegrationsViewModel, modifier: Modifier = Modifier) {
    val link = linkOf(spec, state, keys)
    val name = stringResource(spec.name)
    val status = stringResource(linkLabel(link))
    val toggleCd = stringResource(R.string.int_toggle_cd, name)
    val uses = if (state != null && state.usesToday > 0) stringResource(R.string.int_uses_today, state.usesToday) else null
    RowCard(
        title = name,
        subtitle = stringResource(spec.role),
        modifier = modifier.fillMaxWidth().testTag("integration_row"),
        onClick = { viewModel.open(spec.id) },
        maxSubtitleLines = 2,
        maxTitleLines = 3,
        stackTrailing = isBigFont(),
        leading = { IconTile(R.drawable.ic_integrations, SenaTheme.colors.tintOf(spec.id)) },
        below = {
            Row(Modifier.padding(top = Space.small), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.small)) {
                StatusChip(status, linkColor(link))
                if (uses != null) Muted(uses, style = MaterialTheme.typography.labelMedium)
            }
        },
        trailing = {
            if (state?.canToggle == true && state.enabled != null) {
                SenaSwitch(state.enabled, { viewModel.toggle(spec.id, it) }, Modifier.semantics { contentDescription = toggleCd }.testTag("integration_switch"))
            }
        },
    )
}

@Composable
private fun DetailSheet(spec: IntegrationCatalog.Spec, state: IntegrationState?, keys: Map<String, Boolean>, onDismiss: () -> Unit) {
    val locale = LocalConfiguration.current.locales[0]
    val res = LocalContext.current.resources
    val link = linkOf(spec, state, keys)
    SenaSheet(stringResource(spec.name), onDismiss) {
        Column(Modifier.testTag("integration_detail"), verticalArrangement = Arrangement.spacedBy(Space.gap)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.gap)) {
                StatusChip(stringResource(linkLabel(link)), linkColor(link))
                if (state != null) {
                    Muted(
                        stringResource(R.string.int_uses_today, state.usesToday) + if (state.usdToday > 0) " · " + stringResource(R.string.int_cost_today, Fmt.money(state.usdToday, locale)) else "",
                        style = MaterialTheme.typography.labelMedium,
                    )
                }
            }
            Text(stringResource(spec.role), style = MaterialTheme.typography.bodyLarge)
            LeadCard(Modifier.fillMaxWidth()) {
                Muted(stringResource(R.string.int_how_title), style = MaterialTheme.typography.labelLarge)
                Text(stringResource(spec.how), style = MaterialTheme.typography.bodyLarge)
            }
            if (spec.keys.isNotEmpty()) {
                SenaCard(Modifier.fillMaxWidth()) {
                    Muted(stringResource(R.string.int_keys_title), style = MaterialTheme.typography.labelLarge)
                    spec.keys.forEach { key ->
                        val word = when (keys[key]) { true -> stringResource(R.string.int_key_set); false -> stringResource(R.string.int_key_missing); null -> "?" }
                        Text(stringResource(R.string.int_key_line, key, word), style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
            state?.lastUsed?.let { Muted(Fmt.ago(res, it, System.currentTimeMillis() / 1000.0)) }
            state?.lastError?.let { Text(stringResource(R.string.int_last_error, it), style = MaterialTheme.typography.bodyMedium, color = SenaTheme.colors.badText) }
        }
    }
}
