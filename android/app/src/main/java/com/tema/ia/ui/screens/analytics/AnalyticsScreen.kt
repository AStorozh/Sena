package com.tema.ia.ui.screens.analytics

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
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.tema.ia.R
import com.tema.ia.data.CostBit
import com.tema.ia.data.CostsReport
import com.tema.ia.data.HomeReport
import com.tema.ia.data.QualityReport
import com.tema.ia.data.RoleInfo
import com.tema.ia.data.RegistryStats
import com.tema.ia.data.Verdict
import com.tema.ia.ui.CollectMessages
import com.tema.ia.ui.Fmt
import com.tema.ia.ui.Load
import com.tema.ia.ui.PollWhileVisible
import com.tema.ia.ui.PullRefresh
import com.tema.ia.ui.asString
import com.tema.ia.ui.components.DotLegend
import com.tema.ia.ui.components.DotLegendItem
import com.tema.ia.ui.components.Muted
import com.tema.ia.ui.components.SectionTitle
import com.tema.ia.ui.components.SenaButton
import com.tema.ia.ui.components.SenaCard
import com.tema.ia.ui.components.SenaScreen
import com.tema.ia.ui.components.SenaSwitch
import com.tema.ia.ui.components.SkeletonCard
import com.tema.ia.ui.components.listPadding
import com.tema.ia.ui.components.StatusChip
import com.tema.ia.ui.problem
import com.tema.ia.ui.screens.tasks.verdictColor
import com.tema.ia.ui.screens.tasks.verdictLabel
import com.tema.ia.ui.theme.BigNumberStyle
import com.tema.ia.ui.theme.Coral
import com.tema.ia.ui.theme.Lime
import com.tema.ia.ui.theme.Sky
import com.tema.ia.ui.theme.Space
import com.tema.ia.ui.toUiText
import java.time.LocalDate
import java.time.format.DateTimeFormatter

/**
 * A separate screen for analytical data — kept off the Home screen, which is for what needs attention now, not reports.
 * Spending and quality load through two independent loaders: a slow one shows its own loading/error state without
 * blocking the other section, and one pull-to-refresh gesture (further down) asks both at once.
 */
@Composable
fun AnalyticsScreen(viewModel: AnalyticsViewModel, onBack: () -> Unit) {
    val costsLoad by viewModel.costs.state.collectAsStateWithLifecycle()
    val qualityLoad by viewModel.quality.state.collectAsStateWithLifecycle()
    val registryLoad by viewModel.registry.state.collectAsStateWithLifecycle()
    val homeLoad by viewModel.home.state.collectAsStateWithLifecycle()
    PollWhileVisible(viewModel.costs, viewModel.quality, viewModel.registry, viewModel.home)
    CollectMessages(viewModel.messages)

    SenaScreen(
        title = stringResource(R.string.analytics_title),
        onBack = onBack,
        problem = costsLoad.problem ?: qualityLoad.problem ?: homeLoad.problem ?: registryLoad.problem,
        onRetry = { viewModel.costs.refresh(); viewModel.quality.refresh(); viewModel.registry.refresh(); viewModel.home.refresh() },
    ) {
        PullRefresh(viewModel.costs, viewModel.quality, viewModel.registry, viewModel.home) {
            LazyColumn(
                Modifier.fillMaxSize(),
                contentPadding = listPadding(top = Space.screen),
                verticalArrangement = Arrangement.spacedBy(Space.section),
            ) {
                item { Section(costsLoad, viewModel.costs::refresh) { CostsSection(it) } }
                item { Section(qualityLoad, viewModel.quality::refresh) { QualitySection(it) } }
                item { Section(homeLoad, viewModel.home::refresh) { HomeSection(it, viewModel::move) } }
                item { Section(registryLoad, viewModel.registry::refresh) { RegistrySection(it) } }
            }
        }
    }
}

/** Each section is bounded to its own card height — unlike [LoadingBox]/[ErrorState], which fill the whole screen
 * and would blow up the layout if two of them ended up stacked inside this screen's one scrolling column. */
@Composable
private fun <T> Section(load: Load<T>, onRetry: () -> Unit, content: @Composable (T) -> Unit) {
    when (load) {
        is Load.Loading -> SkeletonCard()
        is Load.Failed -> SenaCard(Modifier.fillMaxWidth()) {
            Text(load.error.toUiText().asString(), style = MaterialTheme.typography.bodyLarge)
            SenaButton(stringResource(R.string.action_retry), onRetry, icon = R.drawable.ic_refresh)
        }
        is Load.Ready -> content(load.value)
    }
}

