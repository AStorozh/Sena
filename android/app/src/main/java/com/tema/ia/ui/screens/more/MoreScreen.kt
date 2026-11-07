package com.tema.ia.ui.screens.more

import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import com.tema.ia.R
import com.tema.ia.data.PanelInfo
import com.tema.ia.ui.components.IconTile
import com.tema.ia.ui.components.LocalBottomPad
import com.tema.ia.ui.components.RowCard
import com.tema.ia.ui.components.SenaIcon
import com.tema.ia.ui.components.SenaLeaf
import com.tema.ia.ui.components.SectionTitle
import com.tema.ia.ui.components.SenaScreen
import com.tema.ia.ui.components.rise
import com.tema.ia.ui.theme.Coral
import com.tema.ia.ui.theme.Lavender
import com.tema.ia.ui.theme.Lime
import com.tema.ia.ui.theme.Mustard
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Sky
import com.tema.ia.ui.theme.Space

/** The fifth item of the bottom menu: the sections that didn't fit at the bottom, and the panels the server offers (goals, the agents' memory, the exam). */
@Composable
fun MoreScreen(onRules: () -> Unit, onIntegrations: () -> Unit, onAnalytics: () -> Unit, onSettings: () -> Unit, onSecrets: () -> Unit = {},
               panels: List<PanelInfo> = emptyList(), onPanel: (String) -> Unit = {}) {
    val colors = SenaTheme.colors
    SenaScreen(
        title = stringResource(R.string.more_title),
        nav = true,
        leading = { SenaLeaf(size = Size.leafTop, tint = colors.onHeader, description = stringResource(R.string.cd_leaf)) },
    ) {
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = Space.screen).padding(top = Space.screen, bottom = LocalBottomPad.current),
            verticalArrangement = Arrangement.spacedBy(Space.gap),
        ) {
            Entry(R.string.more_rules, R.string.more_rules_hint, R.drawable.ic_rules, Lavender, "more_rules", onRules, Modifier.rise(0, "more-rules"))
            Entry(R.string.more_integrations, R.string.more_integrations_hint, R.drawable.ic_integrations, Lime, "more_integrations", onIntegrations, Modifier.rise(1, "more-int"))
            Entry(R.string.more_analytics, R.string.more_analytics_hint, R.drawable.ic_chart, Mustard, "more_analytics", onAnalytics, Modifier.rise(2, "more-ana"))
            Entry(R.string.more_settings, R.string.more_settings_hint, R.drawable.ic_settings, Sky, "more_settings", onSettings, Modifier.rise(3, "more-set"))
            Entry(R.string.more_secrets, R.string.more_secrets_hint, R.drawable.ic_lock, Coral, "more_secrets", onSecrets, Modifier.rise(4, "more-sec"))
            if (panels.isNotEmpty()) SectionTitle(stringResource(R.string.more_panels), Modifier.rise(5, "more-panels"))
            panels.forEachIndexed { index, panel ->
                val badge = if (panel.badge > 0) pluralStringResource(R.plurals.more_panel_badge, panel.badge, panel.badge) else stringResource(R.string.more_panel_hint)
                RowCard(
                    title = panel.title, subtitle = badge, onClick = { onPanel(panel.id) },
                    modifier = Modifier.fillMaxWidth().testTag("more_panel_" + panel.id).rise(6 + index, "more-panel-" + panel.id),
                    leading = { IconTile(R.drawable.ic_sena_leaf, if (panel.badge > 0) Mustard else Sky, size = Size.tileBig) },
                    trailing = { SenaIcon(R.drawable.ic_chevron_right, null, tint = SenaTheme.colors.textMuted) },
                )
            }
        }
    }
}

@Composable
private fun Entry(@StringRes title: Int, @StringRes hint: Int, @DrawableRes icon: Int, tint: Color, tag: String, onClick: () -> Unit, modifier: Modifier) {
    RowCard(
        title = stringResource(title), subtitle = stringResource(hint), onClick = onClick,
        modifier = modifier.fillMaxWidth().testTag(tag),
        leading = { IconTile(icon, tint, size = Size.tileBig) },
        trailing = { SenaIcon(R.drawable.ic_chevron_right, null, tint = SenaTheme.colors.textMuted) },
    )
}