@Composable
private fun CostsSection(report: CostsReport) {
    val locale = LocalConfiguration.current.locales[0]
    Column(verticalArrangement = Arrangement.spacedBy(Space.gap)) {
        SectionTitle(stringResource(R.string.analytics_costs_title))
        SenaCard(Modifier.fillMaxWidth()) {
            Muted(report.title, style = MaterialTheme.typography.labelLarge)
            Text(Fmt.money(report.total, locale), style = BigNumberStyle)
            Muted(stringResource(R.string.analytics_calls, report.calls), style = MaterialTheme.typography.bodySmall)
        }
        if (report.byDay.isNotEmpty()) BitList(stringResource(R.string.analytics_by_day), report.byDay, locale) { k -> dayLabel(k, locale) }
        if (report.topTasks.isNotEmpty()) BitList(stringResource(R.string.analytics_top_tasks), report.topTasks, locale) { it }   // the task's own words
        if (report.byModel.isNotEmpty()) BitList(stringResource(R.string.analytics_by_model), report.byModel, locale) { it }
    }
}

/** The home and the cloud: how many calls stayed on the Mac today, which switchable jobs work where (the owner moves them with
 * a switch: on — at home), and the fixed jobs for the full picture. A place is a word on a chip, never just a color. */
@Composable
private fun HomeSection(report: HomeReport, onMove: (String, Boolean) -> Unit) {
    val locale = LocalConfiguration.current.locales[0]
    Column(verticalArrangement = Arrangement.spacedBy(Space.gap)) {
        SectionTitle(stringResource(R.string.analytics_home_title))
        SenaCard(Modifier.fillMaxWidth()) {
            Muted(stringResource(R.string.analytics_home_today), style = MaterialTheme.typography.labelLarge)
            Text(stringResource(R.string.analytics_home_calls, report.homeToday), style = BigNumberStyle)
            Muted(stringResource(R.string.analytics_home_out, report.outToday, Fmt.money(report.usdToday, locale)), style = MaterialTheme.typography.bodySmall)
            if (report.homeByService.isNotEmpty()) {
                DotLegend(Modifier.fillMaxWidth()) {
                    report.homeByService.entries.sortedByDescending { it.value }.forEach { (service, n) -> DotLegendItem(Lime, "${serviceLabel(service)} $n") }
                }
            }
        }
        SenaCard(Modifier.fillMaxWidth()) {
            Muted(stringResource(R.string.analytics_home_switch_hint), style = MaterialTheme.typography.labelLarge)
            report.switchable.forEach { role -> RoleRow(role, report.minExamples, onMove) }
        }
        SenaCard(Modifier.fillMaxWidth()) {
            Muted(stringResource(R.string.analytics_home_fixed), style = MaterialTheme.typography.labelLarge)
            report.fixed.forEach { role -> RoleRow(role, report.minExamples, null) }
        }
    }
}

@Composable
private fun RoleRow(role: RoleInfo, minExamples: Int, onMove: ((String, Boolean) -> Unit)?) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(Space.small), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(role.title, style = MaterialTheme.typography.bodyLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
            val note = if (role.learning && !role.atHome && role.examples != null) stringResource(R.string.analytics_home_examples, role.examples, minExamples) else role.why
            Muted(note, style = MaterialTheme.typography.bodySmall)
        }
        StatusChip(stringResource(if (role.atHome) R.string.analytics_home_place_home else R.string.analytics_home_place_cloud), if (role.atHome) Lime else Sky)
        val id = role.role
        if (onMove != null && id != null) SenaSwitch(role.atHome, { onMove(id, it) })
    }
}

@Composable
private fun serviceLabel(service: String): String = stringResource(
    when (service) {
        "embeddings" -> R.string.analytics_home_svc_embed
        "chat" -> R.string.analytics_home_svc_chat
        "transcribe" -> R.string.analytics_home_svc_hearing
        "read" -> R.string.analytics_home_svc_reading
        "speech" -> R.string.analytics_home_svc_voice
        else -> R.string.analytics_home_svc_other
    }
)

/** The registry of memory in numbers: how much Sena can find, by kind, and how far it is from needing a faster vector index. */
@Composable
private fun RegistrySection(stats: RegistryStats) {
    val locale = LocalConfiguration.current.locales[0]
    Column(verticalArrangement = Arrangement.spacedBy(Space.gap)) {
        SectionTitle(stringResource(R.string.analytics_registry_title))
        SenaCard(Modifier.fillMaxWidth()) {
            Muted(stringResource(R.string.analytics_registry_hint), style = MaterialTheme.typography.labelLarge)
            Text(stringResource(R.string.analytics_registry_records, stats.total), style = BigNumberStyle)
            stats.items.entries.sortedByDescending { it.value }.forEach { (kind, n) -> NumberRow(kind, n.toString()) }
        }
        SenaCard(Modifier.fillMaxWidth()) {
            NumberRow(stringResource(R.string.analytics_registry_vectors), stats.vectors.toString())
            NumberRow(stringResource(R.string.analytics_registry_versions), stats.versions.toString())
            NumberRow(stringResource(R.string.analytics_registry_links), stats.links.toString())
            NumberRow(stringResource(R.string.analytics_registry_pending), stats.pending.toString())
            NumberRow(stringResource(R.string.analytics_registry_file_mb), String.format(locale, "%.1f", stats.fileMb))
            NumberRow(stringResource(R.string.analytics_registry_matrix_mb), String.format(locale, "%.1f", stats.matrixMb))
            NumberRow(stringResource(R.string.analytics_registry_search_ms), String.format(locale, "%.1f", stats.searchMs))
        }
        SenaCard(Modifier.fillMaxWidth()) {
            if (stats.overThreshold) StatusChip(stringResource(R.string.analytics_registry_warning), Coral)
            else Text(stringResource(R.string.analytics_registry_headroom, stats.headroom), style = MaterialTheme.typography.bodyLarge)
            Muted(stringResource(R.string.analytics_registry_threshold, stats.annVectors, stats.annSearchMs), style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun NumberRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f).padding(end = Space.small))
        Muted(value, style = MaterialTheme.typography.bodyLarge)
    }
}

private fun dayLabel(isoDate: String, locale: java.util.Locale): String =
    runCatching { LocalDate.parse(isoDate).format(DateTimeFormatter.ofPattern("d MMM", locale)) }.getOrDefault(isoDate)

@Composable
private fun BitList(title: String, bits: List<CostBit>, locale: java.util.Locale, label: (String) -> String) {
    Column(verticalArrangement = Arrangement.spacedBy(Space.small)) {
        SectionTitle(title)
        SenaCard(Modifier.fillMaxWidth()) {
            bits.forEach { bit ->
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        label(bit.key), style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f).padding(end = Space.small),
                    )
                    Muted(Fmt.money(bit.usd, locale), style = MaterialTheme.typography.bodyLarge)
                }
            }
        }
    }
}

@Composable
private fun QualitySection(report: QualityReport) {
    Column(verticalArrangement = Arrangement.spacedBy(Space.gap)) {
        SectionTitle(stringResource(R.string.analytics_quality_title))
        SenaCard(Modifier.fillMaxWidth()) {
            Muted(stringResource(R.string.analytics_quality_period, report.days), style = MaterialTheme.typography.labelLarge)
            Text(stringResource(R.string.analytics_tasks, report.tasks), style = MaterialTheme.typography.titleLarge)
            DotLegend(Modifier.fillMaxWidth()) {
                listOf(Verdict.Done, Verdict.DoneWithRemarks, Verdict.Failed, Verdict.Unverified).forEach { v ->
                    val n = report.verdicts[v] ?: 0
                    if (n > 0) DotLegendItem(verdictColor(v, running = false), "${verdictLabel(v, running = false)} $n")
                }
            }
            if (report.ratingAvg != null) Muted(stringResource(R.string.analytics_rating_avg, report.ratingAvg, report.ratingCount), style = MaterialTheme.typography.bodyMedium)
            if (report.errors > 0) Muted(stringResource(R.string.analytics_errors, report.errors), style = MaterialTheme.typography.bodyMedium)
        }
        if (report.repeats.isNotEmpty()) {
            Column(verticalArrangement = Arrangement.spacedBy(Space.small)) {
                SectionTitle(stringResource(R.string.analytics_repeats))
                SenaCard(Modifier.fillMaxWidth()) {
                    report.repeats.forEach { (place, count) ->
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                place, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.weight(1f).padding(end = Space.small),
                            )
                            StatusChip("×$count", Coral)
                        }
                    }
                }
            }
        }
    }
}
